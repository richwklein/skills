"""Deterministic hard-filtering and soft-scoring over normalized books.

The agent translates a natural request ("light sci-fi under 300 pages I've been
ignoring") into structured flags; this module never parses free text. It applies
hard filters, scores survivors with shelf age as a first-class signal, and shapes a
best / alternative / wildcard shortlist. Genuinely semantic criteria (tone, themes,
series status) are left to the agent to resolve on the returned shortlist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .model import NormalizedBook, normalize_tag

# Scoring weights. Documented here because they define ranking behavior.
_W_RATING = 0.5
_W_AGE = 0.3
_W_FIT = 0.2
_AGE_SATURATION_YEARS = 5.0

__all__ = ["Criteria", "Selection", "all_vocab_tokens", "expand_genres", "normalize_tag", "select"]


def expand_genres(requested: list[str], vocab: dict[str, list[str]]) -> set[str]:
    """Return the normalized shelf tokens that satisfy the requested genres.

    ``vocab`` maps a canonical shelf name to its aliases. A requested genre matching
    a canonical name or any alias expands to that whole group; anything else matches
    only itself.
    """
    lookup: dict[str, set[str]] = {}
    for canonical, aliases in vocab.items():
        group = {normalize_tag(canonical), *(normalize_tag(a) for a in aliases)}
        for member in group:
            lookup[member] = group

    tokens: set[str] = set()
    for genre in requested:
        norm = normalize_tag(genre)
        tokens |= lookup.get(norm, {norm})
    return tokens


def all_vocab_tokens(vocab: dict[str, list[str]]) -> set[str]:
    """Every normalized token in a vocabulary — each canonical name plus its aliases.

    Used as the "known universe" for a facet so a book can be classified as tagged
    (has a token from this vocabulary) versus untagged (no format signal at all).
    """
    return expand_genres(list(vocab), vocab)


@dataclass
class Criteria:
    genre_tokens: set[str] = field(default_factory=set)
    author: str | None = None
    min_rating: float | None = None
    max_pages: int | None = None
    min_pages: int | None = None
    published_before: int | None = None
    published_after: int | None = None
    added_before: datetime | None = None
    added_after: datetime | None = None
    unrated: bool = False
    # Format/availability facet ("what I shelved"): the requested facet's shelf tokens
    # (e.g. digital -> {ebook, kindle, …}), plus the union of all known format tokens so
    # a book can be told apart as tagged-but-wrong-format versus not tagged at all.
    format_tokens: set[str] = field(default_factory=set)
    format_vocab_tokens: set[str] = field(default_factory=set)
    prefer: str = "none"  # "neglected" | "recent" | "none"
    limit: int = 3


@dataclass
class Selection:
    fetched_count: int
    filtered_count: int
    shortlist: list[NormalizedBook]
    unknown_pages: list[NormalizedBook]
    criteria: Criteria
    # Books that clear every other filter but carry no shelf tag for the requested
    # format facet — the feed cannot answer digital-vs-physical, so they are surfaced
    # for Open Library enrichment rather than silently dropped.
    unknown_formats: list[NormalizedBook] = field(default_factory=list)


def _book_tokens(book: NormalizedBook) -> frozenset[str]:
    return book.shelf_tokens  # cached on the model; computed once per book


def _genre_match(book: NormalizedBook, tokens: set[str]) -> bool:
    return bool(_book_tokens(book) & tokens)


def _passes(book: NormalizedBook, criteria: Criteria) -> tuple[bool, bool, bool]:
    """Return (passed, page_unknown, format_unknown).

    ``page_unknown``/``format_unknown`` flag a book that clears every other filter but
    cannot answer an active page or format filter (missing page count, or no shelf tag
    for the requested format facet). Such books are surfaced for enrichment, not dropped.
    """
    if criteria.genre_tokens and not _genre_match(book, criteria.genre_tokens):
        return False, False, False
    if criteria.author and criteria.author.lower() not in book.author.lower():
        return False, False, False
    if criteria.min_rating is not None and (book.average_rating or 0.0) < criteria.min_rating:
        return False, False, False
    if criteria.published_after is not None:
        if book.published is None or book.published < criteria.published_after:
            return False, False, False
    if criteria.published_before is not None:
        if book.published is None or book.published > criteria.published_before:
            return False, False, False
    if criteria.added_after is not None:
        if book.date_added is None or book.date_added < criteria.added_after:
            return False, False, False
    if criteria.added_before is not None:
        if book.date_added is None or book.date_added > criteria.added_before:
            return False, False, False
    if criteria.unrated and book.my_rating != 0:
        return False, False, False

    if criteria.format_tokens:
        # "what I shelved": the feed has no edition format, so the only deterministic
        # signal is the user's own shelf tags. Known universe defaults to the requested
        # facet when the full vocabulary was not supplied (e.g. hand-built Criteria).
        known_formats = criteria.format_vocab_tokens or criteria.format_tokens
        book_formats = _book_tokens(book) & known_formats
        if not book_formats:
            return False, False, True  # no format signal -> enrich (Open Library)
        if not (book_formats & criteria.format_tokens):
            return False, False, False  # tagged, but not the requested format

    page_filter = criteria.max_pages is not None or criteria.min_pages is not None
    if page_filter and book.pages is None:
        return False, True, False
    if (
        criteria.max_pages is not None
        and book.pages is not None
        and book.pages > criteria.max_pages
    ):
        return False, False, False
    if (
        criteria.min_pages is not None
        and book.pages is not None
        and book.pages < criteria.min_pages
    ):
        return False, False, False
    return True, False, False


def _age_component(book: NormalizedBook, prefer: str, now: datetime) -> float:
    if prefer == "none" or book.date_added is None:
        return 0.5  # neutral: constant across books, so it does not affect ranking
    age_years = (now - book.date_added).days / 365.25
    saturated = min(max(age_years, 0.0) / _AGE_SATURATION_YEARS, 1.0)
    return saturated if prefer == "neglected" else 1.0 - saturated


def score_book(book: NormalizedBook, criteria: Criteria, now: datetime) -> float:
    rating = (book.average_rating or 0.0) / 5.0
    age = _age_component(book, criteria.prefer, now)
    fit = 1.0 if criteria.genre_tokens and _genre_match(book, criteria.genre_tokens) else 0.0
    return _W_RATING * rating + _W_AGE * age + _W_FIT * fit


def _shape(scored: list[NormalizedBook], limit: int) -> list[NormalizedBook]:
    """Best / strong alternative / wildcard. The wildcard is the highest-scoring book
    that shares no shelf with the top pick, so the trio spans more than one cluster."""
    if limit <= 0 or not scored:
        return scored[: max(limit, 0)]
    if limit < 3 or len(scored) <= limit:
        return scored[:limit]

    picks = scored[: limit - 1]
    chosen = {b.goodreads_id for b in picks}
    top_shelves = _book_tokens(scored[0])
    wildcard = next(
        (
            b
            for b in scored[limit - 1 :]
            if b.goodreads_id not in chosen and not (_book_tokens(b) & top_shelves)
        ),
        scored[limit - 1],
    )
    return [*picks, wildcard]


def select(books: list[NormalizedBook], criteria: Criteria, now: datetime) -> Selection:
    filtered: list[NormalizedBook] = []
    unknown_pages: list[NormalizedBook] = []
    unknown_formats: list[NormalizedBook] = []
    for book in books:
        passed, page_unknown, format_unknown = _passes(book, criteria)
        if page_unknown:
            unknown_pages.append(book)
        if format_unknown:
            unknown_formats.append(book)
        if passed:
            filtered.append(book)

    scored = sorted(filtered, key=lambda b: score_book(b, criteria, now), reverse=True)
    return Selection(
        fetched_count=len(books),
        filtered_count=len(filtered),
        shortlist=_shape(scored, criteria.limit),
        unknown_pages=unknown_pages,
        criteria=criteria,
        unknown_formats=unknown_formats,
    )
