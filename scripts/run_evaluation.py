#!/usr/bin/env python3
"""Run the ReconcileX empirical evaluation suite and print measured benchmark metrics."""

from __future__ import annotations

import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _StatBasedPathFinder(importlib.abc.MetaPathFinder):
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def find_spec(self, fullname: str, path=None, target=None):
        rel_parts = fullname.split(".")
        candidate_dir = self.root_dir.joinpath(*rel_parts)
        init_py = candidate_dir / "__init__.py"
        try:
            os.stat(init_py)
            return importlib.util.spec_from_file_location(
                fullname, str(init_py), submodule_search_locations=[str(candidate_dir)]
            )
        except OSError:
            pass
        candidate_py = self.root_dir.joinpath(*rel_parts[:-1], f"{rel_parts[-1]}.py")
        try:
            os.stat(candidate_py)
            return importlib.util.spec_from_file_location(fullname, str(candidate_py))
        except OSError:
            return None


if not any(f.__class__.__name__.endswith("StatBasedPathFinder") for f in sys.meta_path):
    sys.meta_path.insert(0, _StatBasedPathFinder(PROJECT_ROOT))

from app.observability.evaluation import run_evaluation_benchmark


def main() -> int:
    report = run_evaluation_benchmark()
    print(json.dumps(report.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
