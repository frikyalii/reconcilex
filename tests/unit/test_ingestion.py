"""Unit tests for Phase 2: repositories, CSV/Postgres/Synthetic source adapters, schema mapping, and row-level error reporting."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unittest

from app.core.config import Settings
from app.db.session import create_session_for_settings, reset_in_memory_databases
from app.models.enums import SourceType, TargetEntity
from app.repositories.audit_repo import AuditRepository
from app.repositories.records_repo import RecordsRepository
from app.repositories.sources_repo import SourcesRepository
from app.schemas.domain import DataSourceCreate, SchemaMappingInline, SourceImportRequest
from app.services.ingestion.adapters import IngestionService
from scripts.seed_synthetic import seed_all_synthetic_data


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestIngestionAndAdaptersUnit(unittest.TestCase):
    """Verify CSV, Postgres, and Synthetic adapters, schema mappings, and row-level error reports."""

    def setUp(self) -> None:
        reset_in_memory_databases()
        self.settings = Settings(DATABASE_URL="sqlite:///:memory:")
        self.session = create_session_for_settings(self.settings)
        self.sources_repo = SourcesRepository(self.session)
        self.records_repo = RecordsRepository(self.session)
        self.audit_repo = AuditRepository(self.session)
        self.ingestion_service = IngestionService(self.session)

    def tearDown(self) -> None:
        reset_in_memory_databases()

    def test_csv_adapter_with_schema_mapping_and_row_level_errors(self) -> None:
        """Invalid CSV rows must produce explicit row-level error reports without discarding valid rows."""
        csv_file = FIXTURES_DIR / "orders_sample.csv"
        source = self.sources_repo.create_source(
            DataSourceCreate(
                name="csv_orders_mapped",
                source_type=SourceType.CSV,
                target_entity=TargetEntity.ORDERS,
                connection_config={"file_path": str(csv_file), "delimiter": ","},
                schema_mapping=SchemaMappingInline(
                    field_mappings={
                        "source_record_id": "oms_rec_id",
                        "order_id": "doyuf_order_no",
                        "customer_id": "cust_ref",
                        "expected_amount": "order_total",
                        "currency": "curr_code",
                        "status": "order_state",
                        "event_timestamp": "created_ts",
                        "updated_timestamp": "updated_ts",
                    },
                    required_fields=[
                        "source_record_id",
                        "order_id",
                        "expected_amount",
                        "currency",
                        "status",
                        "event_timestamp",
                        "updated_timestamp",
                    ],
                    validation_rules={"allowed_currencies": ["INR", "USD"]},
                ),
            )
        )

        # 1. Validate first without persisting
        val_report = self.ingestion_service.validate_source_data(source.source_id)
        self.assertEqual(val_report.total_rows, 5)
        self.assertEqual(val_report.valid_rows, 2)
        self.assertEqual(val_report.invalid_rows, 3)
        self.assertFalse(val_report.is_valid)

        error_rows = {err.row_number: err for err in val_report.row_errors}
        self.assertIn(3, error_rows)
        self.assertEqual(error_rows[3].error_code, "MISSING_REQUIRED_FIELD")
        self.assertEqual(error_rows[3].field, "order_id")

        self.assertIn(4, error_rows)
        self.assertEqual(error_rows[4].error_code, "SCHEMA_VALIDATION_FAILED")

        self.assertIn(5, error_rows)
        self.assertEqual(error_rows[5].error_code, "SCHEMA_VALIDATION_FAILED")

        # Confirm nothing persisted during validation
        self.assertEqual(len(self.records_repo.list_orders()), 0)

        # 2. Import valid rows and verify audit trail + duplicate detection on re-import
        imp_report = self.ingestion_service.import_source_data(
            source.source_id, actor_id="op-1", actor_role="operator"
        )
        self.assertEqual(imp_report.total_rows, 5)
        self.assertEqual(imp_report.imported_rows, 2)
        self.assertEqual(imp_report.invalid_rows, 3)
        self.assertEqual(imp_report.imported_ids, ["DY2001", "DY2002"])

        dy2002 = self.records_repo.get_order("DY2002")
        self.assertIsNotNone(dy2002)
        assert dy2002 is not None
        self.assertEqual(dy2002.expected_amount, Decimal("142500.5000"))

        # Re-importing the exact same CSV flags the 2 already-imported rows as DUPLICATE_ENTITY
        re_imp_report = self.ingestion_service.import_source_data(source.source_id)
        self.assertEqual(re_imp_report.imported_rows, 0)
        self.assertEqual(re_imp_report.skipped_duplicate_rows, 2)
        self.assertEqual(re_imp_report.invalid_rows, 5)

        audits = self.audit_repo.list_events_for_entity("data_source", source.source_id)
        self.assertEqual(len(audits), 2)
        self.assertEqual(audits[0].action, "source_import_completed")

    def test_postgres_staging_table_adapter(self) -> None:
        """PostgresTableAdapter reads rows from a whitelisted relational staging table."""
        self.session.execute(
            """
            CREATE TABLE staging_orders (
                ext_id TEXT,
                ord_code TEXT,
                amt TEXT,
                curr TEXT,
                st TEXT,
                ev_ts TEXT,
                up_ts TEXT
            );
            """
        )
        self.session.execute(
            """
            INSERT INTO staging_orders VALUES
            ('STG-1', 'DY3001', '64000.0000', 'INR', 'confirmed', '2026-10-24T05:00:00Z', '2026-10-24T05:00:00Z');
            """
        )
        self.session.commit()

        source = self.sources_repo.create_source(
            DataSourceCreate(
                name="pg_staging_orders",
                source_type=SourceType.POSTGRES,
                target_entity=TargetEntity.ORDERS,
                connection_config={"table_name": "staging_orders", "batch_size": 500},
                schema_mapping=SchemaMappingInline(
                    field_mappings={
                        "source_record_id": "ext_id",
                        "order_id": "ord_code",
                        "expected_amount": "amt",
                        "currency": "curr",
                        "status": "st",
                        "event_timestamp": "ev_ts",
                        "updated_timestamp": "up_ts",
                    },
                    required_fields=["source_record_id", "order_id", "expected_amount"],
                ),
            )
        )
        report = self.ingestion_service.import_source_data(source.source_id)
        self.assertEqual(report.imported_rows, 1)
        self.assertIsNotNone(self.records_repo.get_order("DY3001"))

    def test_synthetic_dataset_seeding_dy1042_and_all_scenarios(self) -> None:
        """Verify synthetic dataset seeding populates DY1042 and all edge-case scenarios."""
        counts = seed_all_synthetic_data(self.session)
        self.assertEqual(counts["doyuf_synthetic_orders"], 8)
        self.assertEqual(counts["doyuf_synthetic_payment_gateway"], 9)
        self.assertEqual(counts["doyuf_synthetic_finance_ledger"], 4)
        self.assertEqual(counts["doyuf_synthetic_refunds"], 2)
        self.assertEqual(counts["doyuf_synthetic_inventory"], 3)
        self.assertEqual(counts["doyuf_synthetic_event_logs"], 3)

        # Verify DY1042 scenario specifically:
        # 1. Order DY1042 is confirmed for INR 80000.0000
        dy1042 = self.records_repo.get_order("DY1042")
        self.assertIsNotNone(dy1042)
        assert dy1042 is not None
        self.assertEqual(dy1042.expected_amount, Decimal("80000.0000"))

        # 2. Gateway payment TXN9001 captured INR 80000.0000
        gw_payments = self.records_repo.list_payments(
            order_id="DY1042", source_system="payment_gateway"
        )
        self.assertEqual(len(gw_payments), 1)
        self.assertEqual(gw_payments[0].transaction_id, "TXN9001")
        self.assertEqual(gw_payments[0].captured_amount, Decimal("80000.0000"))

        # 3. Finance ledger has NO matching entry for DY1042
        ledger_payments = self.records_repo.list_payments(
            order_id="DY1042", source_system="finance_ledger"
        )
        self.assertEqual(len(ledger_payments), 0)

        # 4. Event log has 3 failed webhook delivery events for DY1042 / TXN9001
        logs = self.records_repo.list_event_logs(order_id="DY1042")
        self.assertEqual(len(logs), 3)
        self.assertEqual(logs[-1].error_code, "WEBHOOK_MAX_RETRIES_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
