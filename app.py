import os
import sqlite3
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()
from flask import Flask, jsonify, redirect, request, send_file, abort

import stripe

# PDF certificate
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter


app = Flask(__name__)

# -------------------------
# Config
# -------------------------
DOMAIN_URL = os.environ.get("DOMAIN_URL", "http://127.0.0.1:10000").rstrip("/")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")  # set in Render + local env if needed
PRICE_ID = os.environ.get("PRICE_ID")  # Stripe Price ID for your product

stripe.api_key = STRIPE_SECRET_KEY


# --------------------

import os
import sqlite3
from datetime import datetime

from flask import Flask, jsonify, redirect, request, send_file, abort

import stripe

# PDF certificate
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter


app = Flask(__name__)

# -------------------------
# Config
# -------------------------
DOMAIN_URL = os.environ.get("DOMAIN_URL", "http://127.0.0.1:10000").rstrip("/")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")  # set in Render + local env if needed
PRICE_ID = os.environ.get("PRICE_ID")  # Stripe Price ID for your product

stripe.api_key = STRIPE_SECRET_KEY


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


# -------------------------
# Routes
# -------------------------
@app.route("/health")
def health():
    return jsonify(ok=True)

@app.route("/")
def home():
    return (
        "NILPF Store is running.<br><br>"
        "Start checkout: <a href='/checkout'>/checkout</a>"
    )

@app.route("/checkout")
def checkout():
    if not STRIPE_SECRET_KEY or not PRICE_ID:
        return (
            "Missing STRIPE_SECRET_KEY or PRICE_ID env vars. "
            "Set them locally and in Render Environment.",
            500,
        )

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": PRICE_ID, "quantity": 1}],
        billing_address_collection="required",
        customer_creation="always",
        success_url=f"{DOMAIN_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{DOMAIN_URL}/cancel",
    )
    return redirect(session.url, code=303)

@app.route("/cancel")
def cancel():
    return "Payment canceled."

@app.route("/success")
def success():
    session_id = request.args.get("session_id")
    if not session_id:
        return "Missing session_id", 400

    # Verify with Stripe server-side
    session = stripe.checkout.Session.retrieve(
        session_id,
        expand=["customer_details"],
    )
    if session.get("payment_status") != "paid":
        return "Payment not verified.", 403

    customer_details = session.get("customer_details") or {}
    email = customer_details.get("email", "")
    name = customer_details.get("name", "")

    # Build a single-line property address
    addr = customer_details.get("address") or {}
    line1 = addr.get("line1") or ""
    line2 = addr.get("line2") or ""
    city = addr.get("city") or ""
    state = addr.get("state") or ""
    postal = addr.get("postal_code") or ""
    country = addr.get("country") or ""

    parts = [line1]
    if line2:
        parts.append(line2)
    parts.append(", ".join([p for p in [city, state, postal] if p]))
    if country:
        parts.append(country)
    property_address = " | ".join([p for p in parts if p]).strip()

    license_key = upsert_license(session_id, email, name, property_address, state)

    cert_link_1 = f"/certificate?session_id={session_id}&which=1"
    cert_link_2 = f"/certificate?session_id={session_id}&which=2"

    return f"""
    <h2>Payment verified ✅</h2>
    <p><b>License Key:</b> {license_key}</p>
    <p><b>Property Address:</b> {property_address}</p>

    <h3>Certificates (per property)</h3>
    <p><a href="{cert_link_1}">Download Certificate (Property 1)</a></p>
    <p><a href="{cert_link_2}">Download Certificate (Property 2)</a></p>
    """

@app.route("/certificate")
def certificate():
    session_id = request.args.get("session_id")
    which = request.args.get("which", "1")

    if not session_id:
        return "Missing session_id", 400

    # Verify with Stripe again (prevents sharing links)
    session = stripe.checkout.Session.retrieve(session_id)
    if session.get("payment_status") != "paid":
        return "Payment not verified.", 403

    row = get_license_by_session(session_id)
    if not row:
        return "License record not found.", 404

    payer_email, payer_name, property_address, property_state, license_key, created_at = row

    # Create PDF
    filename = f"certificate_property_{which}.pdf"
    pdf_path = f"/tmp/{filename}"

    c = canvas.Canvas(pdf_path, pagesize=letter)
    width, height = letter

    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, height - 72, "NILPF License Certificate")

    c.setFont("Helvetica", 12)
    y = height - 120
    c.drawString(72, y, f"Issued To: {payer_name}")
    y -= 18
    c.drawString(72, y, f"Email: {payer_email}")
    y -= 18
    c.drawString(72, y, f"License Key: {license_key}")
    y -= 18
    c.drawString(72, y, f"Property #{which}")
    y -= 18
    c.drawString(72, y, f"Property Address: {property_address}")
    y -= 18
    c.drawString(72, y, f"State: {property_state}")
    y -= 18
    c.drawString(72, y, f"Issued (UTC): {created_at}")

    y -= 30
    c.drawString(72, y, "License verification available upon request.")
    y -= 18
    c.drawString(72, y, "Published by Pearlzz")

    c.showPage()
    c.save()

    return send_file(
        pdf_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )


# -------------------------
# Startup
# -------------------------
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
