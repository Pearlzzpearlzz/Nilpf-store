# storage/postgres_adapter.py
# Phase 6B: PostgreSQL JSONB adapter for NILPF Housing OS
#
# Purpose:
# - Store the same major NILPF records currently saved as JSON files.
# - Keep the app interface identical: get_*() and save_*().
# - Use JSONB so the current data shapes do not need to be redesigned yet.
# - Allow emergency fallback/export through StorageCoordinator.

import json
import os
import logging
import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger("NILPF_Postgres")


class PostgresStorageAdapter:
    """
    PostgreSQL-backed storage adapter.

    This adapter stores each major NILPF data group as a named JSONB document.
    It is intentionally conservative so the app can migrate from JSON files
    without changing participant/form/property-paper logic yet.
    """

    def __init__(self, database_url=None):
        self.database_url = database_url or os.environ.get("DATABASE_URL", "")
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required for PostgresStorageAdapter")

        self.ensure_schema()

    def connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def ensure_schema(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS nilpf_json_store (
                        name TEXT PRIMARY KEY,
                        payload JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
            conn.commit()

    def _get_doc(self, name, default):
        try:
            with self.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT payload FROM nilpf_json_store WHERE name = %s",
                        (name,)
                    )
                    row = cur.fetchone()
                    if not row:
                        return default
                    return row["payload"]
        except Exception as exc:
            logger.exception("PostgreSQL read failed for %s: %s", name, exc)
            raise

    def _save_doc(self, name, data):
        try:
            payload = json.dumps(data)
            with self.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO nilpf_json_store (name, payload, updated_at)
                        VALUES (%s, %s::jsonb, NOW())
                        ON CONFLICT (name)
                        DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                        """,
                        (name, payload)
                    )
                conn.commit()
            return data
        except Exception as exc:
            logger.exception("PostgreSQL write failed for %s: %s", name, exc)
            raise

    def get_activation(self):
        return self._get_doc("activation", {})

    def save_activation(self, data):
        return self._save_doc("activation", data)

    def get_participants(self):
        data = self._get_doc("participants", [])
        return data if isinstance(data, list) else []

    def save_participants(self, data):
        return self._save_doc("participants", data)

    def get_license_requests(self):
        data = self._get_doc("license_requests", [])
        return data if isinstance(data, list) else []

    def save_license_requests(self, data):
        return self._save_doc("license_requests", data)

    def get_audit_logs(self):
        data = self._get_doc("audit_logs", [])
        return data if isinstance(data, list) else []

    def save_audit_logs(self, data):
        return self._save_doc("audit_logs", data)

    def get_property_papers(self):
        default = {
            "mou_partner_agreements": [],
            "ilh_master_leases": [],
            "th_master_leases": [],
            "board_resolutions": [],
            "waiver_financial_justifications": [],
            "triple_net_leases": [],
            "program_housing_covenants": []
        }
        data = self._get_doc("property_papers", default.copy())
        if not isinstance(data, dict):
            data = default.copy()
        for key, value in default.items():
            data.setdefault(key, value)
        return data

    def save_property_papers(self, data):
        return self._save_doc("property_papers", data)

    def get_shared_forms(self):
        data = self._get_doc("shared_forms", {})
        return data if isinstance(data, dict) else {}

    def save_shared_forms(self, data):
        return self._save_doc("shared_forms", data)

    def get_employee_certs(self):
        data = self._get_doc("employee_certs", [])
        return data if isinstance(data, list) else []

    def save_employee_certs(self, data):
        return self._save_doc("employee_certs", data)

    def get_paypal_webhook_events(self):
        data = self._get_doc("paypal_webhook_events", [])
        return data if isinstance(data, list) else []

    def save_paypal_webhook_events(self, data):
        return self._save_doc("paypal_webhook_events", data)

    def get_rolodex(self):
        data = self._get_doc("rolodex", [])
        return data if isinstance(data, list) else []

    def save_rolodex(self, data):
        return self._save_doc("rolodex", data)
