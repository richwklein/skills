#!/usr/bin/env python3
"""apply.py — audit and auto-fix template drift.

Usage:
    apply.py <template-owner/repo> [target-path]

Auto-applies:  settings drift, missing exact_match files, missing rulesets.
Reports only:  drifted files (drift may be intentional — requires human review).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

try:
    from .audit import (
        AuditError,
        diff_snippet,
        fetch_file,
        fetch_tree,
        gh_api,
        load_settings_checks,
        parse_args,
        PRESENCE_ONLY,
        resolve_nested,
        should_ignore,
        values_match,
    )
    from .models import (
        ApplyAction,
        ApplyFilesResult,
        ApplyRulesetsResult,
        ApplySettingsResult,
    )
    from .render import render_apply_report
except ImportError:
    from audit import (
        AuditError,
        diff_snippet,
        fetch_file,
        fetch_tree,
        gh_api,
        load_settings_checks,
        parse_args,
        PRESENCE_ONLY,
        resolve_nested,
        should_ignore,
        values_match,
    )
    from models import (
        ApplyAction,
        ApplyFilesResult,
        ApplyRulesetsResult,
        ApplySettingsResult,
    )
    from render import render_apply_report


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


def apply_settings(template_repo: str, target_repo: str) -> ApplySettingsResult:
    checks = load_settings_checks()
    actions: list[ApplyAction] = []
    errors: list[str] = []

    for endpoint in checks.get("endpoints", []):
        path_tmpl = endpoint["path"]
        if endpoint.get("compare") == "rulesets":
            continue

        template_path = path_tmpl.format(repo=template_repo)
        target_path = path_tmpl.format(repo=target_repo)
        template_data = gh_api(template_path, errors=errors)
        target_data = gh_api(target_path, errors=errors)

        if not isinstance(template_data, dict) or not isinstance(target_data, dict):
            actions.append(ApplyAction(
                success=False,
                description=f"`{path_tmpl}`: skipped (API read failed)",
            ))
            continue

        fix_fields: dict = {}
        fix_nested: dict = {}

        for field in endpoint.get("fields", []):
            template_val = template_data.get(field, "unknown")
            target_val = target_data.get(field, "unknown")
            if template_val == "unknown":
                continue
            if not values_match(template_val, target_val):
                fix_fields[field] = template_val

        for parent_key, dotpaths in endpoint.get("nested_fields", {}).items():
            template_parent = template_data.get(parent_key, {}) or {}
            target_parent = target_data.get(parent_key, {}) or {}
            for dotpath in dotpaths:
                template_val = resolve_nested(template_parent, dotpath)
                target_val = resolve_nested(target_parent, dotpath)
                if template_val != target_val and template_val != "unknown":
                    fix_nested.setdefault(parent_key, {})[dotpath] = template_val

        if not fix_fields and not fix_nested:
            continue

        if path_tmpl == "repos/{repo}/private-vulnerability-reporting":
            enabled = fix_fields.get("enabled")
            method = "PUT" if enabled else "DELETE"
            ok = gh_write(method, target_path)
            action_desc = "enabled" if enabled else "disabled"
            actions.append(ApplyAction(
                success=ok is not None,
                description=f"private vulnerability reporting → {action_desc}",
            ))
            continue

        body = dict(fix_fields)
        for parent_key, dotpath_vals in fix_nested.items():
            body[parent_key] = _build_nested(dotpath_vals)

        method = _FIX_METHOD.get(path_tmpl, "PATCH")
        ok = gh_write(method, target_path, body)
        field_names = list(fix_fields.keys()) + list(fix_nested.keys())
        actions.append(ApplyAction(
            success=ok is not None,
            description=f"`{path_tmpl}`: `{', '.join(field_names)}`",
        ))

    return ApplySettingsResult(actions=actions, errors=errors)


def apply_rulesets(template_repo: str, target_repo: str) -> ApplyRulesetsResult:
    errors: list[str] = []
    template_rulesets = gh_api(f"repos/{template_repo}/rulesets", errors=errors)
    target_rulesets = gh_api(f"repos/{target_repo}/rulesets", errors=errors)

    if not isinstance(template_rulesets, list) or not isinstance(target_rulesets, list):
        return ApplyRulesetsResult(
            actions=[ApplyAction(success=False, description="rulesets: skipped (API read failed)")],
            errors=errors,
        )

    target_names = {r.get("name") for r in target_rulesets if isinstance(r, dict)}
    actions: list[ApplyAction] = []

    for r in template_rulesets:
        if not isinstance(r, dict):
            continue
        name = r.get("name")
        ruleset_id = r.get("id")
        if not name or not ruleset_id:
            actions.append(ApplyAction(
                success=False,
                description="ruleset: skipped malformed template summary",
            ))
            continue
        if name in target_names:
            continue
        full = gh_api(f"repos/{template_repo}/rulesets/{ruleset_id}", errors=errors)
        if not isinstance(full, dict):
            actions.append(ApplyAction(
                success=False,
                description=f"ruleset `{name}`: could not fetch from template",
            ))
            continue
        payload = {k: full[k] for k in ("name", "target", "enforcement", "conditions", "rules") if k in full}
        if "bypass_actors" in full:
            payload["bypass_actors"] = full["bypass_actors"]
        ok = gh_write("POST", f"repos/{target_repo}/rulesets", payload)
        actions.append(ApplyAction(
            success=ok is not None,
            description=f"ruleset `{name}`: created",
        ))

    return ApplyRulesetsResult(actions=actions, errors=errors)


# ---- Files ------------------------------------------------------------------

def apply_files(target: Path, template_repo: str) -> ApplyFilesResult:
    """Sync missing exact_match files. Return structured result."""
    tree = fetch_tree(template_repo)
    synced: list[str] = []
    drifted: list[tuple] = []
    fetch_errors: list[str] = []

    for path in tree:
        if should_ignore(path) or path in PRESENCE_ONLY:
            continue

        canonical = fetch_file(template_repo, path)
        if canonical is None:
            fetch_errors.append(path)
            continue

        local_path = target / path
        if not local_path.is_file():
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(canonical)
            synced.append(path)
        elif local_path.read_bytes() != canonical:
            drifted.append((path, diff_snippet(canonical, local_path.read_bytes(), path)))

    return ApplyFilesResult(synced=synced, drifted=drifted, fetch_errors=fetch_errors)


# ---- Main -------------------------------------------------------------------

def main() -> int:
    try:
        args = parse_args(sys.argv)
    except AuditError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.detected:
        print(
            "error: detected template must be confirmed before apply mode mutates the repo. "
            "Rerun with the template repo as the first argument.",
            file=sys.stderr,
        )
        return 2

    settings = apply_settings(args.template_repo, args.target_repo)
    rulesets = apply_rulesets(args.template_repo, args.target_repo)
    files = apply_files(args.target, args.template_repo)

    print(render_apply_report(
        args.target_repo, args.template_repo, settings, rulesets, files,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
