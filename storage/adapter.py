from pathlib import Path
from .json_store import read_json, write_json


PROPERTY_PAPERS_DEFAULT = {
    "mou_partner_agreements": [],
    "ilh_master_leases": [],
    "th_master_leases": [],
    "board_resolutions": [],
    "waiver_financial_justifications": [],
    "triple_net_leases": [],
    "program_housing_covenants": []
}


class JSONStorageAdapter:
    """
    JSON-backed storage adapter for NILPF.

    Purpose:
    - Keep current JSON behavior unchanged.
    - Centralize file access before PostgreSQL migration.
    - Let app.py call storage methods without caring where data lives later.
    """

    def __init__(self, data_dir="data"):
        self.data_dir = Path(data_dir)
        self.activation_file = self.data_dir / "activation.json"
        self.participants_file = self.data_dir / "participants.json"
        self.license_requests_file = self.data_dir / "license_requests.json"
        self.audit_log_file = self.data_dir / "audit_log.json"
        self.property_papers_file = self.data_dir / "property_papers.json"

    def get_activation(self):
        return read_json(self.activation_file, {})

    def save_activation(self, data):
        return write_json(self.activation_file, data, indent=2)

    def get_participants(self):
        return read_json(self.participants_file, [])

    def save_participants(self, data):
        return write_json(self.participants_file, data, indent=2)

    def get_license_requests(self):
        return read_json(self.license_requests_file, [])

    def save_license_requests(self, data):
        return write_json(self.license_requests_file, data, indent=2)

    def get_audit_logs(self):
        data = read_json(self.audit_log_file, [])
        return data if isinstance(data, list) else []

    def save_audit_logs(self, data):
        return write_json(self.audit_log_file, data, indent=2)

    def get_property_papers(self):
        data = read_json(self.property_papers_file, PROPERTY_PAPERS_DEFAULT.copy())

        if not isinstance(data, dict):
            data = PROPERTY_PAPERS_DEFAULT.copy()

        for key, value in PROPERTY_PAPERS_DEFAULT.items():
            data.setdefault(key, value)

        return data

    def save_property_papers(self, data):
        return write_json(self.property_papers_file, data, indent=2)


storage = JSONStorageAdapter()
