# storage/coordinator.py
# Phase 6A: Runtime Storage Coordinator for NILPF Housing OS
#
# Purpose:
# - Keep JSON as the safe current engine.
# - Prepare a clean switching point for future PostgreSQL mode.
# - Preserve emergency JSON writes if a future database engine fails.

import os
import logging
from .adapter import JSONStorageAdapter

try:
    from .postgres_adapter import PostgresStorageAdapter
except Exception:
    PostgresStorageAdapter = None

logger = logging.getLogger("NILPF_Storage")


class StorageCoordinator:
    """
    Coordinates storage reads/writes between the current JSON adapter
    and a future PostgreSQL engine.

    Current production-safe behavior:
    - JSON mode remains the working engine.
    - PostgreSQL mode is recognized but not activated yet.
    - If postgres mode is requested before implementation, it falls back to JSON.
    """

    def __init__(self, mode_override=None, data_dir="data"):
        self.mode = (mode_override or os.environ.get("STORAGE_MODE", "json")).lower()
        self.json_backup = JSONStorageAdapter(data_dir=data_dir)
        self.pg_engine = None

        if self.mode not in ("json", "postgres"):
            logger.warning("Unknown STORAGE_MODE=%s. Falling back to json.", self.mode)
            self.mode = "json"

        if self.mode == "postgres":
            if PostgresStorageAdapter is None:
                logger.warning("Postgres adapter could not be imported. Falling back to json.")
                self.mode = "json"
            else:
                try:
                    self.pg_engine = PostgresStorageAdapter()
                    logger.info("Postgres storage engine activated.")
                except Exception as exc:
                    logger.exception("Postgres storage activation failed. Falling back to json: %s", exc)
                    self.pg_engine = None
                    self.mode = "json"

    def active_engine(self):
        return self.mode

    def emergency_export(self, name, data):
        """
        Write a named emergency export back to the JSON schema used by app.py.
        """
        writers = {
            "activation": self.json_backup.save_activation,
            "participants": self.json_backup.save_participants,
            "license_requests": self.json_backup.save_license_requests,
            "audit_logs": self.json_backup.save_audit_logs,
            "property_papers": self.json_backup.save_property_papers,
            "shared_forms": self.json_backup.save_shared_forms,
            "employee_certs": self.json_backup.save_employee_certs,
            "paypal_webhook_events": self.json_backup.save_paypal_webhook_events,
            "rolodex": self.json_backup.save_rolodex,
        }

        writer = writers.get(name)
        if not writer:
            logger.error("No emergency export writer exists for: %s", name)
            return False

        try:
            writer(data)
            logger.info("Emergency JSON export completed for: %s", name)
            return True
        except Exception as exc:
            logger.critical("Emergency JSON export failed for %s: %s", name, exc)
            return False

    def get_activation(self):
        return self.json_backup.get_activation()

    def save_activation(self, data):
        return self.json_backup.save_activation(data)

    def get_participants(self):
        """
        Participants are Postgres-first.
        If Postgres is inactive/unresponsive, fall back to JSON.
        """
        if getattr(self, "pg_engine", None):
            try:
                return self.pg_engine.get_participants()
            except Exception as exc:
                logger.exception("Postgres participant read failed. Falling back to JSON: %s", exc)

        return self.json_backup.get_participants()

    def save_participants(self, data):
        """
        Participants save Postgres-first.
        JSON is backup mirror when Postgres succeeds, and emergency fallback if Postgres fails.
        """
        if getattr(self, "pg_engine", None):
            try:
                result = self.pg_engine.save_participants(data)

                # Backup mirror only. JSON is not the live world when Postgres is active.
                try:
                    self.json_backup.save_participants(data)
                except Exception as backup_exc:
                    logger.exception("JSON participant backup mirror failed: %s", backup_exc)

                return result
            except Exception as exc:
                logger.exception("Postgres participant save failed. Falling back to JSON: %s", exc)

        return self.json_backup.save_participants(data)

    def get_license_requests(self):
        return self.json_backup.get_license_requests()

    def save_license_requests(self, data):
        return self.json_backup.save_license_requests(data)

    def get_audit_logs(self):
        return self.json_backup.get_audit_logs()

    def save_audit_logs(self, data):
        return self.json_backup.save_audit_logs(data)

    def get_property_papers(self):
        return self.json_backup.get_property_papers()

    def save_property_papers(self, data):
        return self.json_backup.save_property_papers(data)

    def get_shared_forms(self):
        return self.json_backup.get_shared_forms()

    def save_shared_forms(self, data):
        return self.json_backup.save_shared_forms(data)

    def get_employee_certs(self):
        return self.json_backup.get_employee_certs()

    def save_employee_certs(self, data):
        return self.json_backup.save_employee_certs(data)

    def get_paypal_webhook_events(self):
        return self.json_backup.get_paypal_webhook_events()

    def save_paypal_webhook_events(self, data):
        return self.json_backup.save_paypal_webhook_events(data)

    def get_rolodex(self):
        return self.json_backup.get_rolodex()

    def save_rolodex(self, data):
        return self.json_backup.save_rolodex(data)


storage_coordinator = StorageCoordinator()
