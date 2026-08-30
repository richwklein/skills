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
# Flat penalty for a book that comes later in a series than the reader has reached.
# It exceeds the maximum reachable positive score (rating + age + fit <= 1.0), so an
# out-of-order book always sorts below any in-order/standalone one without being
# removed — a mis-tagged standalone or a thin shelf can still surface it.
_SERIES_ORDER_PENALTY = 1.0

# Series progress: canonical series key -> the set of positions the reader has finished
# (from the read shelf). Empty/omitted means "no read-shelf data" — every book is then
# treated as in order, so the feature degrades cleanly when the check is skipped.
SeriesProgress = dict[str, set[float]]

__all__ = [
    "Criteria",
    "Selection",
    "SeriesProgress",
    "SeriesRedirect",
    "all_vocab_tokens",
    "build_series_progress",
    "expand_genres",
    "normalize_tag",
    "select",
]


def build_series_progress(read_books: list[NormalizedBook]) -> SeriesProgress:
    """Map each series the reader has touched to the set of positions they've finished."""
    progress: SeriesProgress = {}
    for book in read_books:
        if book.series_key is not None and book.series_position is not None:
            progress.setdefault(book.series_key, set()).add(book.series_position)
    return progress


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
class SeriesRedirect:
    """One series the reader can't jump into yet, with the book to read instead.

    ``read_max`` is the furthest position finished on the read shelf (``None`` if the
    series hasn't been started). ``next_on_shelf`` is the earliest still-unread entry
    sitting on the to-read shelf, or ``None`` when the earlier books aren't shelved.
    """

    series: str
    read_max: float | None
    next_on_shelf: NormalizedBook | None


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
    # Series-order awareness (populated only when read-shelf progress is supplied):
    # ids of shortlisted books that come later in a series than the reader has reached,
    # and, per affected series, where to pick the series back up.
    series_out_of_order: set[str] = field(default_factory=set)
    series_redirects: list[SeriesRedirect] = field(default_factory=list)


def _read_max(book: NormalizedBook, progress: SeriesProgress) -> float | None:
    """Furthest finished position in this book's series, or ``None`` if untouched."""
    if book.series_key is None:
        return None
    positions = progress.get(book.series_key)
    return max(positions) if positions else None


def _is_out_of_order(book: NormalizedBook, progress: SeriesProgress) -> bool:
    """True when earlier entries in the book's series are still unread.

    A book at position ``p`` is in order once the reader has finished the entry just
    before it (``read_max >= p - 1``), which also admits novellas like ``#2.5`` after
    ``#2``. Openers and prequels (``p <= 1``) are always in order.
    """
    p = book.series_position
    if book.series_key is None or p is None or p <= 1:
        return False
    read_max = _read_max(book, progress)
    return read_max is None or read_max < p - 1


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


def score_book(
    book: NormalizedBook,
    criteria: Criteria,
    now: datetime,
    progress: SeriesProgress | None = None,
) -> float:
    rating = (book.average_rating or 0.0) / 5.0
    age = _age_component(book, criteria.prefer, now)
    fit = 1.0 if criteria.genre_tokens and _genre_match(book, criteria.genre_tokens) else 0.0
    score = _W_RATING * rating + _W_AGE * age + _W_FIT * fit
    if progress is not None and _is_out_of_order(book, progress):
        score -= _SERIES_ORDER_PENALTY
    return score


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


def _series_redirects(
    books: list[NormalizedBook],
    out_of_order: list[NormalizedBook],
    progress: SeriesProgress,
) -> list[SeriesRedirect]:
    """One redirect per out-of-order series: where to resume it on the to-read shelf.

    The target is the earliest still-unread entry that sits on the shelf after what the
    reader has finished — which, when the shelf holds the very next book, is the correct
    in-order pick rather than the demoted one.
    """
    by_series: dict[str, list[NormalizedBook]] = {}
    for book in books:
        if book.series_key is not None and book.series_position is not None:
            by_series.setdefault(book.series_key, []).append(book)

    redirects: list[SeriesRedirect] = []
    seen: set[str] = set()
    for book in out_of_order:
        key = book.series_key
        if key in seen:
            continue
        seen.add(key)
        read_max = _read_max(book, progress)
        pool = by_series.get(key, [])
        if read_max is not None:
            pool = [b for b in pool if b.series_position > read_max]
        # A useful stepping stone is an in-order entry at a whole-series position (>= 1).
        # Two exclusions: another out-of-order book (e.g. #2 when #1 isn't shelved) helps
        # nobody, and a sub-1.0 prequel (#0.5) is typically written and shelved after the
        # main run, so it isn't a real "start here" either. When neither exists, leave the
        # redirect empty and let the section say the earlier books aren't on the shelf.
        in_order = [
            b for b in pool if b.series_position >= 1.0 and not _is_out_of_order(b, progress)
        ]
        next_on_shelf = min(in_order, key=lambda b: b.series_position) if in_order else None
        redirects.append(
            SeriesRedirect(series=book.series, read_max=read_max, next_on_shelf=next_on_shelf)
        )
    return redirects


def select(
    books: list[NormalizedBook],
    criteria: Criteria,
    now: datetime,
    progress: SeriesProgress | None = None,
) -> Selection:
    # ``None`` means the series-order check is off (no read-shelf data): score, flag, and
    # redirect all no-op. An empty dict means the read shelf WAS read and simply holds
    # nothing for these series, so an unstarted series is genuinely out of order.
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

    scored = sorted(filtered, key=lambda b: score_book(b, criteria, now, progress), reverse=True)
    shortlist = _shape(scored, criteria.limit)
    if progress is None:
        return Selection(
            fetched_count=len(books),
            filtered_count=len(filtered),
            shortlist=shortlist,
            unknown_pages=unknown_pages,
            criteria=criteria,
            unknown_formats=unknown_formats,
        )
    out_of_order = [b for b in filtered if _is_out_of_order(b, progress)]
    return Selection(
        fetched_count=len(books),
        filtered_count=len(filtered),
        shortlist=shortlist,
        unknown_pages=unknown_pages,
        criteria=criteria,
        unknown_formats=unknown_formats,
        series_out_of_order={b.goodreads_id for b in shortlist if _is_out_of_order(b, progress)},
        series_redirects=_series_redirects(books, out_of_order, progress),
    )
