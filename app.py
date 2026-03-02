
import os
import sqlite3
from datetime import datetime
import requests
import io
import zipfile
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv(override=True)
from flask import Flask, jsonify, redirect, request, send_file, abort, session, render_template_string, url_for
from io import BytesIO

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret")






from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch

def _safe_redirect():
    path = (request.path or "").lower()

    if path.startswith("/buy") or path.startswith("/success") or path.startswith("/cancel"):
        return redirect("/address")

    if path.startswith("/product"):
        return redirect("/buy")

    return redirect("/")

@app.errorhandler(400)
def handle_400(e):
    return _safe_redirect()

@app.errorhandler(404)
def handle_404(e):
    return _safe_redirect()

@app.errorhandler(500)
def handle_500(e):
    return _safe_redirect()

# ------------------------------------------------
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
# DB Helpers
# -------------------------
DB_PATH = "licenses.db"

def ensure_db_columns():
    """Lightweight migration so existing licenses.db doesn't break."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(licenses)")
    cols = {row[1] for row in cur.fetchall()}
    if "product_sku" not in cols:
        cur.execute("ALTER TABLE licenses ADD COLUMN product_sku TEXT")
    if "transaction_id" not in cols:
        cur.execute("ALTER TABLE licenses ADD COLUMN transaction_id TEXT")
    conn.commit()
    conn.close()

# Product catalog (edit prices + file paths as you wish)

PRODUCTS = {
    "COMPLETE_SET": {
        "label": "NILPF Complete Authority Set",
        "price": "297.00",
        "file": "downloads/ALL_BUNDLE.zip",
    }
}

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
            license_key TEXT,
            product_sku TEXT
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

def upsert_license(session_id: str, email: str, name: str, address: str, state_abbr: str, product_sku: str = None) -> str:
    license_key = make_license_key(state_abbr, address)
    conn = sqlite3.connect(DB_PATH)

    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO licenses (created_at, session_id, payer_email, payer_name, property_address, property_state, license_key, product_sku)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (datetime.utcnow().isoformat(), session_id, email, name, address, state_abbr, license_key, product_sku),
    )
    conn.commit()
    conn.close()
    return license_key

def get_license_by_session(session_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT payer_email, payer_name, property_address, property_state, license_key, created_at, product_sku FROM licenses WHERE session_id = ?",
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
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from flask import Response
import io

@app.route("/certificate")
def certificate():
    session_id = request.args.get("session_id")
    if not session_id:
        abort(400, "Missing session_id.")

    lic = get_license_by_session(session_id)
    if not lic:
        abort(404, "License not found.")

    payer_email, payer_name, prop_addr, prop_state, license_key, created_at, product_sku = lic

    # Business Name priority:
    business_name = (payer_name or "").strip() or "NILPF Registered Operator"
    licensed_address = (prop_addr or "").strip() or "Address on file"
    state_text = (prop_state or "").strip() or "NA"
    license_id = (license_key or "").strip() or "NILPF-NA-UNKNOWN"
    issued_raw = (created_at or "").strip()

    # Friendly date (best-effort)
    issued_display = issued_raw
    try:
        # created_at stored as ISO string; trim microseconds if present
        # Example: 2026-03-02T13:44:21.773103
        dt = issued_raw.replace("Z","")
        issued_display = dt.split(".")[0].replace("T", " ")
    except Exception:
        issued_display = issued_raw or "Unknown"

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    # -------------------------
    # Simple "parchment" style layout (no external images required)
    # -------------------------
    margin = 0.6 * inch

    # Border
    c.setLineWidth(2)
    c.rect(margin, margin, width - 2*margin, height - 2*margin)

    # Title
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(width/2, height - 1.25*inch, "NILPF CERTIFICATE OF REGISTRATION")

    c.setFont("Helvetica", 13)
    c.drawCentredString(width/2, height - 1.55*inch, "National Independent Living Program Framework (NILPF)")

    # Core statement
    y = height - 2.25*inch
    c.setFont("Helvetica-Oblique", 12)
    c.drawCentredString(width/2, y, "This certifies that")
    y -= 0.45*inch

    # Business name
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width/2, y, business_name)
    y -= 0.40*inch

    c.setFont("Helvetica-Oblique", 12)
    c.drawCentredString(width/2, y, "is registered for the licensed business location:")
    y -= 0.35*inch

    # Address
    c.setFont("Helvetica", 12)
    # Split address into multiple lines if long
    addr_lines = []
    addr = licensed_address
    if len(addr) > 62:
        # naive wrap
        while len(addr) > 62:
            cut = addr.rfind(" ", 0, 62)
            if cut == -1:
                cut = 62
            addr_lines.append(addr[:cut].strip())
            addr = addr[cut:].strip()
        if addr:
            addr_lines.append(addr)
    else:
        addr_lines = [addr]

    for line in addr_lines[:3]:
        c.drawCentredString(width/2, y, line)
        y -= 0.22*inch

    # Descriptive paragraph
    y -= 0.10*inch
    c.setFont("Helvetica", 11)
    para = ("Operating in alignment with dignity-centered standards of autonomy, structural clarity, "
            "and sustainable housing governance. This registration is site-specific and non-transferable.")
    # simple wrap
    words = para.split()
    lines = []
    line = []
    for w in words:
        test = (" ".join(line + [w]))
        if len(test) > 90:
            lines.append(" ".join(line))
            line = [w]
        else:
            line.append(w)
    if line:
        lines.append(" ".join(line))

    for pline in lines[:4]:
        c.drawString(margin + 0.35*inch, y, pline)
        y -= 0.20*inch

    # Footer details
    y = margin + 1.70*inch
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin + 0.35*inch, y, f"License ID:  {license_id}")
    y -= 0.25*inch
    c.drawString(margin + 0.35*inch, y, f"Registration Date:  {issued_display}")
    y -= 0.25*inch
    c.drawString(margin + 0.35*inch, y, "Status:  Active")
    y -= 0.25*inch
    c.setFont("Helvetica", 10)
    c.drawString(margin + 0.35*inch, y, f"Transaction ID:  {session_id}")

    # -------------------------
    # Vector Seal (no image file needed)
    # -------------------------
    try:
        from reportlab.lib import colors
    except Exception:
        colors = None

    seal_x = width/2
    seal_y = height/2 - 0.3*inch
    outer_r = 0.85*inch
    inner_r = 0.68*inch

    if colors:
        gold = colors.Color(0.78, 0.63, 0.19)   # gold-ish
        dark = colors.Color(0.30, 0.24, 0.05)   # dark gold/brown
        c.setStrokeColor(gold)
        c.setFillColor(colors.white)
    c.setLineWidth(3)

    # Outer ring
    c.circle(seal_x, seal_y, outer_r, stroke=1, fill=0)
    if colors:
        c.setStrokeColor(dark)
    c.setLineWidth(2)
    c.circle(seal_x, seal_y, inner_r, stroke=1, fill=0)

    # Stars around ring (simple)
    c.setFont("Helvetica-Bold", 12)
    star = "★"
    for dx, dy in [(0, outer_r-10), (outer_r-10, 0), (0, -(outer_r-10)), (-(outer_r-10), 0)]:
        c.drawCentredString(seal_x+dx, seal_y+dy-4, star)

    # Seal text
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(seal_x, seal_y + 12, "PEARLZZ LLC")
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(seal_x, seal_y - 6, "NILPF")
    c.setFont("Helvetica", 9)
    c.drawCentredString(seal_x, seal_y - 22, "OFFICIAL SEAL • 2026")

    # Signature block (right)
    sig_x = width - margin - 3.1*inch
    sig_y = margin + 1.55*inch
    c.setFont("Helvetica-Bold", 16)
    c.drawString(sig_x, sig_y, "PEARLZZ")
    c.setFont("Helvetica", 10)
    c.drawString(sig_x, sig_y - 0.25*inch, "Founder, NILPF")
    c.drawString(sig_x, sig_y - 0.45*inch, "Pearlzz LLC")

    # Final copyright line
    c.setFont("Helvetica-Oblique", 9)
    c.drawCentredString(width/2, margin + 0.55*inch,
                        "© 2026 Pearlzz LLC. All Rights Reserved.")

    c.showPage()
    c.save()

    buf.seek(0)
    filename = f"certificate_{session_id}.pdf"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")



@app.route("/")
def home():
    return redirect("/product")




# -------------------------
# PayPal Buy Route
# -------------------------
@app.route("/buy")
def buy():
    sku = request.args.get("product") or session.get("product_sku")





    if not sku:
       return redirect("/product")

    product = PRODUCTS.get(sku)
    if not product:
        abort(400, "Invalid product selection.")
    access_token = get_paypal_access_token()

    return_url = request.host_url.rstrip("/") + "/success"
    cancel_url = request.host_url.rstrip("/") + "/cancel"

    order_data = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {"currency_code": "USD", "value": product["price"]},
                "custom_id": sku,
                "description": product.get("label", sku),
            }
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
    product_sku = session.get("product_sku")
    if not product_sku:
        return redirect("/buy")
    if not location:
        abort(400, "Missing licensed location in session.")

    full_address = f"{location['street']}, {location['city']}, {location['state']} {location['zip']}"

    license_key = upsert_license(
        order_id,
        location.get("email"),
        location.get("business_name"),
        full_address,
        location.get("state"),
        product_sku,
    )

    return render_template_string("""
    <!doctype html>
    <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Purchase Complete</title>
    <style>
      body{font-family:Arial,sans-serif;max-width:760px;margin:40px auto;padding:0 16px}
      .box{border:2px solid #111;border-radius:12px;padding:18px}
      a.btn{display:inline-block;margin-right:10px;padding:12px 16px;border-radius:10px;text-decoration:none;border:2px solid #111}
    </style></head>
    <body>
        <h1>Payment Completed</h1>
        <p>Your purchase is confirmed and locked to this address.</p>
        <p>
          <a class="btn" href="/download?session_id={{sid}}">Download Files</a>
          <a class="btn" href="/certificate?session_id={{sid}}">Download Certificate</a>
        </p>
      </div>
    </body></html>
    """, sid=order_id)


# -------------------------
# Cancel Route (PayPal)
# -------------------------
@app.route("/cancel")
def cancel():
    return render_template_string("""
    <!doctype html>
    <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Checkout Canceled</title>
    <style>
      body{font-family:Arial,sans-serif;max-width:760px;margin:40px auto;padding:0 16px}
      .box{border:2px solid #111;border-radius:12px;padding:18px}
      a.btn{display:inline-block;padding:12px 16px;border-radius:10px;text-decoration:none;border:2px solid #111}
    </style></head>
    <body>
      <div class="box">
        <h1>Checkout Canceled</h1>
        <p>No payment was completed.</p>
        <p><a class="btn" href="/notice">Start Over</a></p>
      </div>
    </body></html>
    """)

# -------------------------
# Product Choice Route
# -------------------------
@app.route("/product", methods=["GET", "POST"])
def product():
    # Front product page: show cover + what's included + continue
    sku = "COMPLETE_SET"
    product = PRODUCTS.get(sku)
    if not product:
        abort(500, "Product not found in catalog.")

    if request.method == "POST":
        # lock the SKU then continue into your existing flow
        session["product_sku"] = sku
        return redirect("/notice")

    return render_template_string("""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <title>{{ label }}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
          body{font-family:Arial,sans-serif;max-width:920px;margin:40px auto;padding:0 16px}
          .wrap{display:grid;grid-template-columns:1fr;gap:18px}
          @media(min-width:900px){.wrap{grid-template-columns:1.2fr .8fr}}
          .card{border:2px solid #111;border-radius:14px;padding:18px}
          .img{width:100%;border-radius:12px;border:2px solid #111;display:block}
          .btn{display:inline-block;padding:12px 16px;border-radius:10px;border:2px solid #111;background:#fff;cursor:pointer}
          h1{margin:0 0 10px 0}
          ul{margin:10px 0 0 18px}
          .muted{opacity:.85}
          .price{font-size:20px;font-weight:700;margin:12px 0}
          .badge{display:inline-block;padding:6px 10px;border:2px solid #111;border-radius:999px;font-size:13px}
        </style>
      </head>
      <body>
        <div class="wrap">
          <div class="card">
            <img class="img" src="{{ url_for('static', filename='store_preview.png') }}" alt="NILPF Store Preview">
            <p class="muted" style="margin:12px 0 0 0;">
              Preview of what you will receive (download bundle + certificate).
            </p>
          </div>

          <div class="card">
            <span class="badge">One license per physical address</span>
            <h1>{{ label }}</h1>

            <div class="price">${{ price }}</div>

            <div class="muted">
              <b>What you receive:</b>
              <ul>
                <li>Essential Forms bundle</li>
                <li>Core Docs bundle</li>
               
                <li>Immediate download link after payment</li>
                <li>License Certificate (PDF)</li>
              </ul>
            </div>

            <form method="POST" style="margin-top:16px;">
              <button class="btn" type="submit">Continue</button>
            </form>

            <p class="muted" style="margin-top:12px;">
              Next: license notice → enter your licensed address → checkout → download.
            </p>
          </div>
        </div>
      </body>
    </html>
    """, label=product.get("label","NILPF Product"), price=product.get("price",""))


@app.route("/download")
def download():
    session_id = request.args.get("session_id")
    if not session_id:
        abort(400, "Missing session_id.")

    lic = get_license_by_session(session_id)
    if not lic:
        abort(404, "License not found.")

    payer_email, payer_name, prop_addr, prop_state, license_key, created_at, product_sku = lic
    if not product_sku:
        abort(400, "Missing product selection for this purchase.")

    product = PRODUCTS.get(product_sku)
    if not product:
        abort(400, "Invalid product on record.")
    # Resolve file path (single bundle)
    file_path = product["file"]

    if not os.path.exists(file_path):
        abort(500, f"File not found on server: {file_path}")

    # Serve as attachment
    filename = os.path.basename(file_path)
    return send_file(file_path, as_attachment=True, download_name=filename)

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
<div style="margin: 18px 0;">
  <img src="/static/store_preview.png" alt="What's Included Preview" style="max-width:100%; border:1px solid #ddd; border-radius:10px;">
</div>
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


