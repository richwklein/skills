from __future__ import annotations

import importlib.util
import base64
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = ROOT / "skills" / "repo-template-audit" / "lib" / "audit.py"


def load_audit_module():
    spec = importlib.util.spec_from_file_location("repo_template_audit", AUDIT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RepoTemplateAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audit = load_audit_module()

    def test_detect_target_accepts_git_worktrees(self) -> None:
        target = Path("/tmp/worktree")
        calls = [
            subprocess.CompletedProcess(
                ["git"], 0, stdout="true\n", stderr="",
            ),
            subprocess.CompletedProcess(
                ["git"], 0, stdout="git@github.com:owner/repo.git\n", stderr="",
            ),
        ]

        with mock.patch.object(self.audit.subprocess, "run", side_effect=calls) as run:
            self.assertEqual(self.audit.detect_target(target), "owner/repo")

        self.assertEqual(run.call_args_list[0].args[0][3:], ["rev-parse", "--is-inside-work-tree"])
        self.assertEqual(run.call_args_list[1].args[0][3:], ["remote", "get-url", "origin"])

    def test_is_github_repo_arg_treats_existing_relative_paths_as_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            local_repo = tmp_path / "some" / "local-repo"
            local_repo.mkdir(parents=True)

            with mock.patch.object(self.audit, "Path", side_effect=lambda p: tmp_path / p):
                self.assertFalse(self.audit.is_github_repo_arg("some/local-repo"))

        self.assertTrue(self.audit.is_github_repo_arg("owner/repo"))
        self.assertFalse(self.audit.is_github_repo_arg("./owner/repo"))
        self.assertFalse(self.audit.is_github_repo_arg("/owner/repo"))

    def test_audit_settings_reports_api_failures_instead_of_no_drift(self) -> None:
        checks = {
            "endpoints": [
                {
                    "path": "repos/{repo}",
                    "section": "general",
                    "fields": ["allow_squash_merge"],
                },
                {
                    "path": "repos/{repo}/rulesets",
                    "section": "rulesets",
                    "compare": "rulesets",
                },
            ],
        }

        def failing_gh_api(path: str, errors: list[str] | None = None):
            if errors is not None:
                errors.append(f"`{path}`: boom")
            return None

        with (
            mock.patch.object(self.audit, "load_settings_checks", return_value=checks),
            mock.patch.object(self.audit, "gh_api", side_effect=failing_gh_api),
        ):
            report = "\n".join(self.audit.audit_settings("template/repo", "target/repo"))

        self.assertIn("### API errors", report)
        self.assertIn("unknown (API error)", report)
        self.assertNotIn("| _no drift_ | | |", report)

    def test_gh_api_collects_subprocess_errors(self) -> None:
        error = subprocess.CalledProcessError(
            1, ["gh", "api", "repos/owner/repo"], stderr="not found",
        )
        errors: list[str] = []

        with mock.patch.object(self.audit.subprocess, "run", side_effect=error):
            self.assertIsNone(self.audit.gh_api("repos/owner/repo", errors=errors))

        self.assertEqual(errors, ["`repos/owner/repo`: not found"])

    def test_gh_api_success_empty_and_json_decode_error(self) -> None:
        with mock.patch.object(
            self.audit.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["gh"], 0, stdout='{"ok": true}', stderr=""),
        ):
            self.assertEqual(self.audit.gh_api("repos/owner/repo"), {"ok": True})

        with mock.patch.object(
            self.audit.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["gh"], 0, stdout="", stderr=""),
        ):
            self.assertIsNone(self.audit.gh_api("repos/owner/repo"))

        errors: list[str] = []
        with mock.patch.object(
            self.audit.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["gh"], 0, stdout="{bad", stderr=""),
        ):
            self.assertIsNone(self.audit.gh_api("repos/owner/repo", errors=errors))

        self.assertIn("could not parse JSON", errors[0])

    def test_fetch_file_decodes_content_and_rejects_missing_or_invalid_content(self) -> None:
        encoded = base64.b64encode(b"hello").decode()

        with mock.patch.object(self.audit, "gh_api", return_value={"content": encoded}):
            self.assertEqual(self.audit.fetch_file("owner/repo", "file.txt"), b"hello")

        with mock.patch.object(self.audit, "gh_api", return_value={}):
            self.assertIsNone(self.audit.fetch_file("owner/repo", "file.txt"))

        with mock.patch.object(self.audit, "gh_api", return_value={"content": object()}):
            self.assertIsNone(self.audit.fetch_file("owner/repo", "file.txt"))

    def test_fetch_tree_returns_blob_paths_and_exits_on_failure(self) -> None:
        tree = {
            "tree": [
                {"path": "file.txt", "type": "blob"},
                {"path": "dir", "type": "tree"},
            ],
        }

        with mock.patch.object(self.audit, "gh_api", return_value=tree):
            self.assertEqual(self.audit.fetch_tree("owner/repo"), ["file.txt"])

        with mock.patch.object(self.audit, "gh_api", return_value=None):
            with self.assertRaises(SystemExit):
                self.audit.fetch_tree("owner/repo")

    def test_detect_target_exits_for_invalid_git_states(self) -> None:
        with mock.patch.object(
            self.audit.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, ["git"]),
        ):
            with self.assertRaises(SystemExit):
                self.audit.detect_target(Path("/tmp/not-git"))

        with mock.patch.object(
            self.audit.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["git"], 0, stdout="false\n", stderr=""),
        ):
            with self.assertRaises(SystemExit):
                self.audit.detect_target(Path("/tmp/not-worktree"))

        calls = [
            subprocess.CompletedProcess(["git"], 0, stdout="true\n", stderr=""),
            subprocess.CompletedProcess(["git"], 0, stdout="https://example.com/repo.git\n", stderr=""),
        ]
        with mock.patch.object(self.audit.subprocess, "run", side_effect=calls):
            with self.assertRaises(SystemExit):
                self.audit.detect_target(Path("/tmp/repo"))

        calls = [
            subprocess.CompletedProcess(["git"], 0, stdout="true\n", stderr=""),
            subprocess.CalledProcessError(1, ["git"]),
        ]
        with mock.patch.object(self.audit.subprocess, "run", side_effect=calls):
            with self.assertRaises(SystemExit):
                self.audit.detect_target(Path("/tmp/repo"))

    def test_file_helpers_format_expected_values(self) -> None:
        self.assertTrue(self.audit.should_ignore("README.md"))
        self.assertTrue(self.audit.should_ignore(".claude/settings.json"))
        self.assertFalse(self.audit.should_ignore("SECURITY.md"))

        diff = self.audit.diff_snippet(b"old\n", b"new\n", "file.txt")
        self.assertIn("--- template/file.txt", diff)
        self.assertIn("+++ local/file.txt", diff)
        self.assertIn("-old", diff)
        self.assertIn("+new", diff)
        self.assertEqual(
            self.audit.diff_snippet(object(), b"new\n", "file.txt"),
            "(binary file; cannot diff)",
        )

        self.assertEqual(self.audit.resolve_nested({"a": {"b": 1}}, "a.b"), 1)
        self.assertEqual(self.audit.resolve_nested({"a": None}, "a.b"), "unknown")
        self.assertEqual(self.audit.fmt(["z", "a"]), "a, z")
        self.assertEqual(self.audit.fmt([]), "[]")
        self.assertEqual(self.audit.fmt(True), "true")
        self.assertEqual(self.audit.fmt(False), "false")
        self.assertEqual(self.audit.fmt(3), "3")

    def test_audit_files_reports_missing_drift_schema_and_fetch_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "same.txt").write_bytes(b"same")
            (target / "drift.txt").write_bytes(b"local")
            (target / "unfetchable.txt").write_bytes(b"local")
            (target / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}}))

            tree = [
                "README.md",
                ".claude/settings.json",
                "CODE_OF_CONDUCT.md",
                "missing.txt",
                "same.txt",
                "drift.txt",
                "unfetchable.txt",
                "package.json",
            ]
            files = {
                "same.txt": b"same",
                "drift.txt": b"template",
                "unfetchable.txt": None,
            }

            with (
                mock.patch.object(self.audit, "fetch_tree", return_value=tree),
                mock.patch.object(self.audit, "fetch_file", side_effect=lambda _repo, path: files[path]),
            ):
                report = "\n".join(self.audit.audit_files(target, "template/repo"))

        self.assertIn("### Missing (2)", report)
        self.assertIn("`CODE_OF_CONDUCT.md` (presence_only)", report)
        self.assertIn("`missing.txt` (exact_match)", report)
        self.assertIn("### Drifted (1)", report)
        self.assertIn("`drift.txt`", report)
        self.assertIn("### Schema gaps", report)
        self.assertIn("missing scripts", report)
        self.assertIn("### Fetch errors", report)
        self.assertIn("`unfetchable.txt`", report)

    def test_audit_files_reports_none_for_clean_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "same.txt").write_bytes(b"same")

            with (
                mock.patch.object(self.audit, "fetch_tree", return_value=["same.txt"]),
                mock.patch.object(self.audit, "fetch_file", return_value=b"same"),
            ):
                report = "\n".join(self.audit.audit_files(target, "template/repo"))

        self.assertIn("### Missing (0)\n\n_None._", report)
        self.assertIn("### Drifted (0)\n\n_None._", report)
        self.assertIn("### Schema gaps\n\n_None._", report)

    def test_check_schemas_handles_missing_parse_error_missing_scripts_and_complete_scripts(self) -> None:
        required = self.audit.SCHEMA_CHECKS[0]["required_scripts"]

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            self.assertEqual(self.audit.check_schemas(target), [])

            (target / "package.json").write_text("{bad")
            self.assertIn("parse error", self.audit.check_schemas(target)[0])

            (target / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}}))
            self.assertIn("missing scripts", self.audit.check_schemas(target)[0])

            (target / "package.json").write_text(
                json.dumps({"scripts": {script: "run" for script in required}})
            )
            self.assertEqual(self.audit.check_schemas(target), [])

    def test_load_settings_checks_reads_reference_file(self) -> None:
        checks = self.audit.load_settings_checks()
        self.assertIn("endpoints", checks)
        self.assertTrue(checks["endpoints"])

    def test_compare_rulesets_reports_no_drift_missing_extra_and_api_errors(self) -> None:
        responses = {
            "repos/template/repo/rulesets": [{"name": "main"}, {"name": "release"}],
            "repos/target/repo/rulesets": [{"name": "main"}, {"name": "extra"}],
        }

        with mock.patch.object(self.audit, "gh_api", side_effect=lambda path, errors=None: responses[path]):
            rows = self.audit.compare_rulesets("template/repo", "target/repo")

        self.assertIn("| ruleset `release` | present | **missing** |", rows)
        self.assertIn("| ruleset `extra` | absent | present (extra) |", rows)

        responses["repos/target/repo/rulesets"] = [{"name": "main"}, {"name": "release"}]
        with mock.patch.object(self.audit, "gh_api", side_effect=lambda path, errors=None: responses[path]):
            self.assertEqual(
                self.audit.compare_rulesets("template/repo", "target/repo"),
                ["| _no drift_ | | |"],
            )

        with mock.patch.object(self.audit, "gh_api", return_value=None):
            self.assertEqual(
                self.audit.compare_rulesets("template/repo", "target/repo"),
                ["| rulesets | unknown | unknown (API error) |"],
            )

    def test_audit_settings_reports_field_list_nested_and_ruleset_drift(self) -> None:
        checks = {
            "endpoints": [
                {
                    "path": "repos/{repo}",
                    "section": "general",
                    "fields": ["allow_squash_merge", "topics"],
                    "nested_fields": {"security_and_analysis": ["secret_scanning.status"]},
                },
                {
                    "path": "repos/{repo}/rulesets",
                    "section": "rulesets",
                    "compare": "rulesets",
                },
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
            "repos/template/repo/rulesets": [{"name": "main"}],
            "repos/target/repo/rulesets": [],
        }

        with (
            mock.patch.object(self.audit, "load_settings_checks", return_value=checks),
            mock.patch.object(self.audit, "gh_api", side_effect=lambda path, errors=None: responses[path]),
        ):
            report = "\n".join(self.audit.audit_settings("template/repo", "target/repo"))

        self.assertIn("| allow_squash_merge | `true` | `false` |", report)
        self.assertNotIn("| topics |", report)
        self.assertIn("| security_and_analysis.secret_scanning.status | `enabled` | `disabled` |", report)
        self.assertIn("| ruleset `main` | present | **missing** |", report)

    def test_audit_settings_reports_no_drift_for_equal_scalar_and_nested_values(self) -> None:
        checks = {
            "endpoints": [
                {
                    "path": "repos/{repo}",
                    "section": "general",
                    "fields": ["allow_squash_merge"],
                    "nested_fields": {"security_and_analysis": ["secret_scanning.status"]},
                },
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
            mock.patch.object(self.audit, "load_settings_checks", return_value=checks),
            mock.patch.object(self.audit, "gh_api", side_effect=lambda path, errors=None: responses[path]),
        ):
            report = "\n".join(self.audit.audit_settings("template/repo", "target/repo"))

        self.assertIn("| _no drift_ | | |", report)

    def test_detect_template_returns_full_name_when_present(self) -> None:
        with mock.patch.object(
            self.audit,
            "gh_api",
            return_value={"template_repository": {"full_name": "template/repo"}},
        ):
            self.assertEqual(self.audit.detect_template("target/repo"), "template/repo")

        with mock.patch.object(self.audit, "gh_api", return_value={}):
            self.assertIsNone(self.audit.detect_template("target/repo"))

    def test_main_with_explicit_template_prints_report(self) -> None:
        stdout = io.StringIO()

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(self.audit.sys, "argv", ["audit.py", "template/repo", tmp]),
            mock.patch.object(self.audit, "detect_target", return_value="target/repo"),
            mock.patch.object(self.audit, "audit_files", return_value=["## File drift"]),
            mock.patch.object(self.audit, "audit_settings", return_value=["## Settings drift"]),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(self.audit.main(), 0)

        output = stdout.getvalue()
        self.assertIn("# Audit report: `target/repo`", output)
        self.assertIn("_Template: `template/repo`_", output)
        self.assertIn("## File drift", output)
        self.assertIn("## Settings drift", output)

    def test_main_auto_detects_template_or_exits_when_missing(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(self.audit.sys, "argv", ["audit.py", tmp]),
            mock.patch.object(self.audit, "detect_target", return_value="target/repo"),
            mock.patch.object(self.audit, "detect_template", return_value="template/repo"),
            mock.patch.object(self.audit, "audit_files", return_value=[]),
            mock.patch.object(self.audit, "audit_settings", return_value=[]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(self.audit.main(), 0)

        self.assertIn("DETECTED_TEMPLATE=template/repo", stderr.getvalue())

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(self.audit.sys, "argv", ["audit.py", tmp]),
            mock.patch.object(self.audit, "detect_target", return_value="target/repo"),
            mock.patch.object(self.audit, "detect_template", return_value=None),
            contextlib.redirect_stderr(io.StringIO()) as missing_stderr,
        ):
            with self.assertRaises(SystemExit):
                self.audit.main()

        self.assertIn("could not detect", missing_stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
