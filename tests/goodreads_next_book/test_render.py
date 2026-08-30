from __future__ import annotations

from datetime import UTC, datetime


def _book(model, gid, title, pages, shelves):
    return model.NormalizedBook(
        goodreads_id=gid,
        title=title,
        author="Author",
        isbn="9999",
        pages=pages,
        published=2000,
        description="",
        average_rating=4.3,
        date_added=datetime(2019, 1, 1, tzinfo=UTC),
        my_rating=0,
        shelves=shelves,
        cover_url="",
        goodreads_url="https://www.goodreads.com/book/show/1",
    )


def _selection(criteria, model, shortlist, unknown):
    return criteria.Selection(
        fetched_count=298,
        filtered_count=len(shortlist),
        shortlist=shortlist,
        unknown_pages=unknown,
        criteria=criteria.Criteria(max_pages=300, prefer="neglected"),
    )


class TestRender:
    def test_shortlist_and_coverage(self, render, criteria, model) -> None:
        books = [_book(model, "1", "First Book", 200, ["science-fiction"])]
        out = render.render(_selection(criteria, model, books, []))
        assert "# Goodreads next-book candidates" in out
        assert "Fetched 298" in out
        assert "First Book" in out
        assert "Best match" in out
        assert "**neglected**" in out

    def test_needs_enrichment_section(self, render, criteria, model) -> None:
        shortlist = [_book(model, "1", "First Book", 200, ["science-fiction"])]
        unknown = [_book(model, "2", "No Page Count", None, ["fantasy"])]
        out = render.render(_selection(criteria, model, shortlist, unknown))
        assert "Needs enrichment" in out
        assert "No Page Count" in out

    def test_needs_format_enrichment_section(self, render, criteria, model) -> None:
        shortlist = [_book(model, "1", "First Book", 200, ["science-fiction", "kindle"])]
        selection = criteria.Selection(
            fetched_count=298,
            filtered_count=1,
            shortlist=shortlist,
            unknown_pages=[],
            criteria=criteria.Criteria(format_tokens={"digital"}),
            unknown_formats=[_book(model, "2", "Untagged Format", 180, ["science-fiction"])],
        )
        out = render.render(selection)
        assert "format/availability unknown" in out
        assert "Untagged Format" in out
        assert "Open Library" in out

    def test_empty_shortlist_message(self, render, criteria, model) -> None:
        out = render.render(_selection(criteria, model, [], []))
        assert "No shelf book matched" in out

    def test_output_is_newline_terminated(self, render, criteria, model) -> None:
        books = [_book(model, "1", "First Book", 200, ["science-fiction"])]
        out = render.render(_selection(criteria, model, books, []))
        assert out.endswith("\n")
