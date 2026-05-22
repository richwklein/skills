#!/usr/bin/env python3
"""apply.py — audit and auto-fix template drift.

Usage:
    apply.py <template-owner/repo> [target-path]

Auto-applies:  settings drift, missing exact_match files, missing rulesets.
Reports only:  drifted files (drift may be intentional — requires human review).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit import (
    detect_target,
    detect_template,
    diff_snippet,
    fetch_file,
    fetch_tree,
    gh_api,
    is_github_repo_arg,
    load_settings_checks,
    PRESENCE_ONLY,
    resolve_nested,
    should_ignore,
)


# ---- GitHub write helper ----------------------------------------------------

def gh_write(method: str, path: str, body: dict | None = None) -> dict | None:
    cmd = ["gh", "api", "--method", method, path]
    if body is not None:
        cmd += ["--input", "-"]
    try:
        inp = json.dumps(body).encode() if body is not None else None
        result = subprocess.run(cmd, input=inp, check=True, capture_output=True)
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except subprocess.CalledProcessError as e:
        print(f"  ERROR ({method} {path}): {e.stderr.decode().strip()}", file=sys.stderr)
        return None


# ---- Settings ---------------------------------------------------------------

_FIX_METHOD = {
    "repos/{repo}": "PATCH",
    "repos/{repo}/actions/permissions": "PUT",
    "repos/{repo}/actions/permissions/workflow": "PUT",
    "repos/{repo}/actions/permissions/selected-actions": "PUT",
    "repos/{repo}/private-vulnerability-reporting": "PUT",
    "repos/{repo}/code-scanning/default-setup": "PATCH",
}


def _build_nested(dotpath_vals: dict) -> dict:
    result: dict = {}
    for dotpath, value in dotpath_vals.items():
        parts = dotpath.split(".")
        d = result
        for part in parts[:-1]:
            d = d.setdefault(part, {})
        d[parts[-1]] = value
    return result


def apply_settings(template_repo: str, target_repo: str) -> list[str]:
    checks = load_settings_checks()
    lines: list[str] = []

    for endpoint in checks.get("endpoints", []):
        path_tmpl = endpoint["path"]
        if endpoint.get("compare") == "rulesets":
            continue

        t_data = gh_api(path_tmpl.format(repo=template_repo)) or {}
        a_data = gh_api(path_tmpl.format(repo=target_repo)) or {}

        fix_fields: dict = {}
        fix_nested: dict = {}

        for field in endpoint.get("fields", []):
            t_val = t_data.get(field, "unknown")
            a_val = a_data.get(field, "unknown")
            if t_val == "unknown":
                continue
            if isinstance(t_val, list):
                t_norm = sorted(str(i) for i in t_val)
                a_norm = sorted(str(i) for i in a_val) if isinstance(a_val, list) else a_val
                if t_norm != a_norm:
                    fix_fields[field] = t_val
            elif t_val != a_val:
                fix_fields[field] = t_val

        for parent_key, dotpaths in endpoint.get("nested_fields", {}).items():
            t_parent = t_data.get(parent_key, {}) or {}
            a_parent = a_data.get(parent_key, {}) or {}
            for dotpath in dotpaths:
                t_val = resolve_nested(t_parent, dotpath)
                a_val = resolve_nested(a_parent, dotpath)
                if t_val != a_val and t_val != "unknown":
                    fix_nested.setdefault(parent_key, {})[dotpath] = t_val

        if not fix_fields and not fix_nested:
            continue

        target_path = path_tmpl.format(repo=target_repo)

        # private-vulnerability-reporting: no body, method depends on desired state
        if path_tmpl == "repos/{repo}/private-vulnerability-reporting":
            enabled = fix_fields.get("enabled")
            method = "PUT" if enabled else "DELETE"
            ok = gh_write(method, target_path)
            action = "enabled" if enabled else "disabled"
            lines.append(f"- {'✓' if ok is not None else '✗'} private vulnerability reporting → {action}")
            continue

        body = dict(fix_fields)
        for parent_key, dotpath_vals in fix_nested.items():
            body[parent_key] = _build_nested(dotpath_vals)

        method = _FIX_METHOD.get(path_tmpl, "PATCH")
        ok = gh_write(method, target_path, body)
        field_names = list(fix_fields.keys()) + list(fix_nested.keys())
        lines.append(f"- {'✓' if ok is not None else '✗'} `{path_tmpl}`: `{', '.join(field_names)}`")

    return lines


def apply_rulesets(template_repo: str, target_repo: str) -> list[str]:
    t_rulesets = gh_api(f"repos/{template_repo}/rulesets") or []
    a_rulesets = gh_api(f"repos/{target_repo}/rulesets") or []
    a_names = {r.get("name") for r in a_rulesets if isinstance(r, dict)}
    lines: list[str] = []

    for r in t_rulesets:
        if not isinstance(r, dict):
            continue
        name = r.get("name")
        if name in a_names:
            continue
        full = gh_api(f"repos/{template_repo}/rulesets/{r['id']}")
        if not full:
            lines.append(f"- ✗ ruleset `{name}`: could not fetch from template")
            continue
        payload = {k: full[k] for k in ("name", "target", "enforcement", "conditions", "rules") if k in full}
        if "bypass_actors" in full:
            payload["bypass_actors"] = full["bypass_actors"]
        ok = gh_write("POST", f"repos/{target_repo}/rulesets", payload)
        lines.append(f"- {'✓' if ok is not None else '✗'} ruleset `{name}`: created")

    return lines


# ---- Files ------------------------------------------------------------------

def apply_files(target: Path, template_repo: str) -> tuple[list[str], list[tuple]]:
    """Sync missing exact_match files. Return (applied_paths, drifted_list)."""
    tree = fetch_tree(template_repo)
    applied: list[str] = []
    drifted: list[tuple] = []

    for path in tree:
        if should_ignore(path) or path in PRESENCE_ONLY:
            continue

        canonical = fetch_file(template_repo, path)
        if canonical is None:
            continue

        local_path = target / path
        if not local_path.is_file():
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(canonical)
            applied.append(path)
        elif local_path.read_bytes() != canonical:
            drifted.append((path, diff_snippet(canonical, local_path.read_bytes(), path)))

    return applied, drifted


# ---- Report -----------------------------------------------------------------

def print_report(
    target_repo: str,
    template_repo: str,
    settings_lines: list[str],
    ruleset_lines: list[str],
    applied_files: list[str],
    drifted_files: list[tuple],
) -> None:
    print(f"# Apply report: `{target_repo}`")
    print()
    print(f"_Template: `{template_repo}`_")
    print()

    print("## Settings")
    print()
    all_setting_lines = settings_lines + ruleset_lines
    if all_setting_lines:
        for line in all_setting_lines:
            print(line)
    else:
        print("_No settings drift — nothing to apply._")
    print()

    print("## Files")
    print()
    print(f"### Synced ({len(applied_files)})")
    print()
    if applied_files:
        for p in applied_files:
            print(f"- `{p}`")
    else:
        print("_None._")
    print()

    print(f"### Needs review ({len(drifted_files)})")
    print()
    if drifted_files:
        print("_These files differ from the template. Drift may be intentional — review before resetting._")
        print()
        for path, snippet in drifted_files:
            print(f"- `{path}`")
            print()
            print("  ```diff")
            for line in snippet.splitlines():
                print(f"  {line}")
            print("  ```")
            print()
    else:
        print("_None._")


# ---- Main -------------------------------------------------------------------

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
            print(
                "error: no template argument provided and could not detect "
                "template_repository from GitHub API.",
                file=sys.stderr,
            )
            sys.exit(1)

    target_repo = detect_target(target)

    settings_lines = apply_settings(template_repo, target_repo)
    ruleset_lines = apply_rulesets(template_repo, target_repo)
    applied_files, drifted_files = apply_files(target, template_repo)

    print_report(target_repo, template_repo, settings_lines, ruleset_lines, applied_files, drifted_files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
