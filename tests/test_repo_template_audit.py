from __future__ import annotations

import importlib.util
from pathlib import Path


def load_tests(loader, _standard_tests, pattern):
    test_path = Path(__file__).parent / "skills" / "repo-template-audit" / "test_repo_template_audit.py"
    spec = importlib.util.spec_from_file_location("repo_template_audit_tests", test_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return loader.loadTestsFromModule(module)
