#!/usr/bin/env python3
"""Drift detection against a GitHub template repo."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path

from .models import (
    FileDriftResult,
    RulesetDrift,
    SchemaGap,
    SettingDrift,
    SettingsDriftResult,
)
from .render import render_audit_report


class AuditError(Exception):
    pass


# ---- Classification config ---------------------------------------------------


@dataclass
class FileConfig:
    ignore: set[str]
    ignore_prefixes: tuple[str, ...]
    presence_only: set[str]
    schema_checks: list[dict]

    @classmethod
    def from_dict(cls, data: dict) -> FileConfig:
        return cls(
            ignore=set(data.get("ignore", [])),
            ignore_prefixes=tuple(data.get("ignore_prefixes", [])),
            presence_only=set(data.get("presence_only", [])),
            schema_checks=data.get("schema_checks", []),
        )


def load_file_checks() -> dict:
    """Load file-checks.json from the reference directory."""
    ref_path = Path(__file__).resolve().parent.parent / "reference" / "file-checks.json"
    if not ref_path.is_file():
        raise AuditError(f"file checks config not found at {ref_path}")
    with open(ref_path) as f:
        return json.load(f)


_default_file_config: FileConfig | None = None


def get_file_config() -> FileConfig:
    global _default_file_config
    if _default_file_config is None:
        _default_file_config = FileConfig.from_dict(load_file_checks())
    return _default_file_config


PRESENCE_ONLY = get_file_config().presence_only


# ---- gh CLI helpers ----------------------------------------------------------


def gh_api(path: str, errors: list[str] | None = None) -> dict | list | None:
    """GET an API path via `gh api`. Returns parsed JSON, or None on error."""
    try:
        out = subprocess.run(
            ["gh", "api", path],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return json.loads(out) if out.strip() else None
    except subprocess.CalledProcessError as e:
        if errors is not None:
            detail = e.stderr.strip() if e.stderr else f"exit status {e.returncode}"
            errors.append(f"`{path}`: {detail}")
        return None
    except json.JSONDecodeError as e:
        if errors is not None:
            errors.append(f"`{path}`: could not parse JSON ({e})")
        return None


def fetch_file(repo: str, path: str) -> bytes | None:
    """Fetch a file's raw bytes from a GitHub repo via the contents API."""
    data = gh_api(f"repos/{repo}/contents/{path}")
    if not data or "content" not in data:
        return None
    try:
        return base64.b64decode(data["content"])
    except (ValueError, TypeError):
        return None


# ---- Target detection --------------------------------------------------------


def detect_target(target: Path) -> str:
    """Return owner/repo for the target directory."""
    try:
        inside = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        inside = "false"
    if inside != "true":
        is_bare = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--is-bare-repository"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if is_bare == "true":
            raise AuditError(f"{target} is a bare repo — run from a worktree (e.g. cd main/ first)")
        raise AuditError(f"{target} is not a git repo")

    try:
        remote = subprocess.run(
            ["git", "-C", str(target), "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        raise AuditError(f"no origin remote configured in {target}")

    m = re.search(r"github\.com[^:/]*[:/]([^/]+/[^/]+?)(?:\.git)?$", remote)
    if not m:
        raise AuditError(f"could not parse owner/repo from remote: {remote}")
    return m.group(1)


# ---- File tree walk ----------------------------------------------------------


def fetch_tree(repo: str) -> list[str]:
    """Fetch all file paths from a repo's default branch tree."""
    data = gh_api(f"repos/{repo}/git/trees/HEAD?recursive=1")
    if not data or "tree" not in data:
        raise AuditError(f"could not fetch file tree for {repo}")
    return [entry["path"] for entry in data["tree"] if entry["type"] == "blob"]


def should_ignore(path: str, config: FileConfig | None = None) -> bool:
    """Return True if path should be skipped entirely."""
    if config is None:
        config = get_file_config()
    if path in config.ignore:
        return True
    for prefix in config.ignore_prefixes:
        if path.startswith(prefix):
            return True
    return False


def diff_snippet(canonical: bytes, local: bytes, path: str) -> str:
    """Return a unified diff snippet (up to 40 lines)."""
    try:
        c_lines = canonical.decode("utf-8", errors="replace").splitlines(keepends=True)
        l_lines = local.decode("utf-8", errors="replace").splitlines(keepends=True)
    except Exception:
        return "(binary file; cannot diff)"
    diff = unified_diff(
        c_lines,
        l_lines,
        fromfile=f"template/{path}",
        tofile=f"local/{path}",
        n=2,
    )
    return "".join(list(diff)[:40])


def audit_files(
    target: Path, template_repo: str, config: FileConfig | None = None
) -> FileDriftResult:
    """Walk the template tree and compare against the target."""
    if config is None:
        config = get_file_config()
    tree = fetch_tree(template_repo)

    missing = []
    drifted = []
    fetch_errors = []

    for path in tree:
        if should_ignore(path, config):
            continue

        local_path = target / path

        if path in config.presence_only:
            if not local_path.is_file():
                missing.append((path, "presence_only"))
            continue

        if not local_path.is_file():
            missing.append((path, "exact_match"))
            continue

        canonical = fetch_file(template_repo, path)
        if canonical is None:
            fetch_errors.append(path)
            continue

        local_bytes = local_path.read_bytes()
        if canonical == local_bytes:
            continue

        drifted.append((path, diff_snippet(canonical, local_bytes, path)))

    schema_gaps = check_schemas(target, config)

    return FileDriftResult(
        missing=missing,
        drifted=drifted,
        schema_gaps=schema_gaps,
        fetch_errors=fetch_errors,
    )


def check_schemas(target: Path, config: FileConfig | None = None) -> list[SchemaGap]:
    """Run schema validations against local files."""
    if config is None:
        config = get_file_config()
    gaps: list[SchemaGap] = []
    for check in config.schema_checks:
        pkg = target / check["path"]
        if not pkg.is_file():
            continue
        try:
            data = json.loads(pkg.read_text())
        except json.JSONDecodeError as e:
            gaps.append(SchemaGap(path=check["path"], message=f"parse error ({e})"))
            continue

        required = check.get("required_scripts", [])
        if required:
            scripts = data.get("scripts", {})
            missing = [s for s in required if s not in scripts]
            if missing:
                gaps.append(
                    SchemaGap(
                        path=check["path"],
                        message=f"missing scripts `{', '.join(missing)}`",
                    )
                )
    return gaps


# ---- Settings drift ----------------------------------------------------------


def load_settings_checks() -> dict:
    """Load the settings-checks.json from the reference directory."""
    ref_path = Path(__file__).resolve().parent.parent / "reference" / "settings-checks.json"
    if not ref_path.is_file():
        raise AuditError(f"settings checks not found at {ref_path}")
    with open(ref_path) as f:
        return json.load(f)


def resolve_nested(data: dict, dotpath: str):
    """Resolve a dot-separated path like 'secret_scanning.status' in a dict."""
    for key in dotpath.split("."):
        if isinstance(data, dict):
            data = data.get(key, "unknown")
        else:
            return "unknown"
    return data


def values_match(template_val, target_val) -> bool:
    """Compare two setting values, normalizing list order."""
    if isinstance(template_val, list):
        if not isinstance(target_val, list):
            return False
        return sorted(str(i) for i in template_val) == sorted(str(i) for i in target_val)
    return template_val == target_val


def compare_rulesets(
    template_repo: str,
    target_repo: str,
    errors: list[str] | None = None,
) -> list[RulesetDrift]:
    """Compare rulesets between template and target repos."""
    template_rulesets = gh_api(f"repos/{template_repo}/rulesets", errors=errors)
    target_rulesets = gh_api(f"repos/{target_repo}/rulesets", errors=errors)

    if not isinstance(template_rulesets, list) or not isinstance(target_rulesets, list):
        return [RulesetDrift(name="rulesets", status="api_error")]

    template_names = {r.get("name") for r in template_rulesets if isinstance(r, dict)}
    target_names = {r.get("name") for r in target_rulesets if isinstance(r, dict)}

    drifts: list[RulesetDrift] = []
    for name in sorted(template_names - target_names):
        drifts.append(RulesetDrift(name=name, status="missing"))
    for name in sorted(target_names - template_names):
        drifts.append(RulesetDrift(name=name, status="extra"))

    return drifts


def audit_settings(template_repo: str, target_repo: str) -> SettingsDriftResult:
    """Compare GitHub settings between template and target repos."""
    checks = load_settings_checks()

    sections: dict[str, list[SettingDrift | RulesetDrift]] = {}
    api_errors: list[str] = []

    for endpoint in checks.get("endpoints", []):
        path_template = endpoint["path"]
        section_name = endpoint.get("section", "general")

        if endpoint.get("compare") == "rulesets":
            drifts = compare_rulesets(template_repo, target_repo, errors=api_errors)
            sections.setdefault(section_name, []).extend(drifts)
            continue

        template_path = path_template.format(repo=template_repo)
        target_path = path_template.format(repo=target_repo)

        template_data = gh_api(template_path, errors=api_errors)
        target_data = gh_api(target_path, errors=api_errors)

        items: list[SettingDrift | RulesetDrift] = []

        if not isinstance(template_data, dict) or not isinstance(target_data, dict):
            items.append(
                SettingDrift(
                    key=f"endpoint `{path_template}`",
                    template_value="unknown",
                    target_value="unknown (API error)",
                )
            )
            sections.setdefault(section_name, []).extend(items)
            continue

        for field in endpoint.get("fields", []):
            template_val = template_data.get(field, "unknown")
            target_val = target_data.get(field, "unknown")
            if values_match(template_val, target_val):
                continue
            items.append(
                SettingDrift(key=field, template_value=template_val, target_value=target_val)
            )

        for parent_key, dotpaths in endpoint.get("nested_fields", {}).items():
            template_parent = template_data.get(parent_key, {}) or {}
            target_parent = target_data.get(parent_key, {}) or {}

            for dotpath in dotpaths:
                template_val = resolve_nested(template_parent, dotpath)
                target_val = resolve_nested(target_parent, dotpath)
                if template_val == target_val:
                    continue
                items.append(
                    SettingDrift(
                        key=f"{parent_key}.{dotpath}",
                        template_value=template_val,
                        target_value=target_val,
                    )
                )

        sections.setdefault(section_name, []).extend(items)

    return SettingsDriftResult(sections=sections, api_errors=api_errors)


# ---- Main -------------------------------------------------------------------


def detect_template(target_repo: str) -> str | None:
    """Try to detect the template repo from GitHub's template_repository field."""
    data = gh_api(f"repos/{target_repo}")
    if data and isinstance(data.get("template_repository"), dict):
        return data["template_repository"].get("full_name")
    return None


def is_github_repo_arg(arg: str) -> bool:
    """True if arg looks like owner/repo (not a file path)."""
    if arg.startswith(("/", ".", "~")):
        return False
    if Path(arg).exists():
        return False
    return re.fullmatch(r"[^/\s]+/[^/\s]+", arg) is not None


@dataclass
class ParsedArgs:
    template_repo: str
    target: Path
    target_repo: str
    detected: bool = False


_DEFAULT_DESCRIPTION = (
    "Compare a local repo against a GitHub template repo to detect "
    "file drift, settings drift, and schema gaps."
)


def build_parser(
    prog: str = "audit",
    description: str = _DEFAULT_DESCRIPTION,
) -> argparse.ArgumentParser:
    """Build the shared argument parser for audit and apply scripts."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
    )
    parser.add_argument(
        "template",
        nargs="?",
        default=None,
        help=(
            "Template repo in owner/repo format (e.g. richwklein/repo-template-base). "
            "If omitted, auto-detected from the GitHub API."
        ),
    )
    parser.add_argument(
        "target_path",
        nargs="?",
        default=None,
        help="Local repo path to audit (defaults to current working directory).",
    )
    return parser


def parse_args(argv: list[str], description: str = _DEFAULT_DESCRIPTION) -> ParsedArgs:
    """Parse CLI arguments shared by audit and apply scripts."""
    parser = build_parser(
        prog=Path(argv[0]).stem if argv else "audit",
        description=description,
    )
    raw = parser.parse_args(argv[1:])

    if raw.template and is_github_repo_arg(raw.template):
        template_repo = raw.template
        target = Path(raw.target_path or os.getcwd()).resolve()
        detected = False
    else:
        target = Path(raw.template or os.getcwd()).resolve()
        target_repo_id = detect_target(target)
        template = detect_template(target_repo_id)
        if not template:
            raise AuditError(
                "no template argument provided and could not detect "
                "template_repository from GitHub API. Pass the template repo "
                "as the first argument."
            )
        print(f"DETECTED_TEMPLATE={template}", file=sys.stderr)
        template_repo = template
        detected = True

    target_repo = detect_target(target)
    return ParsedArgs(
        template_repo=template_repo,
        target=target,
        target_repo=target_repo,
        detected=detected,
    )


def main() -> int:
    try:
        args = parse_args(sys.argv)
    except AuditError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    file_drift = audit_files(args.target, args.template_repo)
    settings_drift = audit_settings(args.template_repo, args.target_repo)
    print(
        render_audit_report(
            args.target_repo,
            args.template_repo,
            str(args.target),
            file_drift,
            settings_drift,
        )
    )
    return 0
