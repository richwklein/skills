from __future__ import annotations

from datetime import UTC, datetime

NOW = datetime(2026, 8, 29, tzinfo=UTC)


def _mk(model, gid, title, shelves, pages, rating, added_year, my_rating=0):
    return model.NormalizedBook(
        goodreads_id=gid,
        title=title,
        author="Author",
        isbn="",
        pages=pages,
        published=2000,
        description="",
        average_rating=rating,
        date_added=datetime(added_year, 1, 1, tzinfo=UTC),
        my_rating=my_rating,
        shelves=shelves,
        cover_url="",
        goodreads_url="u",
    )


def _shelf(model):
    return [
        _mk(model, "101", "A Short SF", ["science-fiction"], 250, 4.50, 2019),
        _mk(model, "202", "Big Fantasy", ["fantasy"], 900, 4.20, 2026, my_rating=5),
        _mk(model, "303", "YA Space", ["science-fiction", "young-adult"], 280, 3.90, 2020),
        _mk(model, "404", "Learn Code", ["computer-science"], None, 4.00, 2018),
        _mk(model, "505", "Old Mystery", ["mystery"], 320, 4.60, 2015),
        _mk(model, "707", "Classic Tale", ["classic"], 150, 4.10, 2022),
    ]


class TestExpandGenres:
    def test_alias_expands_to_group(self, criteria) -> None:
        tokens = criteria.expand_genres(["sci-fi"], {"science-fiction": ["sci-fi", "sf"]})
        assert "science-fiction" in tokens and "sci-fi" in tokens and "sf" in tokens

    def test_unknown_genre_matches_itself(self, criteria) -> None:
        assert criteria.expand_genres(["cozy"], {}) == {"cozy"}


class TestAllVocabTokens:
    def test_unions_canonicals_and_aliases(self, criteria) -> None:
        vocab = {"digital": ["ebook", "kindle"], "audio": ["audiobook"]}
        assert criteria.all_vocab_tokens(vocab) == {
            "digital",
            "ebook",
            "kindle",
            "audio",
            "audiobook",
        }


class TestFilters:
    def _ids(self, selection):
        return [b.goodreads_id for b in selection.shortlist]

    def test_genre_filter(self, criteria, model) -> None:
        crit = criteria.Criteria(genre_tokens={"science-fiction"}, limit=10)
        sel = criteria.select(_shelf(model), crit, NOW)
        assert set(self._ids(sel)) == {"101", "303"}

    def test_max_pages_flags_unknown_and_excludes_long(self, criteria, model) -> None:
        crit = criteria.Criteria(max_pages=300, limit=10)
        sel = criteria.select(_shelf(model), crit, NOW)
        assert set(self._ids(sel)) == {"101", "303", "707"}  # 250, 280, 150
        assert [b.goodreads_id for b in sel.unknown_pages] == ["404"]  # unknown page count

    def test_unrated_excludes_rated(self, criteria, model) -> None:
        crit = criteria.Criteria(unrated=True, limit=10)
        sel = criteria.select(_shelf(model), crit, NOW)
        assert "202" not in self._ids(sel)  # rated 5

    def test_added_years_ago_via_added_before(self, criteria, model) -> None:
        crit = criteria.Criteria(added_before=datetime(2023, 8, 29, tzinfo=UTC), limit=10)
        sel = criteria.select(_shelf(model), crit, NOW)
        assert "202" not in self._ids(sel)  # added 2026


class TestFormatFilter:
    # Realistic known-format universe (subset of format-vocabulary.json).
    VOCAB = {"digital", "ebook", "kindle", "physical", "paperback", "audiobook", "owned"}

    def _shelf(self, model):
        return [
            _mk(model, "d1", "Kindle Read", ["science-fiction", "kindle"], 200, 4.4, 2019),
            _mk(model, "p1", "Paper Read", ["science-fiction", "paperback"], 210, 4.3, 2019),
            _mk(model, "u1", "Untagged", ["science-fiction"], 220, 4.2, 2019),
        ]

    def test_digital_matches_tagged_and_excludes_physical(self, criteria, model) -> None:
        crit = criteria.Criteria(
            format_tokens={"digital", "ebook", "kindle"},
            format_vocab_tokens=self.VOCAB,
            limit=10,
        )
        sel = criteria.select(self._shelf(model), crit, NOW)
        assert [b.goodreads_id for b in sel.shortlist] == ["d1"]  # kindle matches
        assert [b.goodreads_id for b in sel.unknown_formats] == ["u1"]  # no format tag

    def test_untagged_surfaces_for_enrichment_not_dropped(self, criteria, model) -> None:
        crit = criteria.Criteria(
            format_tokens={"physical", "paperback"},
            format_vocab_tokens=self.VOCAB,
            limit=10,
        )
        sel = criteria.select(self._shelf(model), crit, NOW)
        assert [b.goodreads_id for b in sel.shortlist] == ["p1"]
        # The kindle book is tagged the wrong format -> excluded, not enrichment.
        assert [b.goodreads_id for b in sel.unknown_formats] == ["u1"]

    def test_vocab_tokens_default_to_requested_facet(self, criteria, model) -> None:
        # With no full vocabulary supplied, a book carrying no requested-format tag is
        # treated as unknown (enrichment), never silently matched.
        crit = criteria.Criteria(format_tokens={"kindle"}, limit=10)
        sel = criteria.select(self._shelf(model), crit, NOW)
        assert [b.goodreads_id for b in sel.shortlist] == ["d1"]
        assert {b.goodreads_id for b in sel.unknown_formats} == {"p1", "u1"}


class TestScoringAndShape:
    def test_prefer_neglected_ranks_oldest_first(self, criteria, model) -> None:
        crit = criteria.Criteria(prefer="neglected", limit=10)
        sel = criteria.select(_shelf(model), crit, NOW)
        # Oldest addition (2015) should outrank a same-ish-rated newer book.
        assert sel.shortlist[0].goodreads_id == "505"

    def test_wildcard_breaks_out_of_top_shelf(self, criteria, model) -> None:
        # Three sci-fi outscore the horror book, but the wildcard slot must skip the
        # higher-scoring third sci-fi ("c") in favor of a book off the top shelf ("d").
        books = [
            _mk(model, "a", "SF One", ["science-fiction"], 200, 4.9, 2016),
            _mk(model, "b", "SF Two", ["science-fiction"], 200, 4.8, 2016),
            _mk(model, "c", "SF Three", ["science-fiction"], 200, 4.7, 2016),
            _mk(model, "d", "Horror", ["horror"], 200, 4.6, 2016),
        ]
        crit = criteria.Criteria(prefer="none", limit=3)
        sel = criteria.select(books, crit, NOW)
        assert [b.goodreads_id for b in sel.shortlist] == ["a", "b", "d"]

    def test_counts_reported(self, criteria, model) -> None:
        crit = criteria.Criteria(genre_tokens={"science-fiction"}, limit=1)
        sel = criteria.select(_shelf(model), crit, NOW)
        assert sel.fetched_count == 6
        assert sel.filtered_count == 2
        assert len(sel.shortlist) == 1


def _series_book(model, gid, series, pos, rating=4.30, added_year=2019):
    title = f"Book {pos} ({series}, #{pos})"
    return model.NormalizedBook(
        goodreads_id=gid,
        title=title,
        author="Author",
        isbn="",
        pages=250,
        published=2000,
        description="",
        average_rating=rating,
        date_added=datetime(added_year, 1, 1, tzinfo=UTC),
        my_rating=0,
        shelves=["fantasy"],
        cover_url="",
        goodreads_url="u",
        series=series,
        series_position=float(pos),
    )


class TestSeriesProgress:
    def test_builds_position_sets_per_series(self, criteria, model) -> None:
        read = [
            _series_book(model, "r1", "Swordheart", 1),
            _series_book(model, "r2", "Swordheart", 2),
        ]
        progress = criteria.build_series_progress(read)
        assert progress == {"swordheart": {1.0, 2.0}}

    def test_ignores_standalones(self, criteria, model) -> None:
        standalone = _mk(model, "s1", "The Body", ["horror"], 300, 4.1, 2019)
        assert criteria.build_series_progress([standalone]) == {}


class TestSeriesOrder:
    def _ids(self, selection):
        return [b.goodreads_id for b in selection.shortlist]

    def test_out_of_order_demoted_below_standalone(self, criteria, model) -> None:
        # A high-rated series #3 with nothing read must sort below a lower-rated standalone.
        series3 = _series_book(model, "s3", "Swordheart", 3, rating=4.9)
        standalone = _mk(model, "solo", "Standalone", ["fantasy"], 250, 4.0, 2019)
        crit = criteria.Criteria(limit=10)
        sel = criteria.select([series3, standalone], crit, NOW, progress={})
        assert self._ids(sel) == ["solo", "s3"]  # demoted, but still reachable
        assert sel.series_out_of_order == {"s3"}

    def test_next_in_series_is_in_order(self, criteria, model) -> None:
        # Read #1 -> #2 is the valid next read: not demoted, not flagged.
        book2 = _series_book(model, "b2", "Swordheart", 2)
        progress = criteria.build_series_progress([_series_book(model, "r1", "Swordheart", 1)])
        sel = criteria.select([book2], criteria.Criteria(limit=10), NOW, progress=progress)
        assert sel.series_out_of_order == set()
        assert sel.series_redirects == []

    def test_redirect_points_to_earliest_unread_on_shelf(self, criteria, model) -> None:
        # Read up to #2; shelf holds #3 and #5. #5 is out of order and redirects to #3.
        book3 = _series_book(model, "b3", "Dungeon Crawler Carl", 3)
        book5 = _series_book(model, "b5", "Dungeon Crawler Carl", 5)
        progress = criteria.build_series_progress(
            [_series_book(model, "r2", "Dungeon Crawler Carl", 2)]
        )
        sel = criteria.select([book5, book3], criteria.Criteria(limit=10), NOW, progress=progress)
        assert sel.series_out_of_order == {"b5"}  # #3 is in order, #5 is not
        assert len(sel.series_redirects) == 1
        redirect = sel.series_redirects[0]
        assert redirect.series == "Dungeon Crawler Carl"
        assert redirect.read_max == 2.0
        assert redirect.next_on_shelf.goodreads_id == "b3"

    def test_unstarted_series_has_no_read_max(self, criteria, model) -> None:
        book2 = _series_book(model, "b2", "Curse Bearer", 2)
        sel = criteria.select([book2], criteria.Criteria(limit=10), NOW, progress={})
        assert len(sel.series_redirects) == 1
        assert sel.series_redirects[0].read_max is None
        # No earlier entry on the shelf -> nothing to redirect to.
        assert sel.series_redirects[0].next_on_shelf is None

    def test_sub_one_prequel_is_not_a_redirect_target(self, criteria, model) -> None:
        # Nothing read; the shelf holds only a #0.5 prequel and an out-of-order #3.5.
        # The prequel must not stand in as "start here" -> no redirect target.
        prequel = _series_book(model, "p", "Mistborn", 0.5)
        later = _series_book(model, "l", "Mistborn", 3.5)
        sel = criteria.select([later, prequel], criteria.Criteria(limit=10), NOW, progress={})
        assert len(sel.series_redirects) == 1
        assert sel.series_redirects[0].next_on_shelf is None

    def test_fractional_at_least_one_still_qualifies(self, criteria, model) -> None:
        # A #2.5 novella after the read #2 is a legitimate next read, not a prequel.
        novella = _series_book(model, "n", "Sworn Soldier", 2.5)
        gap_book = _series_book(model, "g", "Sworn Soldier", 5)
        progress = criteria.build_series_progress([_series_book(model, "r2", "Sworn Soldier", 2)])
        sel = criteria.select(
            [gap_book, novella], criteria.Criteria(limit=10), NOW, progress=progress
        )
        assert sel.series_redirects[0].next_on_shelf.goodreads_id == "n"

    def test_no_progress_disables_check(self, criteria, model) -> None:
        # progress=None (the --no-series-check path) never penalizes or flags.
        series3 = _series_book(model, "s3", "Swordheart", 3, rating=4.9)
        sel = criteria.select([series3], criteria.Criteria(limit=10), NOW, progress=None)
        assert sel.series_out_of_order == set()
        assert sel.series_redirects == []
