# backup_json_snapshot.py
# Phase 6E: Local JSON snapshot backup for NILPF Housing OS

from pathlib import Path
from datetime import datetime
from storage.coordinator import storage_coordinator as storage
import shutil

BACKUP_BASE = Path("data/backups")
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_dir = BACKUP_BASE / f"snapshot_{timestamp}"
backup_dir.mkdir(parents=True, exist_ok=True)

files_to_backup = {
    "activation.json": storage.get_activation,
    "participants.json": storage.get_participants,
    "license_requests.json": storage.get_license_requests,
    "audit_log.json": storage.get_audit_logs,
    "property_papers.json": storage.get_property_papers,
    "employee_certifications.json": storage.get_employee_certs,
    "paypal_webhook_events.json": storage.get_paypal_webhook_events,
    "rolodex.json": storage.get_rolodex,
}

for filename, reader in files_to_backup.items():
    try:
        data = reader()  # read current JSON
        target_path = backup_dir / filename
        with open(target_path, "w", encoding="utf-8") as f:
            import json
            json.dump(data, f, indent=2)
        print(f"[+] Backed up {filename} -> {target_path}")
    except Exception as e:
        print(f"[-] Failed to back up {filename}: {e}")

print(f"[+] Snapshot complete: {backup_dir}")
