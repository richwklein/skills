from __future__ import annotations

import importlib.util
import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
APPLY_PATH = ROOT / "skills" / "repo-template-audit" / "lib" / "apply.py"


def load_apply_module():
    spec = importlib.util.spec_from_file_location("repo_template_apply", APPLY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RepoTemplateApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.apply = load_apply_module()

    def test_gh_write_handles_success_empty_body_and_cli_errors(self) -> None:
        with mock.patch.object(
            self.apply.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["gh"], 0, stdout=b'{"ok": true}', stderr=b""),
        ) as run:
            self.assertEqual(self.apply.gh_write("PATCH", "repos/target/repo", {"x": 1}), {"ok": True})

        self.assertEqual(run.call_args.args[0], ["gh", "api", "--method", "PATCH", "repos/target/repo", "--input", "-"])
        self.assertEqual(run.call_args.kwargs["input"], b'{"x": 1}')

        with mock.patch.object(
            self.apply.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["gh"], 0, stdout=b"", stderr=b""),
        ):
            self.assertEqual(self.apply.gh_write("DELETE", "repos/target/repo"), {})

        stderr = io.StringIO()
        error = subprocess.CalledProcessError(1, ["gh"], stderr=b"denied")
        with (
            mock.patch.object(self.apply.subprocess, "run", side_effect=error),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertIsNone(self.apply.gh_write("PATCH", "repos/target/repo", {"x": 1}))

        self.assertIn("ERROR (PATCH repos/target/repo): denied", stderr.getvalue())

    def test_build_nested_builds_dotpath_tree(self) -> None:
        self.assertEqual(
            self.apply._build_nested({"a.b": 1, "a.c.d": 2, "e": 3}),
            {"a": {"b": 1, "c": {"d": 2}}, "e": 3},
        )

    def test_apply_settings_skips_endpoint_and_does_not_write_when_reads_fail(self) -> None:
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
            mock.patch.object(self.apply, "load_settings_checks", return_value=checks),
            mock.patch.object(self.apply, "gh_api", side_effect=failing_gh_api),
            mock.patch.object(self.apply, "gh_write") as gh_write,
        ):
            lines = self.apply.apply_settings("template/repo", "target/repo")

        gh_write.assert_not_called()
        report = "\n".join(lines)
        self.assertIn("skipped (API read failed)", report)
        self.assertIn("API read", report)

    def test_apply_settings_skips_unknown_and_noop_values(self) -> None:
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
            mock.patch.object(self.apply, "load_settings_checks", return_value=checks),
            mock.patch.object(self.apply, "gh_api", side_effect=lambda path, errors=None: responses[path]),
            mock.patch.object(self.apply, "gh_write", return_value={}) as gh_write,
        ):
            lines = self.apply.apply_settings("template/repo", "target/repo")

        gh_write.assert_called_once_with("PATCH", "repos/target/repo", {"topics": ["a", "b"]})
        self.assertEqual(lines, ["- ✓ `repos/{repo}`: `topics`"])

    def test_apply_settings_returns_empty_when_there_is_no_drift(self) -> None:
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
            mock.patch.object(self.apply, "load_settings_checks", return_value=checks),
            mock.patch.object(self.apply, "gh_api", side_effect=lambda path, errors=None: responses[path]),
            mock.patch.object(self.apply, "gh_write") as gh_write,
        ):
            self.assertEqual(self.apply.apply_settings("template/repo", "target/repo"), [])

        gh_write.assert_not_called()

    def test_apply_settings_writes_changed_fields_and_nested_values(self) -> None:
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
            mock.patch.object(self.apply, "load_settings_checks", return_value=checks),
            mock.patch.object(self.apply, "gh_api", side_effect=lambda path, errors=None: responses[path]),
            mock.patch.object(self.apply, "gh_write", return_value={}) as gh_write,
        ):
            lines = self.apply.apply_settings("template/repo", "target/repo")

        gh_write.assert_any_call(
            "PATCH",
            "repos/target/repo",
            {
                "allow_squash_merge": True,
                "security_and_analysis": {"secret_scanning": {"status": "enabled"}},
            },
        )
        gh_write.assert_any_call("DELETE", "repos/target/repo/private-vulnerability-reporting")
        self.assertIn("`repos/{repo}`: `allow_squash_merge, security_and_analysis`", "\n".join(lines))

    def test_apply_rulesets_skips_when_list_reads_fail_and_does_not_write(self) -> None:
        def failing_gh_api(path: str, errors: list[str] | None = None):
            if errors is not None:
                errors.append(f"`{path}`: boom")
            return None

        with (
            mock.patch.object(self.apply, "gh_api", side_effect=failing_gh_api),
            mock.patch.object(self.apply, "gh_write") as gh_write,
        ):
            lines = self.apply.apply_rulesets("template/repo", "target/repo")

        gh_write.assert_not_called()
        self.assertIn("- ✗ rulesets: skipped (API read failed)", lines)
        self.assertTrue(any("API read" in line for line in lines))

    def test_apply_rulesets_reports_malformed_and_fetch_failures(self) -> None:
        responses = {
            "repos/template/repo/rulesets": [
                {"name": "missing-id"},
                {"name": "main", "id": 1},
            ],
            "repos/target/repo/rulesets": [],
            "repos/template/repo/rulesets/1": None,
        }

        with (
            mock.patch.object(self.apply, "gh_api", side_effect=lambda path, errors=None: responses[path]),
            mock.patch.object(self.apply, "gh_write") as gh_write,
        ):
            lines = self.apply.apply_rulesets("template/repo", "target/repo")

        gh_write.assert_not_called()
        report = "\n".join(lines)
        self.assertIn("skipped malformed template summary", report)
        self.assertIn("could not fetch from template", report)

    def test_apply_rulesets_creates_missing_ruleset_payload(self) -> None:
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
            mock.patch.object(self.apply, "gh_api", side_effect=lambda path, errors=None: responses[path]),
            mock.patch.object(self.apply, "gh_write", return_value={}) as gh_write,
        ):
            lines = self.apply.apply_rulesets("template/repo", "target/repo")

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
        self.assertEqual(lines, ["- ✓ ruleset `main`: created"])

    def test_apply_rulesets_skips_non_dict_and_existing_rulesets(self) -> None:
        responses = {
            "repos/template/repo/rulesets": ["bad", {"name": "main", "id": 1}],
            "repos/target/repo/rulesets": [{"name": "main"}],
        }

        with (
            mock.patch.object(self.apply, "gh_api", side_effect=lambda path, errors=None: responses[path]),
            mock.patch.object(self.apply, "gh_write") as gh_write,
        ):
            self.assertEqual(self.apply.apply_rulesets("template/repo", "target/repo"), [])

        gh_write.assert_not_called()

    def test_apply_files_returns_applied_drifted_and_fetch_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "drift.txt").write_bytes(b"local")
            files = {
                "new/nested.txt": b"template",
                "drift.txt": b"template",
                "missing-content.txt": None,
            }

            with (
                mock.patch.object(
                    self.apply,
                    "fetch_tree",
                    return_value=["README.md", "package.json", "new/nested.txt", "drift.txt", "missing-content.txt"],
                ),
                mock.patch.object(self.apply, "fetch_file", side_effect=lambda _repo, path: files[path]),
            ):
                applied, drifted, fetch_errors = self.apply.apply_files(target, "template/repo")

            self.assertEqual(applied, ["new/nested.txt"])
            self.assertEqual((target / "new" / "nested.txt").read_bytes(), b"template")
            self.assertEqual(drifted[0][0], "drift.txt")
            self.assertEqual(fetch_errors, ["missing-content.txt"])

    def test_print_report_includes_fetch_errors(self) -> None:
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            self.apply.print_report(
                "target/repo",
                "template/repo",
                [],
                [],
                ["new.txt"],
                [("drift.txt", "--- a\n+++ b")],
                ["missing-content.txt"],
            )

        output = stdout.getvalue()
        self.assertIn("### Fetch errors (1)", output)
        self.assertIn("`missing-content.txt`", output)
        self.assertIn("### Needs review (1)", output)

    def test_print_report_handles_empty_sections(self) -> None:
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            self.apply.print_report("target/repo", "template/repo", [], [], [], [])

        output = stdout.getvalue()
        self.assertIn("_No settings drift", output)
        self.assertIn("### Synced (0)", output)
        self.assertIn("### Needs review (0)", output)
        self.assertIn("_None._", output)

    def test_main_auto_detected_template_exits_before_mutation(self) -> None:
        stderr = io.StringIO()

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(self.apply.sys, "argv", ["apply.py", tmp]),
            mock.patch.object(self.apply, "detect_target", return_value="target/repo"),
            mock.patch.object(self.apply, "detect_template", return_value="template/repo"),
            mock.patch.object(self.apply, "apply_settings") as apply_settings,
            mock.patch.object(self.apply, "apply_rulesets") as apply_rulesets,
            mock.patch.object(self.apply, "apply_files") as apply_files,
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(self.apply.main(), 2)

        apply_settings.assert_not_called()
        apply_rulesets.assert_not_called()
        apply_files.assert_not_called()
        self.assertIn("DETECTED_TEMPLATE=template/repo", stderr.getvalue())
        self.assertIn("must be confirmed", stderr.getvalue())

    def test_main_without_template_exits_when_detection_fails(self) -> None:
        stderr = io.StringIO()

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(self.apply.sys, "argv", ["apply.py", tmp]),
            mock.patch.object(self.apply, "detect_target", return_value="target/repo"),
            mock.patch.object(self.apply, "detect_template", return_value=None),
            contextlib.redirect_stderr(stderr),
        ):
            with self.assertRaises(SystemExit):
                self.apply.main()

        self.assertIn("could not detect", stderr.getvalue())

    def test_main_with_explicit_template_runs_apply_steps(self) -> None:
        stdout = io.StringIO()

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(self.apply.sys, "argv", ["apply.py", "template/repo", tmp]),
            mock.patch.object(self.apply, "detect_target", return_value="target/repo"),
            mock.patch.object(self.apply, "apply_settings", return_value=["settings"]),
            mock.patch.object(self.apply, "apply_rulesets", return_value=["rulesets"]),
            mock.patch.object(self.apply, "apply_files", return_value=(["new.txt"], [], ["fetch.txt"])),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(self.apply.main(), 0)

        output = stdout.getvalue()
        self.assertIn("# Apply report: `target/repo`", output)
        self.assertIn("settings", output)
        self.assertIn("rulesets", output)
        self.assertIn("fetch.txt", output)


if __name__ == "__main__":
    unittest.main()
