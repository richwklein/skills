from __future__ import annotations


class TestFormatValue:
    def test_formats_values(self, render) -> None:
        assert render.format_value(["z", "a"]) == "a, z"
        assert render.format_value([]) == "[]"
        assert render.format_value(True) == "true"
        assert render.format_value(False) == "false"
        assert render.format_value(3) == "3"


class TestRenderFileDrift:
    def test_renders_all_sections(self, render, models) -> None:
        result = models.FileDriftResult(
            missing=[
                models.MissingFile("CODE_OF_CONDUCT.md", "presence_only"),
                models.MissingFile(
                    "missing.txt",
                    "exact_match",
                    provenance="deleted_locally",
                    evidence="abc1234 remove missing.txt",
                ),
            ],
            drifted=[
                models.DriftedFile("drift.txt", "--- a\n+++ b\n-old\n+new"),
                models.DriftedFile("behind.txt", "--- a\n+++ b\n+added", behind_ref="def5678"),
            ],
            schema_gaps=[models.SchemaGap("package.json", "missing scripts `dev`")],
            fetch_errors=["unfetchable.txt"],
        )
        output = "\n".join(render.render_file_drift(result))
        assert "### Missing (2)" in output
        assert "**New in template**" in output
        assert "`CODE_OF_CONDUCT.md` (presence_only)" in output
        assert "**Deleted locally**" in output
        assert "`missing.txt` (exact_match) — deleted locally in `abc1234 remove missing.txt`" in (
            output
        )
        assert "### Drifted (2)" in output
        assert "Diffs read local → template" in output
        assert "`drift.txt`" in output
        assert "`behind.txt` — **behind template**" in output
        assert "template@def5678" in output
        assert "```diff" in output
        assert "### Schema gaps" in output
        assert "`package.json`: missing scripts `dev`" in output
        assert "### Fetch errors" in output
        assert "`unfetchable.txt`" in output

    def test_renders_none_for_empty(self, render, models) -> None:
        result = models.FileDriftResult()
        output = "\n".join(render.render_file_drift(result))
        assert "### Missing (0)\n\n_None._" in output
        assert "### Drifted (0)\n\n_None._" in output
        assert "### Schema gaps\n\n_None._" in output
        assert "Fetch errors" not in output


class TestRenderSettingsDrift:
    def test_renders_setting_and_ruleset_drift(self, render, models) -> None:
        result = models.SettingsDriftResult(
            sections={
                "general": [
                    models.SettingDrift("allow_squash_merge", True, False),
                ],
                "rulesets": [
                    models.RulesetDrift("main", "missing"),
                    models.RulesetDrift("extra", "extra"),
                ],
            },
        )
        output = "\n".join(render.render_settings_drift(result))
        assert "| allow_squash_merge | `true` | `false` |" in output
        assert "| ruleset `main` | present | **missing** |" in output
        assert "| ruleset `extra` | absent | present (extra) |" in output

    def test_renders_no_drift(self, render, models) -> None:
        result = models.SettingsDriftResult(sections={"general": []})
        output = "\n".join(render.render_settings_drift(result))
        assert "| _no drift_ | | |" in output

    def test_renders_api_error_ruleset(self, render, models) -> None:
        result = models.SettingsDriftResult(
            sections={"rulesets": [models.RulesetDrift("rulesets", "api_error")]},
        )
        output = "\n".join(render.render_settings_drift(result))
        assert "| rulesets | unknown | unknown (API error) |" in output

    def test_renders_api_errors_section(self, render, models) -> None:
        result = models.SettingsDriftResult(api_errors=["endpoint failed"])
        output = "\n".join(render.render_settings_drift(result))
        assert "### API errors" in output
        assert "endpoint failed" in output


class TestRenderApplyReport:
    def test_includes_fetch_errors_and_skipped(self, render, models) -> None:
        settings = models.ApplySettingsResult()
        rulesets = models.ApplyRulesetsResult()
        files = models.ApplyFilesResult(
            synced=["new.txt"],
            drifted=[
                models.DriftedFile("drift.txt", "--- a\n+++ b"),
                models.DriftedFile("behind.txt", "--- a\n+++ b", behind_ref="def5678"),
            ],
            skipped_deleted=[
                models.MissingFile(
                    "removed.txt",
                    "exact_match",
                    provenance="deleted_locally",
                    evidence="abc1234 remove removed.txt",
                ),
            ],
            fetch_errors=["missing-content.txt"],
        )
        output = render.render_apply_report(
            "target/repo",
            "template/repo",
            settings,
            rulesets,
            files,
        )
        assert "### Fetch errors (1)" in output
        assert "`missing-content.txt`" in output
        assert "### Skipped — deleted locally (1)" in output
        assert "`removed.txt` (exact_match) — deleted locally in `abc1234 remove removed.txt`" in (
            output
        )
        assert "### Needs review (2)" in output
        assert "Diffs read local → template" in output
        assert "`behind.txt` — **behind template**" in output

    def test_handles_empty_sections(self, render, models) -> None:
        settings = models.ApplySettingsResult()
        rulesets = models.ApplyRulesetsResult()
        files = models.ApplyFilesResult()
        output = render.render_apply_report(
            "target/repo",
            "template/repo",
            settings,
            rulesets,
            files,
        )
        assert "_No settings drift" in output
        assert "### Synced (0)" in output
        assert "### Needs review (0)" in output
        assert "_None._" in output

    def test_renders_actions(self, render, models) -> None:
        settings = models.ApplySettingsResult(
            actions=[models.ApplyAction(True, "fixed field")],
        )
        rulesets = models.ApplyRulesetsResult(
            actions=[models.ApplyAction(False, "ruleset failed")],
            errors=["api boom"],
        )
        files = models.ApplyFilesResult()
        output = render.render_apply_report(
            "target/repo",
            "template/repo",
            settings,
            rulesets,
            files,
        )
        assert "- ✓ fixed field" in output
        assert "- ✗ ruleset failed" in output
        assert "- ✗ API read: api boom" in output
