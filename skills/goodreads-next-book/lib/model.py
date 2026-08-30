"""Normalized book record built from a Goodreads shelf RSS ``<item>``.

The RSS feed exposes most useful metadata as flat children of ``<item>`` but nests
the page count as ``<book id="…"><num_pages>N</num_pages></book>``. A flat scan of
item children misses it, so page count is read via the ``book/num_pages`` path.
The original parsed fields are retained on ``raw`` so no source data is lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import cached_property
from xml.etree.ElementTree import Element


def normalize_tag(value: str) -> str:
    """Canonical shelf/tag form: lowercased, spaces and underscores to hyphens."""
    return value.strip().lower().replace(" ", "-").replace("_", "-")


# Goodreads appends the series and the book's place in it to the title as a trailing
# parenthetical: ``Title (Series Name, #N)``. The comma is sometimes absent
# (``(Curse Bearer #1)``) and the position may be fractional for novellas/prequels
# (``(Mistborn, #3.5)``). ``[^()]`` keeps the match from crossing an unrelated paren.
_SERIES_RE = re.compile(r"\s*\((?P<name>[^()]+?)\s*,?\s*#(?P<pos>\d+(?:\.\d+)?)\)\s*$")


def parse_series(title: str) -> tuple[str | None, float | None]:
    """Split a Goodreads title into its series name and numeric position.

    Returns ``(None, None)`` for a standalone title or any trailing parenthetical
    that carries no ``#N`` position (e.g. an omnibus ``(Series #1-3)``), leaving the
    book to be treated as a standalone rather than mis-ordered.
    """
    match = _SERIES_RE.search(title)
    if not match:
        return None, None
    name = match.group("name").strip()
    if not name:
        return None, None
    return name, float(match.group("pos"))


def series_key(name: str) -> str:
    """Canonical series key for matching the same series across shelves.

    Lowercased with runs of whitespace collapsed, so ``The Nico di Angelo Adventures``
    on the to-read shelf keys the same as on the read shelf.
    """
    return " ".join(name.lower().split())


# Flat ``<item>`` child tags observed in the live feed (verified against a real
# account). ``num_pages`` is intentionally absent here — it is nested under ``book``.
RAW_TAGS = (
    "guid",
    "pubDate",
    "title",
    "link",
    "book_id",
    "book_image_url",
    "book_small_image_url",
    "book_medium_image_url",
    "book_large_image_url",
    "book_description",
    "author_name",
    "isbn",
    "user_name",
    "user_rating",
    "user_read_at",
    "user_date_added",
    "user_date_created",
    "user_shelves",
    "user_review",
    "average_rating",
    "book_published",
)


def _text(el: Element, tag: str) -> str:
    node = el.find(tag)
    if node is not None and node.text and node.text.strip():
        return node.text.strip()
    return ""


def _int_or_none(value: str) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    # Goodreads uses 0 to mean "unknown" for page count / publication year.
    return n if n != 0 else None


def _float_or_none(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_or_none(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    # RFC 2822 dates with a missing/``-0000`` offset parse as timezone-naive; normalize to
    # UTC so later comparisons against timezone-aware ``now``/cutoffs never raise TypeError.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _shelves(value: str) -> list[str]:
    tags = [part.strip() for part in value.split(",") if part.strip()]
    return [t for t in tags if t != "to-read"]


@dataclass
class NormalizedBook:
    """Stable internal model. Camel-cased keys in ``to_dict`` mirror the handoff record."""

    goodreads_id: str
    title: str
    author: str
    isbn: str
    pages: int | None
    published: int | None
    description: str
    average_rating: float | None
    date_added: datetime | None
    my_rating: int
    shelves: list[str]
    cover_url: str
    goodreads_url: str
    # Series name and this book's position within it, parsed from the title's trailing
    # ``(Series, #N)`` marker. ``None`` for standalones.
    series: str | None = None
    series_position: float | None = None
    raw: dict[str, str] = field(default_factory=dict, repr=False)

    @cached_property
    def shelf_tokens(self) -> frozenset[str]:
        """Normalized shelf tokens, computed once — reused across filtering/scoring/shaping."""
        return frozenset(normalize_tag(shelf) for shelf in self.shelves)

    @cached_property
    def series_key(self) -> str | None:
        """Canonical key for cross-shelf series matching, or ``None`` for a standalone."""
        return series_key(self.series) if self.series else None

    @classmethod
    def from_item(cls, el: Element) -> NormalizedBook:
        raw = {tag: _text(el, tag) for tag in RAW_TAGS}
        pages = _int_or_none(_text(el, "book/num_pages"))
        series, series_position = parse_series(raw["title"])
        return cls(
            goodreads_id=raw["book_id"],
            title=raw["title"],
            author=raw["author_name"],
            isbn=raw["isbn"],
            pages=pages,
            published=_int_or_none(raw["book_published"]),
            description=raw["book_description"],
            average_rating=_float_or_none(raw["average_rating"]),
            date_added=_date_or_none(raw["user_date_added"]),
            my_rating=_int_or_none(raw["user_rating"]) or 0,
            shelves=_shelves(raw["user_shelves"]),
            cover_url=raw["book_image_url"],
            goodreads_url=raw["link"],
            series=series,
            series_position=series_position,
            raw=raw,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "goodreadsId": self.goodreads_id,
            "title": self.title,
            "author": self.author,
            "isbn": self.isbn,
            "pages": self.pages,
            "published": self.published,
            "averageRating": self.average_rating,
            "dateAdded": self.date_added.isoformat() if self.date_added else None,
            "myRating": self.my_rating,
            "shelves": self.shelves,
            "series": self.series,
            "seriesPosition": self.series_position,
            "coverUrl": self.cover_url,
            "goodreadsUrl": self.goodreads_url,
        }
