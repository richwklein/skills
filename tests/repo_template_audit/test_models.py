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
        assert files.skipped_deleted == []
        assert files.fetch_errors == []

    def test_missing_file_defaults(self, models) -> None:
        m = models.MissingFile(path="file.txt", kind="exact_match")
        assert m.provenance == "new_in_template"
        assert m.evidence is None
        deleted = models.MissingFile(
            path="file.txt",
            kind="exact_match",
            provenance="deleted_locally",
            evidence="abc1234 remove file",
        )
        assert deleted.provenance == "deleted_locally"
        assert deleted.evidence == "abc1234 remove file"

    def test_drifted_file_defaults(self, models) -> None:
        d = models.DriftedFile(path="file.txt", diff="-a\n+b")
        assert d.behind_ref is None
        behind = models.DriftedFile(path="file.txt", diff="-a\n+b", behind_ref="def5678")
        assert behind.behind_ref == "def5678"

    def test_setting_drift(self, models) -> None:
        d = models.SettingDrift(key="x", template_value=True, target_value=False)
        assert d.key == "x"
        assert d.template_value is True
        assert d.target_value is False

    def test_ruleset_drift(self, models) -> None:
        d = models.RulesetDrift(name="main", status="missing")
        assert d.name == "main"
        assert d.status == "missing"

    def test_label_drift_defaults(self, models) -> None:
        d = models.LabelDrift(name="bug", status="missing")
        assert d.name == "bug"
        assert d.status == "missing"
        assert d.field is None
        assert d.template_value is None
        assert d.target_value is None

    def test_label_drift_mismatch(self, models) -> None:
        d = models.LabelDrift(
            name="bug",
            status="mismatch",
            field="color",
            template_value="#d73a4a",
            target_value="#ff0000",
        )
        assert d.field == "color"
        assert d.template_value == "#d73a4a"
        assert d.target_value == "#ff0000"

    def test_apply_action(self, models) -> None:
        a = models.ApplyAction(success=True, description="done")
        assert a.success
        assert a.description == "done"
