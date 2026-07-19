from __future__ import annotations

from .models import (
    ApplyFilesResult,
    ApplyRulesetsResult,
    ApplySettingsResult,
    DriftedFile,
    FileDriftResult,
    LabelDrift,
    MissingFile,
    RulesetDrift,
    SettingDrift,
    SettingsDriftResult,
)

DIFF_DIRECTION_NOTE = (
    "_Diffs read local → template: `+` lines are template content a sync would add; "
    "`-` lines are local content a sync would remove._"
)


def format_value(v) -> str:
    if isinstance(v, list):
        return ", ".join(sorted(str(i) for i in v)) if v else "[]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _missing_line(m: MissingFile) -> str:
    if m.provenance == "deleted_locally":
        return f"- `{m.path}` ({m.kind}) — deleted locally in `{m.evidence}`"
    return f"- `{m.path}` ({m.kind})"


def _drifted_heading(f: DriftedFile) -> str:
    if f.behind_ref:
        return (
            f"- `{f.path}` — **behind template** "
            f"(local matches template@{f.behind_ref}; fast-forward sync is safe)"
        )
    return f"- `{f.path}`"


def _diff_block(snippet: str) -> list[str]:
    out = ["", "  ```diff"]
    for line in snippet.splitlines():
        out.append(f"  {line}")
    out.append("  ```")
    out.append("")
    return out


def render_file_drift(result: FileDriftResult) -> list[str]:
    out: list[str] = ["## File drift", ""]

    out.append(f"### Missing ({len(result.missing)})")
    out.append("")
    new_in_template = [m for m in result.missing if m.provenance == "new_in_template"]
    deleted_locally = [m for m in result.missing if m.provenance == "deleted_locally"]
    if not result.missing:
        out.append("_None._")
    if new_in_template:
        out.append("**New in template** — never existed in this repo; adopt by default:")
        out.append("")
        for m in new_in_template:
            out.append(_missing_line(m))
        out.append("")
    if deleted_locally:
        out.append("**Deleted locally** — a local commit removed these; confirm before restoring:")
        out.append("")
        for m in deleted_locally:
            out.append(_missing_line(m))
    out.append("")

    out.append(f"### Drifted ({len(result.drifted)})")
    out.append("")
    if not result.drifted:
        out.append("_None._")
    else:
        out.append(DIFF_DIRECTION_NOTE)
        out.append("")
        for f in result.drifted:
            out.append(_drifted_heading(f))
            out.extend(_diff_block(f.diff))

    out.append("### Schema gaps")
    out.append("")
    if not result.schema_gaps:
        out.append("_None._")
    else:
        for gap in result.schema_gaps:
            out.append(f"- `{gap.path}`: {gap.message}")
    out.append("")

    if result.fetch_errors:
        out.append("### Fetch errors")
        out.append("")
        out.append("_Could not fetch these from the template repo:_")
        for p in result.fetch_errors:
            out.append(f"- `{p}`")
        out.append("")

    return out


def render_settings_drift(result: SettingsDriftResult) -> list[str]:
    out: list[str] = ["## Settings drift", ""]

    for section_name, items in result.sections.items():
        out.append(f"### {section_name}")
        out.append("")
        out.append("| key | template | target |")
        out.append("|---|---|---|")
        if not items:
            out.append("| _no drift_ | | |")
        else:
            for item in items:
                if isinstance(item, RulesetDrift):
                    if item.status == "missing":
                        out.append(f"| ruleset `{item.name}` | present | **missing** |")
                    elif item.status == "extra":
                        out.append(f"| ruleset `{item.name}` | absent | present (extra) |")
                    elif item.status == "api_error":
                        out.append("| rulesets | unknown | unknown (API error) |")
                elif isinstance(item, LabelDrift):
                    if item.status == "missing":
                        out.append(f"| label `{item.name}` | present | **missing** |")
                    elif item.status == "extra":
                        out.append(f"| label `{item.name}` | absent | present (extra) |")
                    elif item.status == "mismatch":
                        out.append(
                            f"| label `{item.name}` ({item.field}) "
                            f"| `{item.template_value}` | `{item.target_value}` |"
                        )
                    elif item.status == "api_error":
                        out.append("| labels | unknown | unknown (API error) |")
                elif isinstance(item, SettingDrift):
                    out.append(
                        f"| {item.key} | `{format_value(item.template_value)}` "
                        f"| `{format_value(item.target_value)}` |"
                    )
        out.append("")

    if result.api_errors:
        out.append("### API errors")
        out.append("")
        out.append("_Could not verify these settings endpoints:_")
        for error in result.api_errors:
            out.append(f"- {error}")
        out.append("")

    return out


def render_audit_report(
    target_repo: str,
    template_repo: str,
    target_path: str,
    file_drift: FileDriftResult,
    settings_drift: SettingsDriftResult,
) -> str:
    lines = [
        f"# Audit report: `{target_repo}`",
        "",
        f"_Template: `{template_repo}`_",
        f"_Local path: `{target_path}`_",
        "",
    ]
    lines.extend(render_file_drift(file_drift))
    lines.extend(render_settings_drift(settings_drift))
    return "\n".join(lines)


def _render_actions(settings: ApplySettingsResult, rulesets: ApplyRulesetsResult) -> list[str]:
    lines: list[str] = []
    for action in settings.actions:
        mark = "✓" if action.success else "✗"
        lines.append(f"- {mark} {action.description}")
    for error in settings.errors:
        lines.append(f"- ✗ API read: {error}")
    for action in rulesets.actions:
        mark = "✓" if action.success else "✗"
        lines.append(f"- {mark} {action.description}")
    for error in rulesets.errors:
        lines.append(f"- ✗ API read: {error}")
    return lines


def render_apply_report(
    target_repo: str,
    template_repo: str,
    settings: ApplySettingsResult,
    rulesets: ApplyRulesetsResult,
    files: ApplyFilesResult,
) -> str:
    lines = [
        f"# Apply report: `{target_repo}`",
        "",
        f"_Template: `{template_repo}`_",
        "",
        "## Settings",
        "",
    ]

    all_actions = _render_actions(settings, rulesets)
    if all_actions:
        lines.extend(all_actions)
    else:
        lines.append("_No settings drift — nothing to apply._")
    lines.append("")

    lines.append("## Files")
    lines.append("")
    lines.append(f"### Synced ({len(files.synced)})")
    lines.append("")
    if files.synced:
        for p in files.synced:
            lines.append(f"- `{p}`")
    else:
        lines.append("_None._")

    if files.skipped_deleted:
        lines.append("")
        lines.append(f"### Skipped — deleted locally ({len(files.skipped_deleted)})")
        lines.append("")
        lines.append(
            "_A local commit removed these template files; "
            "not restored automatically — confirm each removal still stands:_"
        )
        for m in files.skipped_deleted:
            lines.append(_missing_line(m))

    if files.fetch_errors:
        lines.append("")
        lines.append(f"### Fetch errors ({len(files.fetch_errors)})")
        lines.append("")
        lines.append("_Could not fetch these from the template repo:_")
        for p in files.fetch_errors:
            lines.append(f"- `{p}`")
    lines.append("")

    lines.append(f"### Needs review ({len(files.drifted)})")
    lines.append("")
    if files.drifted:
        lines.append("_These files differ from the template — review each before resetting._")
        lines.append(DIFF_DIRECTION_NOTE)
        lines.append("")
        for f in files.drifted:
            lines.append(_drifted_heading(f))
            lines.extend(_diff_block(f.diff))
    else:
        lines.append("_None._")

    return "\n".join(lines)
