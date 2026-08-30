from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills" / "goodreads-next-book"
LIB_DIR = SKILL_DIR / "lib"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The on-disk package is ``lib`` (repo convention), but so is repo-template-audit's.
# Load it under a unique name so the two do not collide in a shared ``pytest`` process.
_PKG = "gnb_lib"


def _ensure_package() -> None:
    if _PKG in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        _PKG, LIB_DIR / "__init__.py", submodule_search_locations=[str(LIB_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PKG] = module
    spec.loader.exec_module(module)


def _mod(name: str):
    _ensure_package()
    return importlib.import_module(f"{_PKG}.{name}")


@pytest.fixture
def model():
    return _mod("model")


@pytest.fixture
def fetch():
    return _mod("fetch")


@pytest.fixture
def criteria():
    return _mod("criteria")


@pytest.fixture
def render():
    return _mod("render")


@pytest.fixture
def cli():
    return _mod("cli")


@pytest.fixture
def sample_bytes() -> bytes:
    return (FIXTURES / "shelf_sample.xml").read_bytes()
