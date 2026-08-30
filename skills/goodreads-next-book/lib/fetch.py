"""Retrieve a full Goodreads shelf via the public RSS endpoint.

The feed caps at 100 items per response and ignores ``order``/``per-page`` tricks,
but honors a ``page`` parameter. Paginating ``page=1..N`` and deduplicating by
``book_id`` recovers the entire shelf (verified: 3 pages -> all 298 Want-to-Read
books for account 63083737). Pagination stops on the first empty page.

The shelf source is either a bare numeric user id or a full RSS URL. A full URL may
carry a private ``key=…`` query param; it is preserved but never logged.
"""

from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from xml.etree.ElementTree import Element

RSS_BASE = "https://www.goodreads.com/review/list_rss/"
USER_AGENT = "Mozilla/5.0 (compatible; goodreads-next-book/1.0)"

Opener = Callable[[str], bytes]


def build_page_url(source: str, page: int, shelf: str = "to-read", per_page: int = 100) -> str:
    """Compose the RSS URL for one page from a numeric id or a full URL."""
    source = source.strip()
    if source.isdigit():
        query = urlencode({"shelf": shelf, "page": page, "per_page": per_page})
        return f"{RSS_BASE}{source}?{query}"

    parts = urlparse(source)
    # Only https reaches urlopen: reject file://, http://, ftp://, etc. This blocks
    # local-file/SSRF reads and stops a secret ``key=`` from crossing a cleartext http
    # link. The scheme is safe to name in the error; the rest of the URL is not.
    if parts.scheme != "https":
        raise ValueError("source URL must use https")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("shelf", shelf)
    query.setdefault("per_page", str(per_page))
    query["page"] = str(page)  # always override; pagination is ours to drive
    return urlunparse(parts._replace(query=urlencode(query)))


def _open_url(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:  # noqa: S310 (https URL only)
        return response.read()


def fetch_shelf(
    source: str,
    shelf: str = "to-read",
    per_page: int = 100,
    max_pages: int = 20,
    opener: Opener = _open_url,
) -> list[Element]:
    """Return every unique ``<item>`` on the shelf, deduplicated by ``book_id``.

    ``opener`` is a seam so tests can serve fixtures without network access.
    """
    seen: dict[str, Element] = {}
    order: list[str] = []
    for page in range(1, max_pages + 1):
        url = build_page_url(source, page, shelf, per_page)
        root = ET.fromstring(opener(url))
        items = root.findall(".//item")
        if not items:
            break
        for item in items:
            book_id = (item.findtext("book_id") or "").strip()
            if book_id and book_id not in seen:
                seen[book_id] = item
                order.append(book_id)
    return [seen[book_id] for book_id in order]
