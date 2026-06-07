# NILPF Housing OS — Emergency JSON Restore Guide

## Purpose

This guide explains how to manually restore NILPF Housing OS from a local JSON snapshot created by:

    python3 backup_json_snapshot.py

The snapshot script stores backups in:

    data/backups/snapshot_YYYYMMDD_HHMMSS/

## Important Safety Rule

Do not restore while the app is actively being used.

Before restoring:

1. Stop the Flask app or Render service.
2. Confirm which snapshot folder you want to restore.
3. Make one fresh backup before replacing anything.
4. Replace only the files you intend to restore.
5. Restart the app.
6. Run Mr. IR Storage Check.

## Core JSON Files

The standard backup set includes:

    activation.json
    participants.json
    license_requests.json
    audit_log.json
    property_papers.json
    employee_certifications.json
    paypal_webhook_events.json
    rolodex.json

## Step 1 — Create a Fresh Safety Backup First

From the project root:

    cd ~/NILPF_FRESH && source venv/bin/activate
    python3 backup_json_snapshot.py

This gives you a rollback point before restoring older data.

## Step 2 — List Available Snapshots

    find data/backups -maxdepth 1 -type d | sort

Choose the snapshot folder you want to restore from.

Example:

    data/backups/snapshot_20260607_110607

## Step 3 — Restore One File Manually

Example: restore participants only.

    cp data/backups/snapshot_20260607_110607/participants.json data/participants.json

## Step 4 — Restore Full Core JSON Set Manually

Only run this after confirming the snapshot folder is correct.

Replace snapshot_YYYYMMDD_HHMMSS with the real folder name.

    SNAPSHOT="data/backups/snapshot_YYYYMMDD_HHMMSS"

    cp "$SNAPSHOT/activation.json" data/activation.json
    cp "$SNAPSHOT/participants.json" data/participants.json
    cp "$SNAPSHOT/license_requests.json" data/license_requests.json
    cp "$SNAPSHOT/audit_log.json" data/audit_log.json
    cp "$SNAPSHOT/property_papers.json" data/property_papers.json
    cp "$SNAPSHOT/employee_certifications.json" data/employee_certifications.json
    cp "$SNAPSHOT/paypal_webhook_events.json" data/paypal_webhook_events.json
    cp "$SNAPSHOT/rolodex.json" data/rolodex.json

## Step 5 — Verify JSON Syntax

    python3 - <<'CHECKPY'
    import json
    from pathlib import Path

    files = [
        "data/activation.json",
        "data/participants.json",
        "data/license_requests.json",
        "data/audit_log.json",
        "data/property_papers.json",
        "data/employee_certifications.json",
        "data/paypal_webhook_events.json",
        "data/rolodex.json",
    ]

    for file in files:
        try:
            json.loads(Path(file).read_text(encoding="utf-8"))
            print(f"PASS {file}")
        except Exception as e:
            print(f"FAIL {file}: {e}")
    CHECKPY

## Step 6 — Run Mr. IR Storage Check

With the app running and logged in, open:

    /mr-ir/storage-check

Expected result:

    Storage object: StorageCoordinator
    Active engine: json

Each core file should show:

    PASS

## Step 7 — Run Mr. IR Scan

Open:

    /mr-ir/scan

Confirm Storage Check appears and passes.

## Git Safety Note

Generated backup folders under data/backups/ should remain local and should not be committed to GitHub.

Commit only:

    backup_json_snapshot.py
    RESTORE_JSON_BACKUP.md
    data/mr_ir_records.json

Do not commit live backup snapshots unless you intentionally create a sanitized demo backup.
