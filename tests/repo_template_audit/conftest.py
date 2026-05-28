from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIB_DIR = ROOT / "skills" / "repo-template-audit" / "lib"
AUDIT_PATH = LIB_DIR / "audit.py"
APPLY_PATH = LIB_DIR / "apply.py"
MODELS_PATH = LIB_DIR / "models.py"
RENDER_PATH = LIB_DIR / "render.py"


def _ensure_lib_on_path():
    lib_dir = str(LIB_DIR)
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _get_or_load(name: str, path: Path):
    """Return existing module from sys.modules, or load it fresh."""
    if name in sys.modules:
        return sys.modules[name]
    return _load_module(name, path)


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
    _ensure_lib_on_path()
    return _get_or_load("models", MODELS_PATH)


@pytest.fixture
def render():
    _ensure_lib_on_path()
    return _get_or_load("render", RENDER_PATH)


@pytest.fixture
def audit():
    _ensure_lib_on_path()
    return _load_module("repo_template_audit", AUDIT_PATH)


@pytest.fixture
def apply_mod():
    _ensure_lib_on_path()
    return _load_module("repo_template_apply", APPLY_PATH)
