"""
One-time controlled migration:
data/participants.json -> PostgreSQL nilpf_json_store participants document.

Safety:
- Requires DATABASE_URL.
- Validates JSON structure.
- Refuses to overwrite non-empty PostgreSQL participant data.
- Verifies count and exact data equality after migration.
- Leaves participants.json unchanged.
"""

import json
import os
from pathlib import Path

from storage.postgres_adapter import PostgresStorageAdapter


def stop(message):
    print(f"STOP: {message}")
    raise SystemExit(1)


database_url = os.environ.get("DATABASE_URL", "")
if not database_url:
    stop("DATABASE_URL is not loaded.")

json_path = Path("data/participants.json")
if not json_path.exists():
    stop(f"{json_path} does not exist.")

try:
    json_participants = json.loads(json_path.read_text())
except Exception as exc:
    stop(f"Could not read participants JSON: {exc}")

if not isinstance(json_participants, list):
    stop("data/participants.json does not contain a list.")

json_count = len(json_participants)
print("JSON participant count:", json_count)

if json_count == 0:
    stop("JSON participant list is empty. Nothing will be migrated.")

postgres = PostgresStorageAdapter(database_url=database_url)
existing_postgres = postgres.get_participants()

if not isinstance(existing_postgres, list):
    stop("Existing PostgreSQL participant document is not a list.")

postgres_before_count = len(existing_postgres)
print("PostgreSQL participant count before migration:", postgres_before_count)

if postgres_before_count > 0:
    stop(
        "PostgreSQL already contains participant records. "
        "Migration will not overwrite them."
    )

postgres.save_participants(json_participants)

verified = postgres.get_participants()
postgres_after_count = len(verified) if isinstance(verified, list) else -1

print("PostgreSQL participant count after migration:", postgres_after_count)

if postgres_after_count != json_count:
    stop(
        f"Verification failed: JSON has {json_count}, "
        f"PostgreSQL has {postgres_after_count}."
    )

if verified != json_participants:
    stop("Verification failed: PostgreSQL data does not exactly match JSON.")

print("PASS: Participant migration completed and verified.")
