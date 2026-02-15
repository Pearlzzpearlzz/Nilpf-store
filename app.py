
import os
import sqlite3
from datetime import datetime
import requests
from dotenv import load_dotenv

from flask import Flask, jsonify, redirect, request, send_file, abort


from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter


app = Flask(__name__)
load_dotenv()

DOMAIN_URL = os.environ.get("DOMAIN_URL", "http://127.0.0.1:10000").rstrip("/")

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_SECRET = os.getenv("PAYPAL_SECRET")
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox")

if PAYPAL_MODE == "live":
    PAYPAL_BASE = "https://api-m.paypal.com"
else:
    PAYPAL_BASE = "https://api-m.sandbox.paypal.com"

# -------------------------
# Config
# -------------------------
DOMAIN_URL = os.environ.get("DOMAIN_URL" )



# -------------------------
# DB Helpers
# -------------------------
DB_PATH = "licenses.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            session_id TEXT NOT NULL UNIQUE,
            payer_email TEXT,
            payer_name TEXT,
            property_address TEXT,
            property_state TEXT,
            license_key TEXT
        )
        """
    )
    conn.commit()
    conn.close()

def make_license_key(state_abbr: str, address: str) -> str:
    # Simple deterministic-ish key seed; you can replace later with stronger logic
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    safe_state = (state_abbr or "NA").upper()[:2]
    return f"NILPF-{safe_state}-{stamp}"

def upsert_license(session_id: str, email: str, name: str, address: str, state_abbr: str) -> str:
    license_key = make_license_key(state_abbr, address)
    conn = sqlite3.connect(DB_PATH)

    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO licenses (created_at, session_id, payer_email, payer_name, property_address, property_state, license_key)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (datetime.utcnow().isoformat(), session_id, email, name, address, state_abbr, license_key),
    )
    conn.commit()
    conn.close()
    return license_key

def get_license_by_session(session_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT payer_email, payer_name, property_address, property_state, license_key, created_at FROM licenses WHERE session_id = ?",
        (session_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row

def get_paypal_access_token():
    if not PAYPAL_CLIENT_ID or not PAYPAL_SECRET:
        raise Exception("Missing PAYPAL_CLIENT_ID or PAYPAL_SECRET in environment.")

    url = f"{PAYPAL_BASE}/v1/oauth2/token"
    headers = {"Accept": "application/json", "Accept-Language": "en_US"}
    data = {"grant_type": "client_credentials"}

    r = requests.post(
        url,
        headers=headers,
        data=data,
        auth=(PAYPAL_CLIENT_ID, PAYPAL_SECRET),
        timeout=20,
    )

    r.raise_for_status()
    return r.json()["access_token"]

# -------------------------
# Routes
# -------------------------
@app.route("/health")
def health():
    return jsonify(ok=True)
@app.route("/certificate")
def certificate():
    session_id = request.args.get("session_id")
    return f"CERTIFICATE FOR {session_id}"
@app.route("/")
def home():
    return (
        "NILPF Store is running.<br><br>"
        "Start checkout: <a href='/buy'>/checkout</a>"
    )




# -------------------------
# PayPal Buy Route
# -------------------------
@app.route("/buy")
def buy():
    access_token = get_paypal_access_token()

    return_url = request.host_url.rstrip("/") + "/success"
    cancel_url = request.host_url.rstrip("/") + "/cancel"

    order_data = {
        "intent": "CAPTURE",
        "purchase_units": [
            {"amount": {"currency_code": "USD", "value": "97.00"}}
        ],
        "application_context": {
            "return_url": return_url,
            "cancel_url": cancel_url,
        },
    }

    resp = requests.post(
        f"{PAYPAL_BASE}/v2/checkout/orders",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        json=order_data,
        timeout=30,
    )

    resp.raise_for_status()
    order = resp.json()

    for link in order.get("links", []):
        if link.get("rel") == "approve":
            return redirect(link["href"])

    abort(500, "No PayPal approval link found")
@app.route("/success")

def success():
    return redirect("/certificate?session_id=TEST123&which=1")  
# -------------------------
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))



