from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills" / "repo-template-audit"
LIB_DIR = SKILL_DIR / "lib"
APPLY_PATH = LIB_DIR / "apply.py"


def _ensure_package():
    skill_dir = str(SKILL_DIR)
    if skill_dir not in sys.path:
        sys.path.insert(0, skill_dir)
    if "lib" not in sys.modules:
        importlib.import_module("lib")


@pytest.fixture
def root_dir():
    return ROOT


@pytest.fixture
def lib_dir():
    return LIB_DIR


@pytest.fixture
def apply_path():
    return APPLY_PATH


@pytest.fixture
def models():
    _ensure_package()
    return importlib.import_module("lib.models")


@pytest.fixture
def render():
    _ensure_package()
    return importlib.import_module("lib.render")


@pytest.fixture
def audit():
    _ensure_package()
    mod = importlib.import_module("lib.audit")
    importlib.reload(mod)
    return mod


@pytest.fixture
def apply_mod():
    _ensure_package()
    mod = importlib.import_module("lib.apply")
    importlib.reload(mod)
    return mod
