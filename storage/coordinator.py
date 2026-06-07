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

        if self.mode == "postgres" and self.pg_engine is None:
            logger.warning("Postgres mode requested, but pg_engine is not wired yet. Falling back to json.")
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
        return self.json_backup.get_participants()

    def save_participants(self, data):
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
