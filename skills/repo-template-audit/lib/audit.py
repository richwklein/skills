#!/usr/bin/env python3
"""repo-template-audit — drift detection against a GitHub template repo.

Usage:
    audit.py <template-owner/repo> [target-path]

template-owner/repo: the GitHub repo to compare against (e.g. richwklein/repo-template-base).
target-path:         local repo to audit (defaults to current working directory).

Outputs a markdown report on stdout.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from difflib import unified_diff
from pathlib import Path


# ---- Classification config ---------------------------------------------------
# Files to skip entirely during comparison.
IGNORE = {
    ".git",
    "README.md",
}

# Prefixes to skip (any path starting with these is ignored).
IGNORE_PREFIXES = (
    ".git/",
    ".claude/",
    "src/",
)

# Files that must exist but whose content varies per repo.
PRESENCE_ONLY = {
    "CODE_OF_CONDUCT.md",
    "package.json",
    "package-lock.json",
    "astro.config.ts",
    "vitest.setup.ts",
    "release-please-config.json",
    ".release-please-manifest.json",
}

# Schema validations applied when the file exists locally.
SCHEMA_CHECKS = [
    {
        "path": "package.json",
        "required_scripts": [
            "dev", "build", "preview", "lint", "lint:fix",
            "format", "format:fix", "test", "test:coverage", "verify",
        ],
    },
]


# ---- gh CLI helpers ----------------------------------------------------------

def gh_api(path: str, errors: list[str] | None = None) -> dict | list | None:
    """GET an API path via `gh api`. Returns parsed JSON, or None on error."""
    try:
        out = subprocess.run(
            ["gh", "api", path],
            check=True, capture_output=True, text=True,
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
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        sys.exit(f"error: {target} is not a git repo")
    if inside != "true":
        sys.exit(f"error: {target} is not a git repo")

    try:
        remote = subprocess.run(
            ["git", "-C", str(target), "remote", "get-url", "origin"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        sys.exit(f"error: no origin remote configured in {target}")

    m = re.search(r"github\.com[^:/]*[:/]([^/]+/[^/]+?)(?:\.git)?$", remote)
    if not m:
        sys.exit(f"error: could not parse owner/repo from remote: {remote}")
    return m.group(1)


# ---- File tree walk ----------------------------------------------------------

def fetch_tree(repo: str) -> list[str]:
    """Fetch all file paths from a repo's default branch tree."""
    data = gh_api(f"repos/{repo}/git/trees/HEAD?recursive=1")
    if not data or "tree" not in data:
        sys.exit(f"error: could not fetch file tree for {repo}")
    return [
        entry["path"]
        for entry in data["tree"]
        if entry["type"] == "blob"
    ]


def should_ignore(path: str) -> bool:
    """Return True if path should be skipped entirely."""
    if path in IGNORE:
        return True
    for prefix in IGNORE_PREFIXES:
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
        c_lines, l_lines,
        fromfile=f"template/{path}", tofile=f"local/{path}",
        n=2,
    )
    return "".join(list(diff)[:40])


def audit_files(target: Path, template_repo: str) -> list[str]:
    """Walk the template tree and compare against the target."""
    tree = fetch_tree(template_repo)

    missing = []
    drifted = []
    fetch_errors = []

    for path in tree:
        if should_ignore(path):
            continue

        local_path = target / path

        if path in PRESENCE_ONLY:
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

    # Schema checks
    schema_gaps = check_schemas(target)

    out: list[str] = ["## File drift", ""]

    out.append(f"### Missing ({len(missing)})")
    out.append("")
    if not missing:
        out.append("_None._")
    else:
        for p, kind in missing:
            out.append(f"- `{p}` ({kind})")
    out.append("")

    out.append(f"### Drifted ({len(drifted)})")
    out.append("")
    if not drifted:
        out.append("_None._")
    else:
        for path, snippet in drifted:
            out.append(f"- `{path}`")
            out.append("")
            out.append("  ```diff")
            for line in snippet.splitlines():
                out.append(f"  {line}")
            out.append("  ```")
            out.append("")

    out.append("### Schema gaps")
    out.append("")
    if not schema_gaps:
        out.append("_None._")
    else:
        out.extend(schema_gaps)
    out.append("")

    if fetch_errors:
        out.append("### Fetch errors")
        out.append("")
        out.append("_Could not fetch these from the template repo:_")
        for p in fetch_errors:
            out.append(f"- `{p}`")
        out.append("")

    return out


def check_schemas(target: Path) -> list[str]:
    """Run schema validations against local files."""
    out = []
    for check in SCHEMA_CHECKS:
        pkg = target / check["path"]
        if not pkg.is_file():
            continue
        try:
            data = json.loads(pkg.read_text())
        except json.JSONDecodeError as e:
            out.append(f"- `{check['path']}`: parse error ({e})")
            continue

        required = check.get("required_scripts", [])
        if required:
            scripts = data.get("scripts", {})
            missing = [s for s in required if s not in scripts]
            if missing:
                out.append(f"- `{check['path']}`: missing scripts `{', '.join(missing)}`")
    return out


# ---- Settings drift ----------------------------------------------------------

def load_settings_checks() -> dict:
    """Load the settings-checks.json from the reference directory."""
    ref_path = Path(__file__).resolve().parent.parent / "reference" / "settings-checks.json"
    if not ref_path.is_file():
        sys.exit(f"error: settings checks not found at {ref_path}")
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


def fmt(v) -> str:
    """Format a value for the drift table."""
    if isinstance(v, list):
        return ", ".join(sorted(str(i) for i in v)) if v else "[]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def compare_rulesets(
    template_repo: str,
    target_repo: str,
    errors: list[str] | None = None,
) -> list[str]:
    """Compare rulesets between template and target repos."""
    t_rulesets = gh_api(f"repos/{template_repo}/rulesets", errors=errors)
    a_rulesets = gh_api(f"repos/{target_repo}/rulesets", errors=errors)

    if not isinstance(t_rulesets, list) or not isinstance(a_rulesets, list):
        return ["| rulesets | unknown | unknown (API error) |"]

    t_names = {r.get("name") for r in t_rulesets if isinstance(r, dict)}
    a_names = {r.get("name") for r in a_rulesets if isinstance(r, dict)}

    out = []
    missing = t_names - a_names
    extra = a_names - t_names

    if not missing and not extra:
        out.append("| _no drift_ | | |")
    else:
        for name in sorted(missing):
            out.append(f"| ruleset `{name}` | present | **missing** |")
        for name in sorted(extra):
            out.append(f"| ruleset `{name}` | absent | present (extra) |")

    return out


def audit_settings(template_repo: str, target_repo: str) -> list[str]:
    """Compare GitHub settings between template and target repos."""
    checks = load_settings_checks()
    out: list[str] = ["## Settings drift", ""]

    sections: dict[str, list[str]] = {}
    api_errors: list[str] = []

    for endpoint in checks.get("endpoints", []):
        path_template = endpoint["path"]
        section_name = endpoint.get("section", "general")

        if endpoint.get("compare") == "rulesets":
            rows = compare_rulesets(template_repo, target_repo, errors=api_errors)
            sections.setdefault(section_name, []).extend(rows)
            continue

        t_path = path_template.format(repo=template_repo)
        a_path = path_template.format(repo=target_repo)

        t_data = gh_api(t_path, errors=api_errors)
        a_data = gh_api(a_path, errors=api_errors)

        rows = []

        if not isinstance(t_data, dict) or not isinstance(a_data, dict):
            rows.append(f"| endpoint `{path_template}` | unknown | unknown (API error) |")
            sections.setdefault(section_name, []).extend(rows)
            continue

        for field in endpoint.get("fields", []):
            t_val = t_data.get(field, "unknown")
            a_val = a_data.get(field, "unknown")

            if isinstance(t_val, list):
                t_norm = sorted(str(i) for i in t_val)
                a_norm = sorted(str(i) for i in a_val) if isinstance(a_val, list) else a_val
                if t_norm == a_norm:
                    continue
            elif t_val == a_val:
                continue

            rows.append(f"| {field} | `{fmt(t_val)}` | `{fmt(a_val)}` |")

        for parent_key, dotpaths in endpoint.get("nested_fields", {}).items():
            t_parent = t_data.get(parent_key, {}) or {}
            a_parent = a_data.get(parent_key, {}) or {}

            for dotpath in dotpaths:
                t_val = resolve_nested(t_parent, dotpath)
                a_val = resolve_nested(a_parent, dotpath)
                if t_val == a_val:
                    continue
                rows.append(f"| {parent_key}.{dotpath} | `{fmt(t_val)}` | `{fmt(a_val)}` |")

        sections.setdefault(section_name, []).extend(rows)

    for section_name, rows in sections.items():
        out.append(f"### {section_name}")
        out.append("")
        out.append("| key | template | target |")
        out.append("|---|---|---|")
        out.extend(rows or ["| _no drift_ | | |"])
        out.append("")

    if api_errors:
        out.append("### API errors")
        out.append("")
        out.append("_Could not verify these settings endpoints:_")
        for error in api_errors:
            out.append(f"- {error}")
        out.append("")

    return out


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


def main() -> int:
    if len(sys.argv) >= 2 and is_github_repo_arg(sys.argv[1]):
        template_repo = sys.argv[1]
        target = Path(sys.argv[2] if len(sys.argv) > 2 else os.getcwd()).resolve()
    else:
        target = Path(sys.argv[1] if len(sys.argv) > 1 else os.getcwd()).resolve()
        target_repo_id = detect_target(target)
        detected = detect_template(target_repo_id)
        if detected:
            print(f"DETECTED_TEMPLATE={detected}", file=sys.stderr)
            template_repo = detected
        else:
            print("error: no template argument provided and could not detect "
                  "template_repository from GitHub API. Pass the template repo "
                  "as the first argument.", file=sys.stderr)
            print("usage: audit.py <template-owner/repo> [target-path]", file=sys.stderr)
            sys.exit(1)

    target_repo = detect_target(target)

    print(f"# Audit report: `{target_repo}`")
    print()
    print(f"_Template: `{template_repo}`_")
    print(f"_Local path: `{target}`_")
    print()
    print("\n".join(audit_files(target, template_repo)))
    print("\n".join(audit_settings(template_repo, target_repo)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
