"""Render a selection as well-formed Markdown for Claude to present.

The shelf source (which may embed a secret ``key=``) is never passed to this module,
so it cannot leak into the report.
"""

from __future__ import annotations

from .criteria import Selection
from .model import NormalizedBook

_ROLES = ("Best match", "Strong alternative", "Wildcard")


def _role(index: int) -> str:
    return _ROLES[index] if index < len(_ROLES) else f"Option {index + 1}"


def _line(book: NormalizedBook) -> list[str]:
    rating = f"{book.average_rating:.2f}" if book.average_rating is not None else "n/a"
    pages = f"{book.pages}p" if book.pages is not None else "pages unknown"
    added = book.date_added.date().isoformat() if book.date_added else "date unknown"
    shelves = ", ".join(book.shelves) if book.shelves else "—"
    parts = [
        f"- avg rating {rating} · {pages} · added {added}",
        f"- shelves: {shelves}",
    ]
    if book.goodreads_url:
        parts.append(f"- {book.goodreads_url}")
    return parts


def render(selection: Selection) -> str:
    c = selection.criteria
    lines: list[str] = ["# Goodreads next-book candidates", ""]
    lines.append(
        f"Fetched {selection.fetched_count} from the shelf; "
        f"{selection.filtered_count} matched the criteria; "
        f"showing {len(selection.shortlist)}."
    )
    if c.prefer != "none":
        lines.append(f"Preference: **{c.prefer}** (shelf age weighted).")
    lines.append("")

    if selection.shortlist:
        lines.append("## Shortlist")
        lines.append("")
        for i, book in enumerate(selection.shortlist):
            lines.append(f"### {i + 1}. {book.title} — {book.author}  _({_role(i)})_")
            lines.extend(_line(book))
            lines.append("")
    else:
        lines.append(
            "_No shelf book matched. Consider relaxing a filter or looking beyond the shelf._"
        )
        lines.append("")

    if selection.unknown_pages:
        lines.append("## Needs enrichment (page count unknown)")
        lines.append("")
        lines.append(
            "A page filter was set, but these otherwise-matching books have no page "
            "count in the feed. Look them up (Open Library by ISBN, then web) before "
            "excluding them:"
        )
        lines.append("")
        for book in selection.unknown_pages:
            isbn = f" · ISBN {book.isbn}" if book.isbn else ""
            lines.append(f"- {book.title} — {book.author}{isbn}")
        lines.append("")

    if selection.unknown_formats:
        lines.append("## Needs enrichment (format/availability unknown)")
        lines.append("")
        lines.append(
            "A format filter was set, but these otherwise-matching books carry no "
            "format shelf tag, so the feed cannot say whether they are available "
            "digitally or in print. Look them up (Open Library by ISBN, then web) to "
            "confirm a digital vs physical edition exists, and label the result as "
            "enrichment — an edition existing is not proof you can access it:"
        )
        lines.append("")
        for book in selection.unknown_formats:
            isbn = f" · ISBN {book.isbn}" if book.isbn else ""
            lines.append(f"- {book.title} — {book.author}{isbn}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
