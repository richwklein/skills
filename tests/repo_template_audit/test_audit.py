from __future__ import annotations

import base64
import contextlib
import io
import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# ---- Package structure ------------------------------------------------------


class TestPackageStructure:
    def test_lib_directory_is_a_package(self, lib_dir) -> None:
        init_path = lib_dir / "__init__.py"
        assert init_path.is_file(), f"Expected {init_path} to exist"

    def test_apply_does_not_use_sys_path_insert(self, apply_path) -> None:
        source = apply_path.read_text()
        assert "sys.path.insert" not in source


# ---- Error handling ----------------------------------------------------------


class TestAuditError:
    def test_is_defined_as_exception(self, audit) -> None:
        assert hasattr(audit, "AuditError")
        assert issubclass(audit.AuditError, Exception)


# ---- gh CLI helpers ----------------------------------------------------------


class TestGhApi:
    def test_collects_subprocess_errors(self, audit) -> None:
        error = subprocess.CalledProcessError(
            1,
            ["gh", "api", "repos/owner/repo"],
            stderr="not found",
        )
        errors: list[str] = []

        with mock.patch.object(audit.subprocess, "run", side_effect=error):
            assert audit.gh_api("repos/owner/repo", errors=errors) is None

        assert errors == ["`repos/owner/repo`: not found"]

    def test_success_empty_and_json_decode_error(self, audit) -> None:
        with mock.patch.object(
            audit.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["gh"], 0, stdout='{"ok": true}', stderr=""),
        ):
            assert audit.gh_api("repos/owner/repo") == {"ok": True}

        with mock.patch.object(
            audit.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["gh"], 0, stdout="", stderr=""),
        ):
            assert audit.gh_api("repos/owner/repo") is None

        errors: list[str] = []
        with mock.patch.object(
            audit.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["gh"], 0, stdout="{bad", stderr=""),
        ):
            assert audit.gh_api("repos/owner/repo", errors=errors) is None

        assert "could not parse JSON" in errors[0]


class TestFetchFile:
    def test_decodes_content(self, audit) -> None:
        encoded = base64.b64encode(b"hello").decode()
        with mock.patch.object(audit, "gh_api", return_value={"content": encoded}):
            assert audit.fetch_file("owner/repo", "file.txt") == b"hello"

    def test_passes_ref(self, audit) -> None:
        encoded = base64.b64encode(b"hello").decode()
        with mock.patch.object(audit, "gh_api", return_value={"content": encoded}) as api:
            assert audit.fetch_file("owner/repo", "file.txt", ref="abc123") == b"hello"
        api.assert_called_once_with("repos/owner/repo/contents/file.txt?ref=abc123")

    def test_rejects_missing_content(self, audit) -> None:
        with mock.patch.object(audit, "gh_api", return_value={}):
            assert audit.fetch_file("owner/repo", "file.txt") is None

    def test_rejects_invalid_content(self, audit) -> None:
        with mock.patch.object(audit, "gh_api", return_value={"content": object()}):
            assert audit.fetch_file("owner/repo", "file.txt") is None


class TestFetchTree:
    def test_returns_blob_paths(self, audit) -> None:
        tree = {
            "tree": [
                {"path": "file.txt", "type": "blob"},
                {"path": "dir", "type": "tree"},
            ],
        }
        with mock.patch.object(audit, "gh_api", return_value=tree):
            assert audit.fetch_tree("owner/repo") == ["file.txt"]

    def test_raises_audit_error_on_failure(self, audit) -> None:
        with mock.patch.object(audit, "gh_api", return_value=None):
            with pytest.raises(audit.AuditError):
                audit.fetch_tree("owner/repo")


# ---- Target detection --------------------------------------------------------


class TestDetectTarget:
    def test_accepts_git_worktrees(self, audit) -> None:
        target = Path("/tmp/worktree")
        calls = [
            subprocess.CompletedProcess(["git"], 0, stdout="true\n", stderr=""),
            subprocess.CompletedProcess(
                ["git"], 0, stdout="git@github.com:owner/repo.git\n", stderr=""
            ),
        ]

        with mock.patch.object(audit.subprocess, "run", side_effect=calls) as run:
            assert audit.detect_target(target) == "owner/repo"

        assert run.call_args_list[0].args[0][3:] == ["rev-parse", "--is-inside-work-tree"]
        assert run.call_args_list[1].args[0][3:] == ["remote", "get-url", "origin"]

    def test_raises_when_not_a_git_repo(self, audit) -> None:
        calls = [
            subprocess.CalledProcessError(1, ["git"]),
            subprocess.CompletedProcess(["git"], 0, stdout="false\n", stderr=""),
        ]
        with mock.patch.object(audit.subprocess, "run", side_effect=calls):
            with pytest.raises(audit.AuditError):
                audit.detect_target(Path("/tmp/not-git"))

    def test_raises_when_not_inside_worktree(self, audit) -> None:
        with mock.patch.object(
            audit.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["git"], 0, stdout="false\n", stderr=""),
        ):
            with pytest.raises(audit.AuditError):
                audit.detect_target(Path("/tmp/not-worktree"))

    def test_raises_with_helpful_message_for_bare_repo(self, audit) -> None:
        calls = [
            subprocess.CompletedProcess(["git"], 0, stdout="false\n", stderr=""),
            subprocess.CompletedProcess(["git"], 0, stdout="true\n", stderr=""),
        ]
        with mock.patch.object(audit.subprocess, "run", side_effect=calls):
            with pytest.raises(audit.AuditError, match="bare repo"):
                audit.detect_target(Path("/tmp/bare-repo"))

    def test_raises_when_remote_url_not_github(self, audit) -> None:
        calls = [
            subprocess.CompletedProcess(["git"], 0, stdout="true\n", stderr=""),
            subprocess.CompletedProcess(
                ["git"], 0, stdout="https://example.com/repo.git\n", stderr=""
            ),
        ]
        with mock.patch.object(audit.subprocess, "run", side_effect=calls):
            with pytest.raises(audit.AuditError):
                audit.detect_target(Path("/tmp/repo"))

    def test_raises_when_no_remote(self, audit) -> None:
        calls = [
            subprocess.CompletedProcess(["git"], 0, stdout="true\n", stderr=""),
            subprocess.CalledProcessError(1, ["git"]),
        ]
        with mock.patch.object(audit.subprocess, "run", side_effect=calls):
            with pytest.raises(audit.AuditError):
                audit.detect_target(Path("/tmp/repo"))


class TestIsGithubRepoArg:
    def test_treats_existing_relative_paths_as_paths(self, audit) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            local_repo = tmp_path / "some" / "local-repo"
            local_repo.mkdir(parents=True)

            with mock.patch.object(audit, "Path", side_effect=lambda p: tmp_path / p):
                assert not audit.is_github_repo_arg("some/local-repo")

        assert audit.is_github_repo_arg("owner/repo")
        assert not audit.is_github_repo_arg("./owner/repo")
        assert not audit.is_github_repo_arg("/owner/repo")


class TestDetectTemplate:
    def test_returns_full_name_when_present(self, audit) -> None:
        with mock.patch.object(
            audit,
            "gh_api",
            return_value={"template_repository": {"full_name": "template/repo"}},
        ):
            assert audit.detect_template("target/repo") == "template/repo"

    def test_returns_none_when_absent(self, audit) -> None:
        with mock.patch.object(audit, "gh_api", return_value={}):
            assert audit.detect_template("target/repo") is None


# ---- Shared helpers ----------------------------------------------------------


class TestValuesMatch:
    def test_equal_scalars(self, audit) -> None:
        assert audit.values_match(True, True)
        assert audit.values_match("foo", "foo")
        assert audit.values_match(42, 42)

    def test_different_scalars(self, audit) -> None:
        assert not audit.values_match(True, False)
        assert not audit.values_match("foo", "bar")

    def test_lists_normalized(self, audit) -> None:
        assert audit.values_match(["b", "a"], ["a", "b"])
        assert not audit.values_match(["a", "b"], ["a", "c"])

    def test_list_vs_nonlist(self, audit) -> None:
        assert not audit.values_match(["a"], "a")

    def test_empty_lists(self, audit) -> None:
        assert audit.values_match([], [])


class TestFileConfig:
    def test_load_file_checks_returns_expected_keys(self, audit) -> None:
        config = audit.load_file_checks()
        assert "ignore" in config
        assert "ignore_prefixes" in config
        assert "presence_only" in config
        assert "schema_checks" in config

    def test_file_checks_json_exists(self, root_dir) -> None:
        ref_path = root_dir / "skills" / "repo-template-audit" / "reference" / "file-checks.json"
        assert ref_path.is_file(), f"Expected config at {ref_path}"

    def test_file_config_from_dict(self, audit) -> None:
        data = {
            "ignore": [".git"],
            "ignore_prefixes": [".git/"],
            "presence_only": ["package.json"],
            "schema_checks": [],
        }
        config = audit.FileConfig.from_dict(data)
        assert ".git" in config.ignore
        assert config.ignore_prefixes == (".git/",)
        assert "package.json" in config.presence_only
        assert config.schema_checks == []

    def test_should_ignore_with_explicit_config(self, audit) -> None:
        config = audit.FileConfig(
            ignore={"custom.md"},
            ignore_prefixes=("custom/",),
            presence_only=set(),
            schema_checks=[],
        )
        assert audit.should_ignore("custom.md", config)
        assert audit.should_ignore("custom/foo.txt", config)
        assert not audit.should_ignore("README.md", config)


class TestFileHelpers:
    def test_should_ignore(self, audit) -> None:
        assert audit.should_ignore("README.md")
        assert audit.should_ignore(".claude/settings.json")
        assert not audit.should_ignore("SECURITY.md")

    def test_diff_snippet_reads_local_to_template(self, audit) -> None:
        diff = audit.diff_snippet(b"template\n", b"local\n", "file.txt")
        assert "--- local/file.txt" in diff
        assert "+++ template/file.txt" in diff
        assert "-local" in diff
        assert "+template" in diff

    def test_diff_snippet_binary(self, audit) -> None:
        assert audit.diff_snippet(object(), b"new\n", "file.txt") == "(binary file; cannot diff)"

    def test_resolve_nested(self, audit) -> None:
        assert audit.resolve_nested({"a": {"b": 1}}, "a.b") == 1
        assert audit.resolve_nested({"a": None}, "a.b") == "unknown"


class TestMissingFileProvenance:
    def test_reports_local_deletion_commit(self, audit) -> None:
        with mock.patch.object(
            audit.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                ["git"], 0, stdout="abc1234 remove file\n", stderr=""
            ),
        ) as run:
            provenance = audit.missing_file_provenance(Path("/tmp/repo"), "file.txt")

        assert provenance == ("deleted_locally", "abc1234 remove file")
        args = run.call_args.args[0]
        assert "--diff-filter=D" in args
        assert args[-1] == "file.txt"

    def test_new_in_template_when_never_deleted(self, audit) -> None:
        with mock.patch.object(
            audit.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["git"], 0, stdout="\n", stderr=""),
        ):
            assert audit.missing_file_provenance(Path("/tmp/repo"), "file.txt") == (
                "new_in_template",
                None,
            )

    def test_new_in_template_when_git_fails(self, audit) -> None:
        with mock.patch.object(
            audit.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(128, ["git"]),
        ):
            assert audit.missing_file_provenance(Path("/tmp/repo"), "file.txt") == (
                "new_in_template",
                None,
            )


class TestBehindTemplateRef:
    def test_returns_matching_older_commit(self, audit) -> None:
        commits = [{"sha": "headsha000000"}, {"sha": "abc1234def567"}]
        with (
            mock.patch.object(audit, "gh_api", return_value=commits),
            mock.patch.object(audit, "fetch_file", return_value=b"local") as fetch,
        ):
            assert audit.behind_template_ref("t/repo", "file.txt", b"local") == "abc1234"
        fetch.assert_called_once_with("t/repo", "file.txt", ref="abc1234def567")

    def test_returns_none_when_no_blob_matches(self, audit) -> None:
        commits = [{"sha": "headsha000000"}, {"sha": "oldsha1234567"}]
        with (
            mock.patch.object(audit, "gh_api", return_value=commits),
            mock.patch.object(audit, "fetch_file", return_value=b"other"),
        ):
            assert audit.behind_template_ref("t/repo", "file.txt", b"local") is None

    def test_returns_none_on_api_error(self, audit) -> None:
        with mock.patch.object(audit, "gh_api", return_value=None):
            assert audit.behind_template_ref("t/repo", "file.txt", b"local") is None

    def test_skips_head_and_malformed_entries(self, audit) -> None:
        commits = [{"sha": "headsha000000"}, "bad", {}, {"sha": "match12345678"}]
        with (
            mock.patch.object(audit, "gh_api", return_value=commits),
            mock.patch.object(audit, "fetch_file", return_value=b"local") as fetch,
        ):
            assert audit.behind_template_ref("t/repo", "file.txt", b"local") == "match12"
        fetch.assert_called_once_with("t/repo", "file.txt", ref="match12345678")


# ---- File drift --------------------------------------------------------------


class TestAuditFiles:
    def test_returns_structured_result(self, audit) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "same.txt").write_bytes(b"same")
            (target / "drift.txt").write_bytes(b"local")
            (target / "behind.txt").write_bytes(b"old template")
            (target / "unfetchable.txt").write_bytes(b"local")
            (target / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}}))

            tree = [
                "README.md",
                ".claude/settings.json",
                "CODE_OF_CONDUCT.md",
                "missing.txt",
                "same.txt",
                "drift.txt",
                "behind.txt",
                "unfetchable.txt",
                "package.json",
            ]
            files = {
                "same.txt": b"same",
                "drift.txt": b"template",
                "behind.txt": b"new template",
                "unfetchable.txt": None,
            }

            def fake_provenance(_target, path):
                if path == "missing.txt":
                    return ("deleted_locally", "abc1234 remove missing.txt")
                return ("new_in_template", None)

            def fake_behind(_repo, path, _local):
                return "def5678" if path == "behind.txt" else None

            with (
                mock.patch.object(audit, "fetch_tree", return_value=tree),
                mock.patch.object(audit, "fetch_file", side_effect=lambda _repo, path: files[path]),
                mock.patch.object(audit, "missing_file_provenance", side_effect=fake_provenance),
                mock.patch.object(audit, "behind_template_ref", side_effect=fake_behind),
            ):
                result = audit.audit_files(target, "template/repo")

        missing = {m.path: m for m in result.missing}
        assert len(missing) == 2
        assert missing["CODE_OF_CONDUCT.md"].kind == "presence_only"
        assert missing["CODE_OF_CONDUCT.md"].provenance == "new_in_template"
        assert missing["CODE_OF_CONDUCT.md"].evidence is None
        assert missing["missing.txt"].kind == "exact_match"
        assert missing["missing.txt"].provenance == "deleted_locally"
        assert missing["missing.txt"].evidence == "abc1234 remove missing.txt"
        drifted = {d.path: d for d in result.drifted}
        assert len(drifted) == 2
        assert drifted["drift.txt"].behind_ref is None
        assert drifted["behind.txt"].behind_ref == "def5678"
        assert len(result.schema_gaps) == 1
        assert result.schema_gaps[0].path == "package.json"
        assert "missing scripts" in result.schema_gaps[0].message
        assert result.fetch_errors == ["unfetchable.txt"]

    def test_returns_empty_for_clean_repo(self, audit) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "same.txt").write_bytes(b"same")

            with (
                mock.patch.object(audit, "fetch_tree", return_value=["same.txt"]),
                mock.patch.object(audit, "fetch_file", return_value=b"same"),
            ):
                result = audit.audit_files(target, "template/repo")

        assert result.missing == []
        assert result.drifted == []
        assert result.schema_gaps == []
        assert result.fetch_errors == []


class TestCheckSchemas:
    def test_handles_all_cases(self, audit) -> None:
        required = audit.get_file_config().schema_checks[0]["required_scripts"]

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            assert audit.check_schemas(target) == []

            (target / "package.json").write_text("{bad")
            gaps = audit.check_schemas(target)
            assert len(gaps) == 1
            assert "parse error" in gaps[0].message

            (target / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}}))
            gaps = audit.check_schemas(target)
            assert len(gaps) == 1
            assert "missing scripts" in gaps[0].message
            assert gaps[0].path == "package.json"

            (target / "package.json").write_text(
                json.dumps({"scripts": {script: "run" for script in required}})
            )
            assert audit.check_schemas(target) == []


# ---- Settings drift ----------------------------------------------------------


class TestLoadSettingsChecks:
    def test_reads_reference_file(self, audit) -> None:
        checks = audit.load_settings_checks()
        assert "endpoints" in checks
        assert checks["endpoints"]


class TestCompareRulesets:
    def test_reports_missing_and_extra(self, audit) -> None:
        responses = {
            "repos/template/repo/rulesets": [{"name": "main"}, {"name": "release"}],
            "repos/target/repo/rulesets": [{"name": "main"}, {"name": "extra"}],
        }

        with mock.patch.object(
            audit, "gh_api", side_effect=lambda path, errors=None: responses[path]
        ):
            drifts = audit.compare_rulesets("template/repo", "target/repo")

        assert any(d.name == "release" and d.status == "missing" for d in drifts)
        assert any(d.name == "extra" and d.status == "extra" for d in drifts)

    def test_reports_no_drift(self, audit) -> None:
        responses = {
            "repos/template/repo/rulesets": [{"name": "main"}, {"name": "release"}],
            "repos/target/repo/rulesets": [{"name": "main"}, {"name": "release"}],
        }
        with mock.patch.object(
            audit, "gh_api", side_effect=lambda path, errors=None: responses[path]
        ):
            assert audit.compare_rulesets("template/repo", "target/repo") == []

    def test_reports_api_errors(self, audit) -> None:
        with mock.patch.object(audit, "gh_api", return_value=None):
            drifts = audit.compare_rulesets("template/repo", "target/repo")
        assert len(drifts) == 1
        assert drifts[0].status == "api_error"


class TestCompareLabels:
    def test_reports_missing_and_extra(self, audit) -> None:
        responses = {
            "repos/template/repo/labels": [
                {"name": "bug", "color": "d73a4a", "description": "A bug"},
                {"name": "enhancement", "color": "a2eeef", "description": ""},
            ],
            "repos/target/repo/labels": [
                {"name": "bug", "color": "d73a4a", "description": "A bug"},
                {"name": "custom", "color": "ffffff", "description": ""},
            ],
        }
        with mock.patch.object(
            audit, "gh_api", side_effect=lambda path, errors=None: responses[path]
        ):
            drifts = audit.compare_labels("template/repo", "target/repo")

        assert any(d.name == "enhancement" and d.status == "missing" for d in drifts)
        assert any(d.name == "custom" and d.status == "extra" for d in drifts)

    def test_reports_color_mismatch(self, audit) -> None:
        responses = {
            "repos/template/repo/labels": [
                {"name": "bug", "color": "d73a4a", "description": "A bug"},
            ],
            "repos/target/repo/labels": [
                {"name": "bug", "color": "ff0000", "description": "A bug"},
            ],
        }
        with mock.patch.object(
            audit, "gh_api", side_effect=lambda path, errors=None: responses[path]
        ):
            drifts = audit.compare_labels("template/repo", "target/repo")

        assert len(drifts) == 1
        assert drifts[0].name == "bug"
        assert drifts[0].status == "mismatch"
        assert drifts[0].field == "color"
        assert drifts[0].template_value == "d73a4a"
        assert drifts[0].target_value == "ff0000"

    def test_reports_description_mismatch(self, audit) -> None:
        responses = {
            "repos/template/repo/labels": [
                {"name": "bug", "color": "d73a4a", "description": "A bug"},
            ],
            "repos/target/repo/labels": [
                {"name": "bug", "color": "d73a4a", "description": "Something else"},
            ],
        }
        with mock.patch.object(
            audit, "gh_api", side_effect=lambda path, errors=None: responses[path]
        ):
            drifts = audit.compare_labels("template/repo", "target/repo")

        assert len(drifts) == 1
        assert drifts[0].field == "description"

    def test_reports_no_drift(self, audit) -> None:
        label = {"name": "bug", "color": "d73a4a", "description": "A bug"}
        responses = {
            "repos/template/repo/labels": [label],
            "repos/target/repo/labels": [label],
        }
        with mock.patch.object(
            audit, "gh_api", side_effect=lambda path, errors=None: responses[path]
        ):
            assert audit.compare_labels("template/repo", "target/repo") == []

    def test_reports_api_error(self, audit) -> None:
        with mock.patch.object(audit, "gh_api", return_value=None):
            drifts = audit.compare_labels("template/repo", "target/repo")
        assert len(drifts) == 1
        assert drifts[0].status == "api_error"


class TestAuditSettings:
    def test_reports_field_list_nested_and_ruleset_drift(self, audit) -> None:
        checks = {
            "endpoints": [
                {
                    "path": "repos/{repo}",
                    "section": "general",
                    "fields": ["allow_squash_merge", "topics"],
                    "nested_fields": {"security_and_analysis": ["secret_scanning.status"]},
                },
                {"path": "repos/{repo}/rulesets", "section": "rulesets", "compare": "rulesets"},
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
            mock.patch.object(audit, "load_settings_checks", return_value=checks),
            mock.patch.object(
                audit, "gh_api", side_effect=lambda path, errors=None: responses[path]
            ),
        ):
            result = audit.audit_settings("template/repo", "target/repo")

        general = result.sections["general"]
        keys = [d.key for d in general]
        assert "allow_squash_merge" in keys
        assert "topics" not in keys
        assert "security_and_analysis.secret_scanning.status" in keys

        squash = next(d for d in general if d.key == "allow_squash_merge")
        assert squash.template_value is True
        assert squash.target_value is False

        rulesets_section = result.sections["rulesets"]
        assert len(rulesets_section) == 1
        assert rulesets_section[0].name == "main"
        assert rulesets_section[0].status == "missing"

    def test_reports_no_drift_for_equal_values(self, audit) -> None:
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
            mock.patch.object(audit, "load_settings_checks", return_value=checks),
            mock.patch.object(
                audit, "gh_api", side_effect=lambda path, errors=None: responses[path]
            ),
        ):
            result = audit.audit_settings("template/repo", "target/repo")

        assert result.sections["general"] == []

    def test_reports_api_failures(self, audit) -> None:
        checks = {
            "endpoints": [
                {"path": "repos/{repo}", "section": "general", "fields": ["allow_squash_merge"]},
                {"path": "repos/{repo}/rulesets", "section": "rulesets", "compare": "rulesets"},
            ],
        }

        def failing_gh_api(path: str, errors: list[str] | None = None):
            if errors is not None:
                errors.append(f"`{path}`: boom")
            return None

        with (
            mock.patch.object(audit, "load_settings_checks", return_value=checks),
            mock.patch.object(audit, "gh_api", side_effect=failing_gh_api),
        ):
            result = audit.audit_settings("template/repo", "target/repo")

        assert len(result.api_errors) > 0
        general = result.sections["general"]
        assert len(general) == 1
        assert general[0].target_value == "unknown (API error)"
        rulesets_section = result.sections["rulesets"]
        assert len(rulesets_section) == 1
        assert rulesets_section[0].status == "api_error"


# ---- Argument parsing --------------------------------------------------------


class TestParseArgs:
    def test_explicit_template_and_target(self, audit) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(audit, "is_github_repo_arg", return_value=True),
                mock.patch.object(audit, "detect_target", return_value="target/repo"),
            ):
                result = audit.parse_args(["audit.py", "owner/repo", tmp])
        assert result.template_repo == "owner/repo"
        assert result.target == Path(tmp).resolve()
        assert result.target_repo == "target/repo"

    def test_explicit_template_default_target(self, audit) -> None:
        with (
            mock.patch.object(audit, "is_github_repo_arg", return_value=True),
            mock.patch.object(audit, "detect_target", return_value="target/repo"),
        ):
            result = audit.parse_args(["audit.py", "owner/repo"])
        assert result.template_repo == "owner/repo"
        assert result.target == Path.cwd().resolve()
        assert result.target_repo == "target/repo"

    def test_auto_detect_returns_detected(self, audit) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(audit, "is_github_repo_arg", return_value=False),
                mock.patch.object(audit, "detect_target", return_value="target/repo"),
                mock.patch.object(audit, "detect_template", return_value="template/repo"),
            ):
                result = audit.parse_args(["audit.py", tmp])
        assert result.template_repo == "template/repo"
        assert result.detected

    def test_no_template_raises_audit_error(self, audit) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(audit, "is_github_repo_arg", return_value=False),
                mock.patch.object(audit, "detect_target", return_value="target/repo"),
                mock.patch.object(audit, "detect_template", return_value=None),
            ):
                with pytest.raises(audit.AuditError):
                    audit.parse_args(["audit.py", tmp])


# ---- Main --------------------------------------------------------------------


class TestAuditMain:
    def test_explicit_template_prints_report(self, audit) -> None:
        stdout = io.StringIO()
        file_drift = audit.FileDriftResult()
        settings_drift = audit.SettingsDriftResult(sections={"general": []})

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(audit.sys, "argv", ["audit.py", "template/repo", tmp]),
            mock.patch.object(audit, "detect_target", return_value="target/repo"),
            mock.patch.object(audit, "audit_files", return_value=file_drift),
            mock.patch.object(audit, "audit_settings", return_value=settings_drift),
            contextlib.redirect_stdout(stdout),
        ):
            assert audit.main() == 0

        output = stdout.getvalue()
        assert "# Audit report: `target/repo`" in output
        assert "_Template: `template/repo`_" in output
        assert "## File drift" in output
        assert "## Settings drift" in output

    def test_auto_detects_template(self, audit) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        file_drift = audit.FileDriftResult()
        settings_drift = audit.SettingsDriftResult()

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(audit.sys, "argv", ["audit.py", tmp]),
            mock.patch.object(audit, "detect_target", return_value="target/repo"),
            mock.patch.object(audit, "detect_template", return_value="template/repo"),
            mock.patch.object(audit, "audit_files", return_value=file_drift),
            mock.patch.object(audit, "audit_settings", return_value=settings_drift),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            assert audit.main() == 0

        assert "DETECTED_TEMPLATE=template/repo" in stderr.getvalue()

    def test_returns_error_when_template_missing(self, audit) -> None:
        stderr = io.StringIO()

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(audit.sys, "argv", ["audit.py", tmp]),
            mock.patch.object(audit, "detect_target", return_value="target/repo"),
            mock.patch.object(audit, "detect_template", return_value=None),
            contextlib.redirect_stderr(stderr),
        ):
            assert audit.main() == 1

        assert "could not detect" in stderr.getvalue()
