#!/usr/bin/env python3
"""Seed script to populate ReconcileX with the complete synthetic dataset.

Creates synthetic data sources for:
1. Orders (8 orders including DY1001, DY1042, DY1043, DY1044, DY1045, DY1046, DY1047, DY1048)
2. Payment Gateway (9 payment records including TXN9001 for DY1042 and orphan TXN9004)
3. Finance Settlement Ledger (4 settled records — intentionally missing DY1042/TXN9001)
4. Refunds (2 refund records for DY1045 totaling INR 60,000 vs INR 50,000 captured)
5. Inventory Records (3 inventory records including DY1047 partial deduction)
6. Event Logs (3 failed payment webhook delivery events for DY1042 / TXN9001)
"""

from __future__ import annotations

import importlib.abc
import importlib.util
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

from app.db.session import DatabaseSession, session_scope
from app.models.enums import SourceType, TargetEntity
from app.repositories.sources_repo import SourcesRepository
from app.schemas.domain import DataSourceCreate
from app.services.ingestion.adapters import IngestionService


SYNTHETIC_SOURCES = [
    ("doyuf_synthetic_orders", TargetEntity.ORDERS),
    ("doyuf_synthetic_payment_gateway", TargetEntity.PAYMENTS),
    ("doyuf_synthetic_finance_ledger", TargetEntity.FINANCE_LEDGER),
    ("doyuf_synthetic_refunds", TargetEntity.REFUNDS),
    ("doyuf_synthetic_inventory", TargetEntity.INVENTORY),
    ("doyuf_synthetic_event_logs", TargetEntity.EVENT_LOGS),
]


def seed_all_synthetic_data(session: DatabaseSession) -> dict[str, int]:
    """Register synthetic sources and import all synthetic records idempotently."""
    sources_repo = SourcesRepository(session)
    ingestion_service = IngestionService(session)
    summary: dict[str, int] = {}

    for source_name, target_entity in SYNTHETIC_SOURCES:
        existing = sources_repo.get_source_by_name(source_name)
        if existing is None:
            existing = sources_repo.create_source(
                DataSourceCreate(
                    name=source_name,
                    source_type=SourceType.SYNTHETIC,
                    target_entity=target_entity,
                    connection_config={"preset": "doyuf_full_suite"},
                )
            )
        report = ingestion_service.import_source_data(existing.source_id)
        summary[source_name] = report.imported_rows

    return summary


def main() -> int:
    with session_scope() as session:
        counts = seed_all_synthetic_data(session)
    print("Seeded synthetic dataset successfully:")
    for k, v in counts.items():
        print(f"  - {k}: {v} rows imported")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
