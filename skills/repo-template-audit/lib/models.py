from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SchemaGap:
    path: str
    message: str


@dataclass
class MissingFile:
    path: str
    kind: str  # exact_match | presence_only
    provenance: str = "new_in_template"  # new_in_template | deleted_locally
    evidence: str | None = None  # deleting commit ("<short-sha> <subject>")


@dataclass
class DriftedFile:
    path: str
    diff: str
    behind_ref: str | None = None  # template commit whose blob matches local content


@dataclass
class FileDriftResult:
    missing: list[MissingFile] = field(default_factory=list)
    drifted: list[DriftedFile] = field(default_factory=list)
    schema_gaps: list[SchemaGap] = field(default_factory=list)
    fetch_errors: list[str] = field(default_factory=list)


@dataclass
class SettingDrift:
    key: str
    template_value: Any
    target_value: Any


@dataclass
class RulesetDrift:
    name: str
    status: str


@dataclass
class LabelDrift:
    name: str
    status: str  # "missing" | "extra" | "mismatch" | "api_error"
    field: str | None = None  # "color" or "description" (mismatch only)
    template_value: str | None = None
    target_value: str | None = None


@dataclass
class SettingsDriftResult:
    sections: dict[str, list[SettingDrift | RulesetDrift | LabelDrift]] = field(
        default_factory=dict
    )
    api_errors: list[str] = field(default_factory=list)


@dataclass
class ApplyAction:
    success: bool
    description: str


@dataclass
class ApplySettingsResult:
    actions: list[ApplyAction] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ApplyRulesetsResult:
    actions: list[ApplyAction] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ApplyFilesResult:
    synced: list[str] = field(default_factory=list)
    drifted: list[DriftedFile] = field(default_factory=list)
    skipped_deleted: list[MissingFile] = field(default_factory=list)
    fetch_errors: list[str] = field(default_factory=list)
