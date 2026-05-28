from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SchemaGap:
    path: str
    message: str


@dataclass
class FileDriftResult:
    missing: list[tuple[str, str]] = field(default_factory=list)
    drifted: list[tuple[str, str]] = field(default_factory=list)
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
class SettingsDriftResult:
    sections: dict[str, list[SettingDrift | RulesetDrift]] = field(default_factory=dict)
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
    drifted: list[tuple[str, str]] = field(default_factory=list)
    fetch_errors: list[str] = field(default_factory=list)
