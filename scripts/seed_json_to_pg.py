import os, json, sys
from pathlib import Path
from datetime import datetime, timezone

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("FAIL: DATABASE_URL is not set in Render environment.")
    sys.exit(1)

try:
    import psycopg2
    from psycopg2.extras import Json
    DRIVER = "psycopg2"
except Exception:
    try:
        import psycopg
        DRIVER = "psycopg"
    except Exception:
        print("FAIL: No PostgreSQL driver found. Install psycopg2-binary or psycopg.")
        sys.exit(1)

EXPECTED_DEFAULTS = {
    "activation": {},
    "participants": [],
    "license_requests": [],
    "audit_log": [],
    "property_papers": {},
    "employee_certifications": [],
    "paypal_webhook_events": [],
    "rolodex": [],
    "shared_forms": {},
    "mr_ir_records": [],
}

def connect():
    if DRIVER == "psycopg2":
        return psycopg2.connect(DATABASE_URL)
    return psycopg.connect(DATABASE_URL)

def execute(cur, sql, params=None):
    cur.execute(sql, params or ())

def table_exists(cur):
    execute(cur, """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_name = 'nilpf_json_store'
        )
    """)
    return cur.fetchone()[0]

def get_columns(cur):
    execute(cur, """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'nilpf_json_store'
        ORDER BY ordinal_position
    """)
    return [r[0] for r in cur.fetchall()]

def upsert_record(cur, key_col, json_col, source_col, updated_col, store_key, payload, source_file):
    execute(cur, f"DELETE FROM nilpf_json_store WHERE {key_col} = %s", (store_key,))

    cols = [key_col, json_col]
    vals = [store_key, Json(payload) if DRIVER == "psycopg2" else json.dumps(payload)]

    if source_col:
        cols.append(source_col)
        vals.append(source_file)

    if updated_col:
        cols.append(updated_col)
        vals.append(datetime.now(timezone.utc))

    placeholders = ", ".join(["%s"] * len(vals))
    col_sql = ", ".join(cols)

    execute(
        cur,
        f"INSERT INTO nilpf_json_store ({col_sql}) VALUES ({placeholders})",
        tuple(vals)
    )

data_dir = Path("data")
if not data_dir.exists():
    print("WARN: /app/data folder not found. Will seed expected defaults only.")
    json_files = []
else:
    json_files = sorted(data_dir.rglob("*.json"))

print("JSON files found under data/:", len(json_files))
for p in json_files:
    print("FOUND:", p.as_posix())

conn = connect()
cur = conn.cursor()

if not table_exists(cur):
    print("nilpf_json_store not found. Creating safe JSONB store table...")
    execute(cur, """
        CREATE TABLE nilpf_json_store (
            name TEXT PRIMARY KEY,
            payload JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.commit()

columns = get_columns(cur)
print("Detected nilpf_json_store columns:", columns)

key_candidates = ["store_key", "key", "record_key", "name", "filename"]
json_candidates = ["payload", "data", "json_data", "value", "content", "record_data"]

key_col = next((c for c in key_candidates if c in columns), None)
json_col = next((c for c in json_candidates if c in columns), None)

if not key_col or not json_col:
    print("FAIL: Could not identify key/json columns in nilpf_json_store.")
    print("Columns found:", columns)
    sys.exit(1)

source_col = "source_file" if "source_file" in columns else None
updated_col = "updated_at" if "updated_at" in columns else None

backup_table = "nilpf_json_store_backup_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
execute(cur, f"CREATE TABLE {backup_table} AS TABLE nilpf_json_store")
conn.commit()
print("Backup created:", backup_table)

seeded = 0
skipped = 0
seen_keys = set()

for path in json_files:
    rel = path.relative_to(data_dir).as_posix()
    store_key = path.stem if "/" not in rel else rel.replace("/", ".").replace(".json", "")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"SKIP invalid JSON: {rel} ({e})")
        skipped += 1
        continue

    upsert_record(cur, key_col, json_col, source_col, updated_col, store_key, payload, f"data/{rel}")
    seen_keys.add(store_key)

    print(f"SEEDED FILE: {store_key} <= data/{rel}")
    seeded += 1

defaulted = 0

for store_key, payload in EXPECTED_DEFAULTS.items():
    if store_key in seen_keys:
        continue

    upsert_record(cur, key_col, json_col, source_col, updated_col, store_key, payload, "DEFAULT_EMPTY_RECORD")
    print(f"SEEDED DEFAULT: {store_key}")
    defaulted += 1

conn.commit()

execute(cur, f"SELECT {key_col} FROM nilpf_json_store ORDER BY {key_col}")
keys = [r[0] for r in cur.fetchall()]

cur.close()
conn.close()

print("")
print("DONE.")
print("Seeded JSON files:", seeded)
print("Seeded safe defaults:", defaulted)
print("Skipped files:", skipped)
print("Total rows now in nilpf_json_store:", len(keys))
print("Backup table:", backup_table)
print("")
print("Current store keys:")
for k in keys:
    print("-", k)
