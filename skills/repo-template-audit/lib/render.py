from __future__ import annotations

try:
    from .models import (
        ApplyFilesResult,
        ApplyRulesetsResult,
        ApplySettingsResult,
        FileDriftResult,
        RulesetDrift,
        SettingDrift,
        SettingsDriftResult,
    )
except ImportError:
    from models import (
        ApplyFilesResult,
        ApplyRulesetsResult,
        ApplySettingsResult,
        FileDriftResult,
        RulesetDrift,
        SettingDrift,
        SettingsDriftResult,
    )


def format_value(v) -> str:
    if isinstance(v, list):
        return ", ".join(sorted(str(i) for i in v)) if v else "[]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def render_file_drift(result: FileDriftResult) -> list[str]:
    out: list[str] = ["## File drift", ""]

    out.append(f"### Missing ({len(result.missing)})")
    out.append("")
    if not result.missing:
        out.append("_None._")
    else:
        for p, kind in result.missing:
            out.append(f"- `{p}` ({kind})")
    out.append("")

    out.append(f"### Drifted ({len(result.drifted)})")
    out.append("")
    if not result.drifted:
        out.append("_None._")
    else:
        for path, snippet in result.drifted:
            out.append(f"- `{path}`")
            out.append("")
            out.append("  ```diff")
            for line in snippet.splitlines():
                out.append(f"  {line}")
            out.append("  ```")
            out.append("")

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
        lines.append(
            "_These files differ from the template. "
            "Drift may be intentional — review before resetting._"
        )
        lines.append("")
        for path, snippet in files.drifted:
            lines.append(f"- `{path}`")
            lines.append("")
            lines.append("  ```diff")
            for line in snippet.splitlines():
                lines.append(f"  {line}")
            lines.append("  ```")
            lines.append("")
    else:
        lines.append("_None._")

    return "\n".join(lines)
