from __future__ import annotations

EMPTY_FEED = b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'


def _opener(sample: bytes):
    def opener(url: str) -> bytes:
        return sample if "page=1" in url else EMPTY_FEED

    return opener


class TestBuildPageUrl:
    def test_numeric_id(self, fetch) -> None:
        url = fetch.build_page_url("12345678", 1)
        assert "review/list_rss/12345678" in url
        assert "page=1" in url
        assert "per_page=100" in url
        assert "shelf=to-read" in url

    def test_full_url_preserves_key_and_overrides_page(self, fetch) -> None:
        from urllib.parse import parse_qs, urlparse

        source = "https://www.goodreads.com/review/list_rss/9?key=SECRET&shelf=to-read&page=1"
        url = fetch.build_page_url(source, 3)
        query = parse_qs(urlparse(url).query)
        assert query["key"] == ["SECRET"]  # secret param preserved
        assert query["page"] == ["3"]  # our pagination overrides the source's page

    def test_force_shelf_overrides_pinned_shelf(self, fetch) -> None:
        from urllib.parse import parse_qs, urlparse

        source = "https://www.goodreads.com/review/list_rss/9?key=SECRET&shelf=to-read"
        # Without force, a shelf pinned in the URL wins; with force (the read-shelf
        # cross-reference) it is overridden so we never re-read the to-read shelf.
        pinned = parse_qs(urlparse(fetch.build_page_url(source, 1, shelf="read")).query)
        forced = parse_qs(
            urlparse(fetch.build_page_url(source, 1, shelf="read", force_shelf=True)).query
        )
        assert pinned["shelf"] == ["to-read"]
        assert forced["shelf"] == ["read"]
        assert forced["key"] == ["SECRET"]  # secret still preserved

    def test_non_https_source_rejected(self, fetch) -> None:
        import pytest

        for bad in ("file:///etc/passwd", "http://internal/review/list_rss/9?key=SECRET"):
            with pytest.raises(ValueError, match="https"):
                fetch.build_page_url(bad, 1)


class TestDateParsing:
    def test_naive_date_is_normalized_to_utc(self, model) -> None:
        # ``-0000`` means "unknown offset" and parses timezone-naive; it must not stay
        # naive or later comparisons against aware datetimes would raise TypeError.
        parsed = model._date_or_none("Wed, 01 Jan 2020 12:00:00 -0000")
        assert parsed is not None and parsed.tzinfo is not None

    def test_aware_date_offset_preserved(self, model) -> None:
        parsed = model._date_or_none("Wed, 01 Jan 2020 12:00:00 -0800")
        assert parsed is not None and parsed.utcoffset().total_seconds() == -8 * 3600


class TestFetchShelf:
    def test_paginates_dedups_and_stops(self, fetch, sample_bytes) -> None:
        items = fetch.fetch_shelf("123", opener=_opener(sample_bytes))
        ids = [item.findtext("book_id") for item in items]
        # 7 items in the fixture, one duplicate book_id (202) removed, first occurrence kept.
        assert ids == ["101", "202", "303", "404", "505", "707"]

    def test_empty_first_page_returns_nothing(self, fetch) -> None:
        assert fetch.fetch_shelf("123", opener=lambda url: EMPTY_FEED) == []


class TestParseSeries:
    def test_comma_marker(self, model) -> None:
        assert model.parse_series("Daggerbound (Swordheart, #2)") == ("Swordheart", 2.0)

    def test_missing_comma(self, model) -> None:
        assert model.parse_series("When Among Crows (Curse Bearer #1)") == ("Curse Bearer", 1.0)

    def test_fractional_position(self, model) -> None:
        name, pos = model.parse_series("Mistborn: Secret History (Mistborn, #3.5)")
        assert name == "Mistborn" and pos == 3.5

    def test_standalone_has_no_series(self, model) -> None:
        assert model.parse_series("The Body") == (None, None)

    def test_parenthetical_without_position_is_not_a_series(self, model) -> None:
        # An omnibus range carries no single position -> treat as a standalone, not #1.
        assert model.parse_series("The Broken Earth (Omnibus #1-3)") == (None, None)

    def test_series_key_collapses_case_and_whitespace(self, model) -> None:
        assert model.series_key("The Nico di Angelo  Adventures") == "the nico di angelo adventures"


class TestParseItem:
    def _books(self, fetch, model, sample_bytes):
        items = fetch.fetch_shelf("123", opener=_opener(sample_bytes))
        return {b.goodreads_id: b for b in (model.NormalizedBook.from_item(i) for i in items)}

    def test_nested_num_pages_and_fields(self, fetch, model, sample_bytes) -> None:
        books = self._books(fetch, model, sample_bytes)
        short_sf = books["101"]
        assert short_sf.pages == 250
        assert short_sf.published == 2001
        assert short_sf.average_rating == 4.50
        assert short_sf.shelves == ["science-fiction"]  # to-read stripped
        assert short_sf.my_rating == 0
        assert short_sf.date_added.year == 2019

    def test_zero_pages_becomes_unknown(self, fetch, model, sample_bytes) -> None:
        books = self._books(fetch, model, sample_bytes)
        assert books["404"].pages is None  # num_pages 0 -> unknown

    def test_missing_isbn_is_empty(self, fetch, model, sample_bytes) -> None:
        books = self._books(fetch, model, sample_bytes)
        assert books["707"].isbn == ""
