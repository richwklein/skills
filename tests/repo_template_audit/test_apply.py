from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
from pathlib import Path
from unittest import mock


class TestGhWrite:
    def test_success_with_body(self, apply_mod) -> None:
        with mock.patch.object(
            apply_mod.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["gh"], 0, stdout=b'{"ok": true}', stderr=b""),
        ) as run:
            assert apply_mod.gh_write("PATCH", "repos/target/repo", {"x": 1}) == {"ok": True}

        assert run.call_args.args[0] == [
            "gh",
            "api",
            "--method",
            "PATCH",
            "repos/target/repo",
            "--input",
            "-",
        ]
        assert run.call_args.kwargs["input"] == b'{"x": 1}'

    def test_success_empty_body(self, apply_mod) -> None:
        with mock.patch.object(
            apply_mod.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["gh"], 0, stdout=b"", stderr=b""),
        ):
            assert apply_mod.gh_write("DELETE", "repos/target/repo") == {}

    def test_cli_error(self, apply_mod) -> None:
        stderr = io.StringIO()
        error = subprocess.CalledProcessError(1, ["gh"], stderr=b"denied")
        with (
            mock.patch.object(apply_mod.subprocess, "run", side_effect=error),
            contextlib.redirect_stderr(stderr),
        ):
            assert apply_mod.gh_write("PATCH", "repos/target/repo", {"x": 1}) is None

        assert "ERROR (PATCH repos/target/repo): denied" in stderr.getvalue()


class TestBuildNested:
    def test_builds_dotpath_tree(self, apply_mod) -> None:
        assert apply_mod._build_nested({"a.b": 1, "a.c.d": 2, "e": 3}) == {
            "a": {"b": 1, "c": {"d": 2}},
            "e": 3,
        }


class TestApplySettings:
    def test_skips_when_reads_fail(self, apply_mod) -> None:
        checks = {
            "endpoints": [
                {
                    "path": "repos/{repo}",
                    "fields": ["allow_squash_merge"],
                    "nested_fields": {"security_and_analysis": ["secret_scanning.status"]},
                }
            ],
        }

        def failing_gh_api(path: str, errors: list[str] | None = None):
            if errors is not None:
                errors.append(f"`{path}`: boom")
            return None

        with (
            mock.patch.object(apply_mod, "load_settings_checks", return_value=checks),
            mock.patch.object(apply_mod, "gh_api", side_effect=failing_gh_api),
            mock.patch.object(apply_mod, "gh_write") as gh_write,
        ):
            result = apply_mod.apply_settings("template/repo", "target/repo")

        gh_write.assert_not_called()
        assert any(
            not a.success and "skipped (API read failed)" in a.description for a in result.actions
        )
        assert result.errors

    def test_skips_unknown_and_noop_values(self, apply_mod) -> None:
        checks = {
            "endpoints": [
                {
                    "path": "repos/{repo}",
                    "fields": ["missing", "topics", "allow_squash_merge"],
                    "nested_fields": {"security_and_analysis": ["secret_scanning.status"]},
                }
            ],
        }
        responses = {
            "repos/template/repo": {
                "topics": ["a", "b"],
                "allow_squash_merge": True,
                "security_and_analysis": {},
            },
            "repos/target/repo": {
                "topics": "not-a-list",
                "allow_squash_merge": True,
                "security_and_analysis": {},
            },
        }

        with (
            mock.patch.object(apply_mod, "load_settings_checks", return_value=checks),
            mock.patch.object(
                apply_mod, "gh_api", side_effect=lambda path, errors=None: responses[path]
            ),
            mock.patch.object(apply_mod, "gh_write", return_value={}) as gh_write,
        ):
            result = apply_mod.apply_settings("template/repo", "target/repo")

        gh_write.assert_called_once_with("PATCH", "repos/target/repo", {"topics": ["a", "b"]})
        assert len(result.actions) == 1
        assert result.actions[0].success
        assert "`repos/{repo}`" in result.actions[0].description
        assert "`topics`" in result.actions[0].description

    def test_returns_empty_when_no_drift(self, apply_mod) -> None:
        checks = {
            "endpoints": [
                {
                    "path": "repos/{repo}",
                    "fields": ["allow_squash_merge"],
                    "nested_fields": {"security_and_analysis": ["secret_scanning.status"]},
                }
            ],
        }
        responses = {
            "repos/template/repo": {
                "allow_squash_merge": True,
                "security_and_analysis": {"secret_scanning": {"status": "enabled"}},
            },
            "repos/target/repo": {
                "allow_squash_merge": True,
                "security_and_analysis": {"secret_scanning": {"status": "enabled"}},
            },
        }

        with (
            mock.patch.object(apply_mod, "load_settings_checks", return_value=checks),
            mock.patch.object(
                apply_mod, "gh_api", side_effect=lambda path, errors=None: responses[path]
            ),
            mock.patch.object(apply_mod, "gh_write") as gh_write,
        ):
            result = apply_mod.apply_settings("template/repo", "target/repo")

        gh_write.assert_not_called()
        assert result.actions == []
        assert result.errors == []

    def test_writes_changed_fields_and_nested_values(self, apply_mod) -> None:
        checks = {
            "endpoints": [
                {
                    "path": "repos/{repo}",
                    "fields": ["allow_squash_merge", "topics"],
                    "nested_fields": {"security_and_analysis": ["secret_scanning.status"]},
                },
                {"path": "repos/{repo}/private-vulnerability-reporting", "fields": ["enabled"]},
                {"path": "repos/{repo}/rulesets", "compare": "rulesets"},
            ],
        }
        responses = {
            "repos/template/repo": {
                "allow_squash_merge": True,
                "topics": ["b", "a"],
                "security_and_analysis": {"secret_scanning": {"status": "enabled"}},
            },
            "repos/target/repo": {
                "allow_squash_merge": False,
                "topics": ["a", "b"],
                "security_and_analysis": {"secret_scanning": {"status": "disabled"}},
            },
            "repos/template/repo/private-vulnerability-reporting": {"enabled": False},
            "repos/target/repo/private-vulnerability-reporting": {"enabled": True},
        }

        with (
            mock.patch.object(apply_mod, "load_settings_checks", return_value=checks),
            mock.patch.object(
                apply_mod, "gh_api", side_effect=lambda path, errors=None: responses[path]
            ),
            mock.patch.object(apply_mod, "gh_write", return_value={}) as gh_write,
        ):
            result = apply_mod.apply_settings("template/repo", "target/repo")

        gh_write.assert_any_call(
            "PATCH",
            "repos/target/repo",
            {
                "allow_squash_merge": True,
                "security_and_analysis": {"secret_scanning": {"status": "enabled"}},
            },
        )
        gh_write.assert_any_call("DELETE", "repos/target/repo/private-vulnerability-reporting")
        descriptions = [a.description for a in result.actions]
        assert any("allow_squash_merge" in d and "security_and_analysis" in d for d in descriptions)


class TestApplyRulesets:
    def test_skips_when_list_reads_fail(self, apply_mod) -> None:
        def failing_gh_api(path: str, errors: list[str] | None = None):
            if errors is not None:
                errors.append(f"`{path}`: boom")
            return None

        with (
            mock.patch.object(apply_mod, "gh_api", side_effect=failing_gh_api),
            mock.patch.object(apply_mod, "gh_write") as gh_write,
        ):
            result = apply_mod.apply_rulesets("template/repo", "target/repo")

        gh_write.assert_not_called()
        assert any(
            not a.success and "skipped (API read failed)" in a.description for a in result.actions
        )
        assert result.errors

    def test_reports_malformed_and_fetch_failures(self, apply_mod) -> None:
        responses = {
            "repos/template/repo/rulesets": [{"name": "missing-id"}, {"name": "main", "id": 1}],
            "repos/target/repo/rulesets": [],
            "repos/template/repo/rulesets/1": None,
        }

        with (
            mock.patch.object(
                apply_mod, "gh_api", side_effect=lambda path, errors=None: responses[path]
            ),
            mock.patch.object(apply_mod, "gh_write") as gh_write,
        ):
            result = apply_mod.apply_rulesets("template/repo", "target/repo")

        gh_write.assert_not_called()
        descriptions = " ".join(a.description for a in result.actions)
        assert "skipped malformed template summary" in descriptions
        assert "could not fetch from template" in descriptions

    def test_creates_missing_ruleset_payload(self, apply_mod) -> None:
        responses = {
            "repos/template/repo/rulesets": [{"name": "main", "id": 1}],
            "repos/target/repo/rulesets": [],
            "repos/template/repo/rulesets/1": {
                "name": "main",
                "target": "branch",
                "enforcement": "active",
                "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
                "rules": [{"type": "deletion"}],
                "bypass_actors": [],
                "ignored": True,
            },
        }

        with (
            mock.patch.object(
                apply_mod, "gh_api", side_effect=lambda path, errors=None: responses[path]
            ),
            mock.patch.object(apply_mod, "gh_write", return_value={}) as gh_write,
        ):
            result = apply_mod.apply_rulesets("template/repo", "target/repo")

        gh_write.assert_called_once_with(
            "POST",
            "repos/target/repo/rulesets",
            {
                "name": "main",
                "target": "branch",
                "enforcement": "active",
                "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
                "rules": [{"type": "deletion"}],
                "bypass_actors": [],
            },
        )
        assert len(result.actions) == 1
        assert result.actions[0].success
        assert "ruleset `main`: created" in result.actions[0].description

    def test_skips_non_dict_and_existing_rulesets(self, apply_mod) -> None:
        responses = {
            "repos/template/repo/rulesets": ["bad", {"name": "main", "id": 1}],
            "repos/target/repo/rulesets": [{"name": "main"}],
        }

        with (
            mock.patch.object(
                apply_mod, "gh_api", side_effect=lambda path, errors=None: responses[path]
            ),
            mock.patch.object(apply_mod, "gh_write") as gh_write,
        ):
            result = apply_mod.apply_rulesets("template/repo", "target/repo")

        gh_write.assert_not_called()
        assert result.actions == []


class TestApplyFiles:
    def test_returns_structured_result(self, apply_mod) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "drift.txt").write_bytes(b"local")
            files = {
                "new/nested.txt": b"template",
                "drift.txt": b"template",
                "missing-content.txt": None,
            }

            def fake_provenance(_target, path):
                if path == "removed.txt":
                    return ("deleted_locally", "abc1234 remove removed.txt")
                return ("new_in_template", None)

            with (
                mock.patch.object(
                    apply_mod,
                    "fetch_tree",
                    return_value=[
                        "README.md",
                        "package.json",
                        "new/nested.txt",
                        "drift.txt",
                        "missing-content.txt",
                        "removed.txt",
                    ],
                ),
                mock.patch.object(
                    apply_mod, "fetch_file", side_effect=lambda _repo, path: files[path]
                ),
                mock.patch.object(
                    apply_mod, "missing_file_provenance", side_effect=fake_provenance
                ),
                mock.patch.object(apply_mod, "behind_template_ref", return_value=None),
            ):
                result = apply_mod.apply_files(target, "template/repo")

            assert result.synced == ["new/nested.txt"]
            assert (target / "new" / "nested.txt").read_bytes() == b"template"
            assert result.drifted[0].path == "drift.txt"
            assert result.drifted[0].behind_ref is None
            assert len(result.skipped_deleted) == 1
            assert result.skipped_deleted[0].path == "removed.txt"
            assert result.skipped_deleted[0].evidence == "abc1234 remove removed.txt"
            assert not (target / "removed.txt").exists()
            assert result.fetch_errors == ["missing-content.txt"]

    def test_marks_behind_template_drift(self, apply_mod) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "drift.txt").write_bytes(b"old template")

            with (
                mock.patch.object(apply_mod, "fetch_tree", return_value=["drift.txt"]),
                mock.patch.object(apply_mod, "fetch_file", return_value=b"new template"),
                mock.patch.object(
                    apply_mod, "behind_template_ref", return_value="def5678"
                ) as behind,
            ):
                result = apply_mod.apply_files(target, "template/repo")

            behind.assert_called_once_with("template/repo", "drift.txt", b"old template")
            assert result.drifted[0].behind_ref == "def5678"


class TestApplyMain:
    def test_auto_detected_template_exits_before_mutation(self, audit, apply_mod) -> None:
        stderr = io.StringIO()
        args = audit.ParsedArgs(
            template_repo="template/repo",
            target=Path("/tmp"),
            target_repo="target/repo",
            detected=True,
        )

        with (
            mock.patch.object(apply_mod, "parse_args", return_value=args),
            mock.patch.object(apply_mod, "apply_settings") as settings,
            mock.patch.object(apply_mod, "apply_rulesets") as rulesets,
            mock.patch.object(apply_mod, "apply_files") as files,
            contextlib.redirect_stderr(stderr),
        ):
            assert apply_mod.main() == 2

        settings.assert_not_called()
        rulesets.assert_not_called()
        files.assert_not_called()
        assert "must be confirmed" in stderr.getvalue()

    def test_returns_error_when_parse_args_fails(self, audit, apply_mod) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.object(apply_mod, "parse_args", side_effect=apply_mod.AuditError("boom")),
            contextlib.redirect_stderr(stderr),
        ):
            assert apply_mod.main() == 1
        assert "boom" in stderr.getvalue()

    def test_explicit_template_runs_apply_steps(self, audit, apply_mod) -> None:
        stdout = io.StringIO()
        args = audit.ParsedArgs(
            template_repo="template/repo",
            target=Path("/tmp"),
            target_repo="target/repo",
            detected=False,
        )
        settings = apply_mod.ApplySettingsResult(
            actions=[apply_mod.ApplyAction(True, "settings fixed")],
        )
        rulesets = apply_mod.ApplyRulesetsResult(
            actions=[apply_mod.ApplyAction(True, "ruleset created")],
        )
        files = apply_mod.ApplyFilesResult(
            synced=["new.txt"],
            fetch_errors=["fetch.txt"],
        )

        with (
            mock.patch.object(apply_mod, "parse_args", return_value=args),
            mock.patch.object(apply_mod, "apply_settings", return_value=settings),
            mock.patch.object(apply_mod, "apply_rulesets", return_value=rulesets),
            mock.patch.object(apply_mod, "apply_files", return_value=files),
            contextlib.redirect_stdout(stdout),
        ):
            assert apply_mod.main() == 0

        output = stdout.getvalue()
        assert "# Apply report: `target/repo`" in output
        assert "✓ settings fixed" in output
        assert "✓ ruleset created" in output
        assert "`new.txt`" in output
        assert "fetch.txt" in output
