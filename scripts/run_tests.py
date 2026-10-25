#!/usr/bin/env python3
"""Test runner for ReconcileX.

Supports both standard `pytest`/`unittest` discovery and 9p virtualized mounts
where `os.listdir()` is disabled by the hypervisor (by installing a lightweight
`sys.meta_path` finder that checks `os.stat()` on candidate module paths).
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class StatBasedPathFinder(importlib.abc.MetaPathFinder):
    """Fallback MetaPathFinder that resolves packages/modules via os.stat() when readdir is disabled."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def find_spec(self, fullname: str, path=None, target=None):
        rel_parts = fullname.split(".")
        candidate_dir = self.root_dir.joinpath(*rel_parts)
        init_py = candidate_dir / "__init__.py"
        if self._exists(init_py):
            return importlib.util.spec_from_file_location(
                fullname,
                str(init_py),
                submodule_search_locations=[str(candidate_dir)],
            )

        candidate_py = self.root_dir.joinpath(*rel_parts[:-1], f"{rel_parts[-1]}.py")
        if self._exists(candidate_py):
            return importlib.util.spec_from_file_location(fullname, str(candidate_py))

        return None

    @staticmethod
    def _exists(p: Path) -> bool:
        try:
            os.stat(p)
            return True
        except OSError:
            return False


def install_import_hook() -> None:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    if not any(isinstance(f, StatBasedPathFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, StatBasedPathFinder(PROJECT_ROOT))


TEST_MODULES = [
    "tests.unit.test_foundation",
    "tests.unit.test_ingestion",
    "tests.unit.test_reconciliation_engine",
    "tests.unit.test_async_workers",
    "tests.workflows.test_investigation_workflow",
    "tests.workflows.test_resolution_workflow",
    "tests.integration.test_phase7_evaluation_and_hardening",
    "tests.api.test_operations_api",
    "tests.api.test_phase2_crud_and_ingestion_api",
    "tests.api.test_reconciliation_api",
    "tests.api.test_investigations_api",
    "tests.api.test_resolutions_api",
]


def main() -> int:
    install_import_hook()
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader

    for mod_name in TEST_MODULES:
        mod = importlib.import_module(mod_name)
        suite.addTests(loader.loadTestsFromModule(mod))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
