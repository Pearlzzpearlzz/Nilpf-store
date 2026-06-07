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

def connect():
    if DRIVER == "psycopg2":
        return psycopg2.connect(DATABASE_URL)
    return psycopg.connect(DATABASE_URL)

def table_exists(cur):
    cur.execute("""
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_name = 'nilpf_json_store'
        )
    """)
    return cur.fetchone()[0]

def get_columns(cur):
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'nilpf_json_store'
        ORDER BY ordinal_position
    """)
    return [r[0] for r in cur.fetchall()]

def execute(cur, sql, params=None):
    cur.execute(sql, params or ())

data_dir = Path("data")
if not data_dir.exists():
    print("FAIL: /app/data folder not found.")
    sys.exit(1)

json_files = sorted(data_dir.rglob("*.json"))
if not json_files:
    print("WARN: No JSON files found under /app/data.")
    sys.exit(0)

conn = connect()
cur = conn.cursor()

if not table_exists(cur):
    print("nilpf_json_store not found. Creating safe JSONB store table...")
    execute(cur, """
        CREATE TABLE nilpf_json_store (
            store_key TEXT PRIMARY KEY,
            payload JSONB NOT NULL,
            source_file TEXT,
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

for path in json_files:
    rel = path.relative_to(data_dir).as_posix()
    store_key = path.stem if "/" not in rel else rel.replace("/", ".").replace(".json", "")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"SKIP invalid JSON: {rel} ({e})")
        skipped += 1
        continue

    execute(cur, f"DELETE FROM nilpf_json_store WHERE {key_col} = %s", (store_key,))

    cols = [key_col, json_col]
    vals = [store_key, Json(payload) if DRIVER == "psycopg2" else json.dumps(payload)]

    if source_col:
        cols.append(source_col)
        vals.append(rel)

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

    print(f"SEEDED: {store_key} <= data/{rel}")
    seeded += 1

conn.commit()

execute(cur, "SELECT COUNT(*) FROM nilpf_json_store")
count = cur.fetchone()[0]

cur.close()
conn.close()

print("")
print("DONE.")
print("Seeded files:", seeded)
print("Skipped files:", skipped)
print("Rows now in nilpf_json_store:", count)
print("Backup table:", backup_table)
