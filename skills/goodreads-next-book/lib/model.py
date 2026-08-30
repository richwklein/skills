"""Normalized book record built from a Goodreads shelf RSS ``<item>``.

The RSS feed exposes most useful metadata as flat children of ``<item>`` but nests
the page count as ``<book id="…"><num_pages>N</num_pages></book>``. A flat scan of
item children misses it, so page count is read via the ``book/num_pages`` path.
The original parsed fields are retained on ``raw`` so no source data is lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import cached_property
from xml.etree.ElementTree import Element


def normalize_tag(value: str) -> str:
    """Canonical shelf/tag form: lowercased, spaces and underscores to hyphens."""
    return value.strip().lower().replace(" ", "-").replace("_", "-")


# Flat ``<item>`` child tags observed in the live feed (verified against account
# 63083737). ``num_pages`` is intentionally absent here — it is nested under ``book``.
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
    raw: dict[str, str] = field(default_factory=dict, repr=False)

    @cached_property
    def shelf_tokens(self) -> frozenset[str]:
        """Normalized shelf tokens, computed once — reused across filtering/scoring/shaping."""
        return frozenset(normalize_tag(shelf) for shelf in self.shelves)

    @classmethod
    def from_item(cls, el: Element) -> NormalizedBook:
        raw = {tag: _text(el, tag) for tag in RAW_TAGS}
        pages = _int_or_none(_text(el, "book/num_pages"))
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
            "coverUrl": self.cover_url,
            "goodreadsUrl": self.goodreads_url,
        }
