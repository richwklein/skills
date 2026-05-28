from __future__ import annotations


class TestModels:
    def test_schema_gap(self, models) -> None:
        gap = models.SchemaGap(path="package.json", message="missing scripts")
        assert gap.path == "package.json"
        assert gap.message == "missing scripts"

    def test_file_drift_result_defaults(self, models) -> None:
        result = models.FileDriftResult()
        assert result.missing == []
        assert result.drifted == []
        assert result.schema_gaps == []
        assert result.fetch_errors == []

    def test_settings_drift_result_defaults(self, models) -> None:
        result = models.SettingsDriftResult()
        assert result.sections == {}
        assert result.api_errors == []

    def test_apply_results_defaults(self, models) -> None:
        settings = models.ApplySettingsResult()
        assert settings.actions == []
        assert settings.errors == []
        rulesets = models.ApplyRulesetsResult()
        assert rulesets.actions == []
        files = models.ApplyFilesResult()
        assert files.synced == []
        assert files.drifted == []
        assert files.fetch_errors == []

    def test_setting_drift(self, models) -> None:
        d = models.SettingDrift(key="x", template_value=True, target_value=False)
        assert d.key == "x"
        assert d.template_value is True
        assert d.target_value is False

    def test_ruleset_drift(self, models) -> None:
        d = models.RulesetDrift(name="main", status="missing")
        assert d.name == "main"
        assert d.status == "missing"

    def test_apply_action(self, models) -> None:
        a = models.ApplyAction(success=True, description="done")
        assert a.success
        assert a.description == "done"
