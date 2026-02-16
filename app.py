import os
import sqlite3
from datetime import datetime
import requests
from dotenv import load_dotenv

from flask import Flask, jsonify, redirect, request, send_file, abort, session, render_template_string




from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-in-render")
load_dotenv()

DOMAIN_URL = os.environ.get("DOMAIN_URL", "http://127.0.0.1:10000").rstrip("/")

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_SECRET = os.getenv("PAYPAL_SECRET")
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox")
PAYPAL_API_BASE = os.getenv("PAYPAL_API_BASE")

if PAYPAL_API_BASE:
    PAYPAL_BASE = PAYPAL_API_BASE.rstrip("/")
elif PAYPAL_MODE == "live":
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
    return redirect("/notice")




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
    order_id = request.args.get("token")
    if not order_id:
        abort(400, "Missing PayPal order token.")

    access_token = get_paypal_access_token()

    capture_resp = requests.post(
        f"{PAYPAL_BASE}/v2/checkout/orders/{order_id}/capture",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        timeout=30,
    )

    capture_resp.raise_for_status()
    capture_data = capture_resp.json()

    if capture_data.get("status") != "COMPLETED":
        abort(400, "Payment not completed.")

    location = session.get("licensed_location")
    if not location:
        abort(400, "Missing licensed location in session.")

    full_address = f"{location['street']}, {location['city']}, {location['state']} {location['zip']}"

    license_key = upsert_license(
        order_id,
        location.get("email"),
        location.get("business_name"),
        full_address,
        location.get("state"),
    )

    return redirect(f"/certificate?session_id={order_id}")

init_db()

NOTICE_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>NILPF License Notice</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
      body { font-family: Arial, sans-serif; max-width: 760px; margin: 40px auto; padding: 0 16px; }
      .box { border: 2px solid #111; border-radius: 12px; padding: 18px; }
      h1 { margin-top: 0; }
      .btn { display: inline-block; padding: 12px 16px; border-radius: 10px; text-decoration: none; border: 2px solid #111; }
      .btn:hover { opacity: 0.85; }
      .small { font-size: 14px; opacity: 0.9; }
    </style>
  </head>
  <body>
    <div class="box">
      <h1>⚠️ License & Usage Notice</h1>
      <p>This framework license is valid for <b>one physical business location only</b>.</p>
      <p>A complete physical address (Street, City, State, ZIP) is required before purchase.</p>
      <p>Each separate property/location requires its own license.</p>
      <p class="small">By continuing, you confirm you will provide the correct licensed business location address.</p>
      <p><a class="btn" href="/address">Continue</a></p>
    </div>
  </body>
</html>
"""

ADDRESS_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Enter Licensed Address</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
      body { font-family: Arial, sans-serif; max-width: 760px; margin: 40px auto; padding: 0 16px; }
      label { display:block; margin: 12px 0 6px; font-weight: bold; }
      input, select { width: 100%; padding: 10px; border-radius: 10px; border: 1px solid #999; }
      .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
      .btn { margin-top: 16px; padding: 12px 16px; border-radius: 10px; border: 2px solid #111; background: #fff; cursor:pointer; }
      .warn { background: #fff7d6; border: 1px solid #e6c200; padding: 10px; border-radius: 10px; }
      .small { font-size: 14px; opacity: 0.9; }
    </style>
  </head>
  <body>
    <div class="warn">
      <b>Required:</b> This address becomes the licensed business location for your purchase.
    </div>

    <h1>Licensed Business Location</h1>

    {% if error %}
      <p style="color:#b00020;"><b>{{ error }}</b></p>
    {% endif %}

    <form method="POST" action="/address">
      <label>Business / LLC Name *</label>
      <input name="business_name" required>

      <label>Street Address *</label>
      <input name="street" required>

      <div class="row">
        <div>
          <label>City *</label>
          <input name="city" required>
        </div>
        <div>
          <label>State *</label>
          <input name="state" required placeholder="OH">
        </div>
      </div>

      <label>ZIP Code *</label>
      <input name="zip" required>

      <label>Email (recommended)</label>
      <input name="email" type="email" placeholder="you@company.com">

      <label class="small">
        <input type="checkbox" name="confirm" value="yes" required>
        I confirm this address represents one licensed business location.
      </label>

      <button class="btn" type="submit">Continue to Purchase</button>
    </form>
  </body>
</html>
"""
@app.route("/notice")
def license_notice():

    return render_template_string(NOTICE_HTML)

@app.route("/address", methods=["GET", "POST"])
def address():
    if request.method == "GET":
        return render_template_string(ADDRESS_HTML, error=None)

    # POST: validate & store
    business_name = (request.form.get("business_name") or "").strip()
    street = (request.form.get("street") or "").strip()
    city = (request.form.get("city") or "").strip()
    state = (request.form.get("state") or "").strip().upper()
    zip_code = (request.form.get("zip") or "").strip()
    email = (request.form.get("email") or "").strip()
    confirm = request.form.get("confirm")

    if not all([business_name, street, city, state, zip_code]) or confirm != "yes":
        return render_template_string(ADDRESS_HTML, error="Please complete all required fields and confirm the checkbox.")

    session["licensed_location"] = {
        "business_name": business_name,
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_code,
        "email": email
    }

    # CHANGE THIS redirect to match your existing purchase route
    return redirect("/buy")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


