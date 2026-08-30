"""Command-line entry point: resolve source, fetch, filter, score, render.

Shelf source resolution order: positional argument, then the
``GOODREADS_TO_READ_RSS_URL`` environment variable, else a clean error. The source
may embed a secret ``key=`` and is never printed, including on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.etree.ElementTree import ParseError

from .criteria import (
    Criteria,
    Selection,
    SeriesProgress,
    all_vocab_tokens,
    build_series_progress,
    expand_genres,
    select,
)
from .fetch import Opener, fetch_shelf
from .model import NormalizedBook
from .render import render

_ENV_USER_ID = "GOODREADS_USER_ID"
_ENV_SOURCE = "GOODREADS_TO_READ_RSS_URL"
_REFERENCE = Path(__file__).resolve().parent.parent / "reference"
_VOCAB_PATH = _REFERENCE / "shelf-vocabulary.json"
_FORMAT_VOCAB_PATH = _REFERENCE / "format-vocabulary.json"


def _load_vocab(path: Path = _VOCAB_PATH) -> dict[str, list[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Degrade to no aliases rather than crash, but say so: silent failure would
        # quietly change results (e.g. --genre sci-fi stops expanding). Name only the
        # file, never a path that could embed anything sensitive.
        print(
            f"warning: could not load {path.name} ({type(exc).__name__}); "
            "alias expansion disabled.",
            file=sys.stderr,
        )
        return {}


def _parse_date(value: str) -> datetime:
    # Date-only cutoffs are evaluated at UTC start-of-day. ``--added-before`` is later
    # widened to end-of-day so same-day additions are included (see _criteria_from_args).
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="next-book",
        description="Search a Goodreads Want-to-Read shelf and rank next-read candidates.",
    )
    p.add_argument(
        "source",
        nargs="?",
        help=(
            f"Goodreads numeric user id or full RSS URL. Falls back to ${_ENV_USER_ID}, "
            f"then ${_ENV_SOURCE}."
        ),
    )
    p.add_argument("--shelf", default="to-read", help="Shelf name (default: to-read).")
    p.add_argument(
        "--genre",
        action="append",
        default=[],
        metavar="NAME",
        help="Genre/shelf to require; repeatable. Matched via shelf vocabulary.",
    )
    p.add_argument(
        "--format",
        action="append",
        default=[],
        metavar="FACET",
        help=(
            "Availability facet from your own shelf tags: digital, physical, audio, "
            "owned, library (repeatable). Books with no format shelf tag surface for "
            "enrichment rather than being dropped."
        ),
    )
    p.add_argument("--author", help="Substring match against author name.")
    p.add_argument(
        "--min-rating", type=float, metavar="R", help="Minimum Goodreads average rating."
    )
    p.add_argument("--max-pages", type=int, metavar="N", help="Maximum page count.")
    p.add_argument("--min-pages", type=int, metavar="N", help="Minimum page count.")
    p.add_argument("--published-before", type=int, metavar="YEAR")
    p.add_argument("--published-after", type=int, metavar="YEAR")
    p.add_argument("--added-before", type=_parse_date, metavar="YYYY-MM-DD")
    p.add_argument("--added-after", type=_parse_date, metavar="YYYY-MM-DD")
    p.add_argument(
        "--added-years-ago", type=float, metavar="N", help="Only books added at least N years ago."
    )
    p.add_argument(
        "--unrated", action="store_true", help="Only books you have not rated (been ignoring)."
    )
    p.add_argument(
        "--prefer",
        choices=("neglected", "recent", "none"),
        default="none",
        help="Weight shelf age toward long-neglected or recently-added books.",
    )
    p.add_argument(
        "--limit", type=int, default=3, metavar="K", help="How many to show (default 3)."
    )
    p.add_argument("--per-page", type=int, default=100, help="RSS page size (default 100).")
    p.add_argument(
        "--series-shelf",
        default="read",
        metavar="NAME",
        help="Shelf to read series progress from for the order check (default: read).",
    )
    p.add_argument(
        "--no-series-check",
        action="store_true",
        help="Skip the read-shelf fetch and the series-order demotion/redirects.",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")
    return p


def _criteria_from_args(
    args: argparse.Namespace,
    vocab: dict[str, list[str]],
    format_vocab: dict[str, list[str]],
    now: datetime,
) -> Criteria:
    added_before = args.added_before
    if added_before is not None:
        # Include the whole calendar day: without this, a book added at 08:00 on the
        # cutoff date would be excluded by the strict start-of-day comparison.
        added_before = added_before + timedelta(days=1) - timedelta(microseconds=1)
    if args.added_years_ago is not None:
        cutoff = now - timedelta(days=args.added_years_ago * 365.25)
        added_before = min(added_before, cutoff) if added_before else cutoff
    return Criteria(
        genre_tokens=expand_genres(args.genre, vocab),
        format_tokens=expand_genres(args.format, format_vocab),
        format_vocab_tokens=all_vocab_tokens(format_vocab),
        author=args.author,
        min_rating=args.min_rating,
        max_pages=args.max_pages,
        min_pages=args.min_pages,
        published_before=args.published_before,
        published_after=args.published_after,
        added_before=added_before,
        added_after=args.added_after,
        unrated=args.unrated,
        prefer=args.prefer,
        limit=args.limit,
    )


def _to_json(selection: Selection) -> str:
    return json.dumps(
        {
            "fetchedCount": selection.fetched_count,
            "filteredCount": selection.filtered_count,
            "shortlist": [b.to_dict() for b in selection.shortlist],
            "needsEnrichment": [b.to_dict() for b in selection.unknown_pages],
            "needsFormatEnrichment": [b.to_dict() for b in selection.unknown_formats],
            "seriesOutOfOrder": sorted(selection.series_out_of_order),
            "seriesRedirects": [
                {
                    "series": r.series,
                    "readMax": r.read_max,
                    "nextOnShelf": r.next_on_shelf.to_dict() if r.next_on_shelf else None,
                }
                for r in selection.series_redirects
            ],
        },
        indent=2,
    )


def _fetch_progress(
    source: str, args: argparse.Namespace, opener_kwargs: dict[str, Opener]
) -> SeriesProgress | None:
    """Read-shelf progress for the series-order check, or ``None`` if it can't be read.

    A read-shelf failure must not sink the whole run — the to-read recommendation still
    stands, just without series-order awareness — so network/parse errors degrade to a
    warning. The source is never echoed (it may embed a secret ``key=``).
    """
    try:
        items = fetch_shelf(
            source,
            shelf=args.series_shelf,
            per_page=args.per_page,
            force_shelf=True,
            **opener_kwargs,
        )
    except (OSError, ParseError):
        print(
            f"warning: could not read the '{args.series_shelf}' shelf; series-order check skipped.",
            file=sys.stderr,
        )
        return None
    return build_series_progress([NormalizedBook.from_item(item) for item in items])


def run(args: argparse.Namespace, now: datetime, opener: Opener | None = None) -> Selection:
    # Resolution order: explicit arg, then the non-sensitive user id, then the full RSS
    # URL (which may embed a private ``key=`` for a private shelf).
    source = args.source or os.environ.get(_ENV_USER_ID) or os.environ.get(_ENV_SOURCE)
    if not source:
        raise ValueError(
            "No shelf source. Pass a Goodreads user id or RSS URL, "
            f"or set ${_ENV_USER_ID} (user id) or ${_ENV_SOURCE} (full URL)."
        )
    kwargs: dict[str, Opener] = {"opener": opener} if opener is not None else {}
    items = fetch_shelf(source, shelf=args.shelf, per_page=args.per_page, **kwargs)
    books = [NormalizedBook.from_item(item) for item in items]
    criteria = _criteria_from_args(args, _load_vocab(), _load_vocab(_FORMAT_VOCAB_PATH), now)
    progress = None if args.no_series_check else _fetch_progress(source, args, kwargs)
    return select(books, criteria, now, progress)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        selection = run(args, datetime.now(UTC))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError:
        # URLError, socket timeouts, and connection resets are all OSError subclasses.
        # Deliberately omit the URL — it may contain a secret key.
        print("error: could not reach the Goodreads RSS feed.", file=sys.stderr)
        return 1
    except ParseError:
        print("error: could not parse the RSS feed response.", file=sys.stderr)
        return 1

    print(_to_json(selection) if args.json else render(selection))
    return 0
