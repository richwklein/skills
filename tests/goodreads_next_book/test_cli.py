from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

NOW = datetime(2026, 8, 29, tzinfo=UTC)


def _item(title: str, book_id: str) -> ET.Element:
    el = ET.Element("item")
    ET.SubElement(el, "title").text = title
    ET.SubElement(el, "book_id").text = book_id
    ET.SubElement(el, "average_rating").text = "4.30"
    return el


class TestAddedBeforeInclusivity:
    def test_added_before_widens_to_end_of_day(self, cli) -> None:
        args = cli._build_parser().parse_args(["123", "--added-before", "2024-01-01"])
        crit = cli._criteria_from_args(args, {}, {}, NOW)
        # A book added anytime on 2024-01-01 must fall on the included side of the cutoff.
        assert crit.added_before == datetime(2024, 1, 1, 23, 59, 59, 999999, tzinfo=UTC)


class TestLoadVocab:
    def test_missing_file_warns_and_returns_empty(self, cli, capsys) -> None:
        result = cli._load_vocab(Path("/nonexistent/does-not-exist.json"))
        assert result == {}
        assert "warning" in capsys.readouterr().err

    def test_valid_file_loads_silently(self, cli, capsys) -> None:
        # The shipped vocabulary loads without any warning noise on a normal run.
        assert cli._load_vocab()  # default path -> shelf-vocabulary.json
        assert capsys.readouterr().err == ""


class TestSourceResolution:
    def _run_capturing_source(self, cli, monkeypatch, argv):
        seen: dict[str, str] = {}

        def fake_fetch(source, **_kwargs):
            seen["source"] = source
            return []  # empty shelf -> select() short-circuits to an empty result

        monkeypatch.setattr(cli, "fetch_shelf", fake_fetch)
        cli.main(argv)
        return seen.get("source")

    def test_arg_beats_both_env_vars(self, cli, monkeypatch) -> None:
        monkeypatch.setenv("GOODREADS_USER_ID", "222")
        monkeypatch.setenv("GOODREADS_TO_READ_RSS_URL", "https://example.com/list_rss/3")
        assert self._run_capturing_source(cli, monkeypatch, ["111"]) == "111"

    def test_user_id_env_beats_url_env(self, cli, monkeypatch) -> None:
        monkeypatch.delenv("GOODREADS_USER_ID", raising=False)
        monkeypatch.setenv("GOODREADS_USER_ID", "222")
        monkeypatch.setenv("GOODREADS_TO_READ_RSS_URL", "https://example.com/list_rss/3")
        assert self._run_capturing_source(cli, monkeypatch, []) == "222"

    def test_url_env_used_when_no_id(self, cli, monkeypatch) -> None:
        monkeypatch.delenv("GOODREADS_USER_ID", raising=False)
        monkeypatch.setenv("GOODREADS_TO_READ_RSS_URL", "https://example.com/list_rss/3")
        assert self._run_capturing_source(cli, monkeypatch, []) == "https://example.com/list_rss/3"

    def test_no_source_is_clean_error(self, cli, monkeypatch, capsys) -> None:
        monkeypatch.delenv("GOODREADS_USER_ID", raising=False)
        monkeypatch.delenv("GOODREADS_TO_READ_RSS_URL", raising=False)
        assert cli.main([]) == 2
        assert "No shelf source" in capsys.readouterr().err


class TestSeriesCheckWiring:
    def test_read_shelf_demotes_out_of_order(self, cli, monkeypatch) -> None:
        to_read = [_item("Sword 3 (Swordheart, #3)", "301"), _item("Standalone", "900")]

        def fake(source, shelf="to-read", **_kw):
            return [] if shelf == "read" else to_read  # nothing read -> #3 out of order

        monkeypatch.setattr(cli, "fetch_shelf", fake)
        sel = cli.run(cli._build_parser().parse_args(["123"]), NOW)
        assert sel.series_out_of_order == {"301"}
        assert sel.shortlist[0].goodreads_id == "900"  # standalone leads the demoted #3

    def test_read_shelf_marks_next_in_series_in_order(self, cli, monkeypatch) -> None:
        def fake(source, shelf="to-read", **_kw):
            if shelf == "read":
                return [_item("Sword 1 (Swordheart, #1)", "100")]
            return [_item("Sword 2 (Swordheart, #2)", "200")]

        monkeypatch.setattr(cli, "fetch_shelf", fake)
        sel = cli.run(cli._build_parser().parse_args(["123"]), NOW)
        assert sel.series_out_of_order == set()  # #2 follows the read #1
        assert sel.series_redirects == []

    def test_no_series_check_skips_read_fetch(self, cli, monkeypatch) -> None:
        shelves: list[str] = []

        def fake(source, shelf="to-read", **_kw):
            shelves.append(shelf)
            return [_item("Sword 3 (Swordheart, #3)", "301")]

        monkeypatch.setattr(cli, "fetch_shelf", fake)
        sel = cli.run(cli._build_parser().parse_args(["123", "--no-series-check"]), NOW)
        assert shelves == ["to-read"]  # the read shelf is never fetched
        assert sel.series_out_of_order == set()

    def test_read_shelf_error_degrades_without_crashing(self, cli, monkeypatch, capsys) -> None:
        def fake(source, shelf="to-read", **_kw):
            if shelf == "read":
                raise TimeoutError("boom")
            return [_item("Sword 3 (Swordheart, #3)", "301")]

        monkeypatch.setattr(cli, "fetch_shelf", fake)
        sel = cli.run(cli._build_parser().parse_args(["123"]), NOW)
        assert "series-order check skipped" in capsys.readouterr().err
        assert sel.series_out_of_order == set()  # recommendation still stands


class TestNetworkErrorHandling:
    def test_timeout_is_caught_as_clean_error(self, cli, monkeypatch, capsys) -> None:
        def boom(*_args, **_kwargs):
            raise TimeoutError("read timed out")

        monkeypatch.setattr(cli, "fetch_shelf", boom)
        code = cli.main(["123"])
        assert code == 1
        err = capsys.readouterr().err
        assert "could not reach" in err
        assert "123" not in err  # source is never echoed
