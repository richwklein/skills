from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

NOW = datetime(2026, 8, 29, tzinfo=UTC)


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
