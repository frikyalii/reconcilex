#!/usr/bin/env python3
"""Interactive end-to-end demonstration of the ReconcileX MVP (Scenario DY1042).

Runs the full lifecycle in one command:
1. Seeds all synthetic data sources (Orders, Payment Gateway, Finance Ledger, Refunds, Inventory, Event Logs).
2. Runs the deterministic ReconciliationEngine and prints all detected discrepancies.
3. Selects Scenario DY1042 (Order DY1042 / Payment TXN9001 missing from finance_ledger).
4. Runs the LangGraph Discrepancy Investigation workflow and prints cited evidence & candidate root causes.
5. Approves the proposed resolution (`retry_webhook_sync`) as an authorized finance operator.
6. Executes the simulated recovery action and triggers a post-resolution verification reconciliation run.
7. Prints the final verified state and audit trail.
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

from app.core.config import Settings
from app.db.session import create_session_for_settings, reset_in_memory_databases
from app.repositories.audit_repo import AuditRepository
from app.repositories.reconciliation_repo import ReconciliationRepository
from app.services.investigation.service import InvestigationService
from app.services.reconciliation.engine import ReconciliationEngine
from app.services.resolution.service import ResolutionService
from scripts.seed_synthetic import seed_all_synthetic_data


def main() -> int:
    reset_in_memory_databases()
    settings = Settings(DATABASE_URL="sqlite:///:memory:")
    session = create_session_for_settings(settings)

    print("======================================================================")
    print(" ReconcileX — End-to-End Walkthrough (Scenario DY1042)")
    print("======================================================================")

    # Step 1: Seed synthetic dataset
    counts = seed_all_synthetic_data(session)
    print("\n[Step 1] Seeded Synthetic Sources:")
    for source_name, row_count in counts.items():
        print(f"  - {source_name}: {row_count} records")

    # Step 2: Run deterministic reconciliation
    engine = ReconciliationEngine(session)
    run, findings = engine.execute_run(triggered_by="demo_operator")
    print(f"\n[Step 2] Deterministic Reconciliation Run Completed (run_id={run.run_id})")
    print(f"  - Status: {run.status.value}")
    print(f"  - Snapshot SHA-256: {run.source_snapshot['snapshot_sha256'][:16]}...")
    print(f"  - Total Discrepancies Detected: {len(findings)}")
    for idx, f in enumerate(findings, start=1):
        print(
            f"    {idx}. [{f.severity.value.upper()}] {f.rule_code} | "
            f"order={f.order_id} | txn={f.transaction_id} | {f.description}"
        )

    # Step 3: Isolate DY1042 missing finance settlement
    dy1042_disc = next(
        f
        for f in findings
        if f.order_id == "DY1042"
        and f.rule_code == "RULE_MISSING_FINANCE_SETTLEMENT"
    )
    print(f"\n[Step 3] Selected Discrepancy for Investigation: {dy1042_disc.discrepancy_id}")
    print(f"  - Rule: {dy1042_disc.rule_code}")
    print(f"  - Expected: {dy1042_disc.expected_value}")
    print(f"  - Actual: {dy1042_disc.actual_value}")

    # Step 4: Run LangGraph investigation workflow
    inv_service = InvestigationService(session)
    investigation, proposed_res = inv_service.run_investigation(
        dy1042_disc.discrepancy_id,
        actor_id="finance-lead-01",
        actor_role="operator",
    )
    print(f"\n[Step 4] LangGraph Investigation Completed (investigation_id={investigation.investigation_id})")
    print(f"  - Status: {investigation.status.value} (validation_passed={investigation.validation_passed})")
    print(f"  - Confidence Score: {investigation.confidence_score}")
    print(f"  - Cited Evidence IDs: {investigation.evidence_references}")
    print(f"  - Root-Cause Hypothesis: {investigation.candidate_causes[0]['hypothesis']}")
    assert proposed_res is not None
    print(
        f"  - Proposed Resolution Action: {proposed_res.action_type.value} "
        f"(resolution_id={proposed_res.resolution_id}, status={proposed_res.status.value})"
    )

    # Step 5: Human operator approves the proposed resolution
    res_service = ResolutionService(session)
    approved_res = res_service.approve_resolution(
        resolution_id=proposed_res.resolution_id,
        actor_id="finance-lead-01",
        actor_role="operator",
        comment="Verified 3 failed webhook delivery logs in DLQ; approve replay.",
    )
    print(f"\n[Step 5] Operator Approved Resolution: status={approved_res.status.value}")

    # Step 6: Execute simulated recovery & run post-resolution verification
    verified_res = res_service.execute_resolution(
        resolution_id=proposed_res.resolution_id,
        actor_id="finance-lead-01",
        actor_role="operator",
    )
    recon_repo = ReconciliationRepository(session)
    updated_disc = recon_repo.get_discrepancy(dy1042_disc.discrepancy_id)
    assert updated_disc is not None

    print(f"\n[Step 6] Executed Simulated Recovery & Post-Resolution Verification:")
    print(f"  - Resolution Status: {verified_res.status.value}")
    print(f"  - Created Finance Ledger Entry: {verified_res.execution_result.get('created_finance_ledger_transaction_id')}")
    print(f"  - Verification Run ID: {verified_res.verification_run_id}")
    print(f"  - Discrepancy Status After Verification: {updated_disc.status.value}")

    # Step 7: Print audit trail
    audit_repo = AuditRepository(session)
    audits = audit_repo.list_events_for_entity(
        "resolution_action", proposed_res.resolution_id
    )
    print("\n[Step 7] Immutable Audit Trail for Resolution:")
    for ev in audits:
        print(
            f"  - {ev.created_at} | action={ev.action} | actor={ev.actor_id} ({ev.actor_role})"
        )

    print("\n======================================================================")
    print(" End-to-End Walkthrough Completed Successfully!")
    print("======================================================================")
    reset_in_memory_databases()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
