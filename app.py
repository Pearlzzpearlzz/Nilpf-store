from reportlab.pdfgen import canvas
import fitz
from pathlib import Path
from storage.coordinator import storage_coordinator as storage
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory
import json
import os
from datetime import date, datetime, timedelta
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Render runs Flask behind a reverse proxy; trust forwarded HTTPS/session headers.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_for=1)

@app.context_processor
def inject_today():
    return {"today": date.today().isoformat()}


@app.after_request
def add_idle_logout_script(response):
    try:
        if response.direct_passthrough:
            return response

        if response.content_type and "text/html" in response.content_type:
            skip_paths = ["/login", "/logout", "/activate", "/request-access", "/static"]
            if any(request.path.startswith(path) for path in skip_paths):
                return response

            html = response.get_data(as_text=True)
            if "</body>" in html and "NILPF_IDLE_LOGOUT_TIMER" not in html:
                idle_script = """
<script id="NILPF_IDLE_LOGOUT_TIMER">
(function () {
  var idleTimer;

  function resetIdleTimer() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(function () {
      window.location.href = "/logout";
    }, 120000);
  }

  ["click", "mousemove", "keydown", "scroll", "touchstart"].forEach(function (eventName) {
    document.addEventListener(eventName, resetIdleTimer, true);
  });

  resetIdleTimer();
})();
</script>
"""
                html = html.replace("</body>", idle_script + "</body>")
                response.set_data(html)
                response.headers["Content-Length"] = len(response.get_data())
    except Exception as e:
        print(f"IDLE LOGOUT SCRIPT ERROR: {e}")

    return response

from pypdf import PdfReader, PdfWriter
from PyPDF2 import PdfMerger
from pypdf.generic import NameObject

from io import BytesIO

def fill_pdf_fields(src, out, data):
    os.makedirs(os.path.dirname(out), exist_ok=True)

    reader = PdfReader(src)
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)

    for page in writer.pages:
        writer.update_page_form_field_values(page, data)

    try:
        writer.set_need_appearances_writer(True)
    except Exception:
        pass

    if "/AcroForm" in reader.trailer["/Root"]:
        writer._root_object.update({
            NameObject("/AcroForm"): reader.trailer["/Root"]["/AcroForm"]
        })

    with open(out, "wb") as f:
        writer.write(f)



# =========================
# TRUE-TO-SIGHT HTML PRINT PAGE → PDF EXPORT HELPER
def generate_true_to_sight_pdf(pid, route_template, filename):
    """
    Future-ready PDF engine.

    Keeps the old call pattern:
        generate_true_to_sight_pdf(id, "/some-print/{id}", "some_form_true_to_sight.pdf")

    But avoids Playwright visiting Flask routes by URL.
    This prevents login/session redirects from being printed into the PDF.
    """
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    from flask import render_template

    if pid < 0 or pid >= len(participants):
        raise ValueError(f"Participant not found for PDF export: {pid}")

    participant = participants[pid]

    # Derive form_key from the PDF filename.
    # Example: bill_of_dignity_true_to_sight.pdf -> bill_of_dignity
    form_key = filename
    if form_key.endswith("_true_to_sight.pdf"):
        form_key = form_key.replace("_true_to_sight.pdf", "")
    elif form_key.endswith(".pdf"):
        form_key = form_key.replace(".pdf", "")

    form_state = participant.get("forms", {}).get(form_key, {})
    d = form_state.get("data", {})
    locked = form_state.get("locked", False)

    # Most true-to-sight print templates follow this naming pattern:
    # bill_of_dignity -> bill_of_dignity_print.html
    # Some forms use special template names, so they need overrides.
    PDF_TEMPLATE_OVERRIDES = {
        "mla": "mla_transitional_print.html",
    }

    template_name = PDF_TEMPLATE_OVERRIDES.get(form_key, f"{form_key}_print.html")

    activation = {}
    try:
        activation = load_activation()
    except Exception:
        activation = {}

    html_content = render_template(
        template_name,
        id=pid,
        participant=participant,
        d=d,
        locked=locked,
        activation=activation,
        program_type=participant.get("program_type") or activation.get("program_type", "")
    )

    folder = Path(f"static/filled/participant_{pid}")
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / filename

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_content, wait_until="load")
        page.pdf(
            path=str(output),
            format="Letter",
            print_background=True,
            margin={
                "top": "0.4in",
                "right": "0.4in",
                "bottom": "0.4in",
                "left": "0.4in"
            }
        )
        browser.close()

    return str(output)


def fill_pdf(src, out, data):
    return fill_pdf_fields(src, out, data)

def fill_pdf(src, out, data):
    os.makedirs(os.path.dirname(out), exist_ok=True)

    reader = PdfReader(src)
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)

    root = reader.trailer["/Root"]
    if "/AcroForm" not in root:
        raise ValueError("SOURCE PDF HAS NO /AcroForm")

    writer._root_object.update({
        NameObject("/AcroForm"): root["/AcroForm"]
    })

    for page in writer.pages:
        writer.update_page_form_field_values(page, data)

    try:
        writer.set_need_appearances_writer(True)
    except Exception:
        pass

    with open(out, "wb") as f:
        writer.write(f)


app.secret_key = os.environ.get("SECRET_KEY", "dev-local-nilpf-secret-change-in-render")

def license_access_allowed(activation):
    """
    Live access rule:
    - active/payment_received_active = allowed
    - no license_status = allowed for older/local activation records
    - pending_payment_verification = allowed only until grace_expires_at
    """
    status = str((activation or {}).get("license_status", "")).strip()

    if not status:
        return True

    if status in ["active", "payment_received_active"]:
        return True

    if status == "pending_payment_verification":
        expires = (activation or {}).get("grace_expires_at", "")
        if not expires:
            return False
        try:
            return datetime.now() <= datetime.fromisoformat(expires)
        except Exception:
            return False

    return False


def license_payment_notice(activation):
    status = str((activation or {}).get("license_status", "")).strip()
    expires = (activation or {}).get("grace_expires_at", "")

    if status == "pending_payment_verification" and expires:
        return f"Payment is due within the 3-day access window. Grace access expires: {expires}"

    if status == "pending_payment_verification":
        return "Payment is due before access can continue."

    return ""


@app.before_request
def require_login():
    public_routes = ["login", "activate", "logout", "request_access", "paypal_webhook", "owner_license_approval", "owner_license_approval_activate"]
    if request.endpoint in public_routes or request.path == "/favicon.ico" or (request.path and request.path.startswith("/static/")):
        return

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    activated_system = load_activation()
    if not license_access_allowed(activated_system):
        flash("Your 3-day access window has ended. Payment is required to continue.")
        session.clear()
        return redirect(url_for("login"))

    view_args = request.view_args or {}
    if "id" in view_args:
        pid = view_args.get("id")
        try:
            pid = int(pid)
        except (ValueError, TypeError):
            pid = -1
        if pid < 0 or pid >= len(participants):
            flash("Participant not found.")
            return redirect(url_for("add_participant"))

        admin_name_required_routes = [
            "referral_source_record",
            "referral_source_record_print",
            "referral_source_record_final",
            "agency_sponsorship_record",
            "agency_sponsorship_record_print",
            "agency_sponsorship_record_final",
            "payor_funding_record",
            "payor_funding_record_print",
            "payor_funding_record_final",
            "billing_invoice_setup",
            "billing_invoice_setup_print",
            "billing_invoice_setup_final"
        ]

        if request.endpoint in admin_name_required_routes:
            participant = participants[pid]
            if not str(participant.get("name", "")).strip():
                flash("Enter participant name before completing admin paperwork.")
                return redirect(url_for("add_participant"))

DATA_FILE = "data/activation.json"




# PACKET MANIFEST (controls clean packet export)

# PRN (AS NEEDED) FORMS
PRN_FORMS = [
    "08_reported_occurrence_form.pdf",
    "12_transfer_form.pdf"
]

PACKET_MANIFEST = {
    "ILH": [
        {"title": "Independent Living Disclosure", "completed_file": "01_independent_living_disclosure.pdf"},
        {"title": "Master License Agreement", "completed_file": "/mla/{id}"},
        {"title": "Bill of Dignity", "completed_file": "15_member_bill_of_dignity_independence.pdf"}
    ],
    "Transitional": [
        {"title": "Master Lease Agreement", "completed_file": "02_MASTER LEASE AGREEMENT.pdf"},
        {"title": "MLA - Reentry Transitional Housing Program", "completed_file": "03_MLA_Reentry Transitional Housing Program.pdf"},
        {"title": "Program Compliance Addendum", "completed_file": "04_PROGRAM COMPLIANCE ADDENDUM.pdf"}
    ],
    "VA_GPD_Aligned": [
        {"title": "VA Coordination Acknowledgment", "completed_file": "05_VA COORDINATION ACKNOWLEDGMENT.pdf"},
        {"title": "Board Resolution", "completed_file": "BOARD RESOLUTION.pdf"},
        {"title": "Waiver Financial Justification Template", "completed_file": "WAIVER FINANCIAL JUSTIFICATION TEMPLATE.pdf"}
    ],
    "DOC_Reentry_Aligned": [
        {"title": "Master License Agreement", "completed_file": "/mla/{id}"},
        {"title": "Program Compliance Addendum", "completed_file": "04_PROGRAM COMPLIANCE ADDENDUM.pdf"},
        {"title": "Guest Addendum", "completed_file": "09_guest_addendum.pdf"},
        {"title": "Emergency Contact", "completed_file": "07_emergency_contact_form.pdf"}
    ]
}


CORE_DOCS_BY_PROGRAM = {
    "ILH": [
        {"title": "Entry Screening & Self-Determination", "file": "/entry-screening/{id}"},
        {"title": "Independent Living Disclosure", "file": "/independent-living-disclosure/{id}"},
        {"title": "House Rules & Community Standards", "file": "/house-rules/{id}"},
        {"title": "Fire Safety & Self-Preservation", "file": "/fire-safety/{id}"},
        {"title": "Emergency Contact", "file": "/emergency-contact/{id}"},
        {"title": "Emergency Evacuation", "file": "/emergency-evacuation/{id}"},
        {"title": "Guest Addendum", "file": "/guest-addendum/{id}"},
        {"title": "No Services / No Supervision", "file": "/no-services-supervision/{id}"},
        {"title": "Common Area Security", "file": "/common-area-security/{id}"},
        {"title": "Personal Belongings", "file": "/personal-belongings/{id}"},
        {"title": "Property Belongings", "file": "/property-belongings/{id}"},
        {"title": "Pet Animal Information", "file": "/pet-animal/{id}"},
        {"title": "Privacy Acknowledgment", "file": "/privacy-acknowledgment/{id}"},
        {"title": "Privacy Noncommercial", "file": "/privacy-noncommercial/{id}"},
        {"title": "Vehicle Parking", "file": "/vehicle-parking/{id}"},
        {"title": "Transfer Form", "file": "/transfer/{id}"},
        {"title": "Security Camera Disclosure", "file": "/security-camera/{id}"},
        {"title": "Voluntary Participation", "file": "/voluntary-participation/{id}"},
        {"title": "Incident Report", "file": "/incident-report/{id}"},
          {"title": "🔒 Sensitivity Vault", "file": "/sensitive-identity-record/{id}", "vault": True},
        {"title": "Release of Information / Authorization to Communicate", "file": "/release-of-information/{id}"},
        {"title": "Bill of Dignity & Independence", "file": "/bill-of-dignity/{id}"},
        {"title": "ILH Master License Agreement", "file": "/ilh-mla/{id}"},
    ],
    "Transitional": [
        {"title": "Initial Intake Assessment", "file": "/intake-assessment/{id}"},
        {"title": "Master License Agreement", "file": "/mla/{id}"},
        {"title": "Program Compliance Addendum", "file": "/program-compliance-addendum/{id}"},
        {"title": "Program Participation Agreement", "file": "/program-participation-agreement/{id}"},
          {"title": "🔒 Sensitivity Vault", "file": "/sensitive-identity-record/{id}", "vault": True},
        {"title": "Release of Information / Authorization to Communicate", "file": "/release-of-information/{id}"},
    ],
    "VA_GPD_Aligned": [
        {"title": "VA Coordination Acknowledgment", "file": "va_gpd/05_VA COORDINATION ACKNOWLEDGMENT.pdf"}
    ],
     "DOC_Reentry_Aligned": [
    ],
    "PSH": [],
}


SHARED_HOUSING_FORMS = [
    {"title": "House Rules & Community Standards", "file": "/house-rules/{id}"},
    {"title": "Fire Safety & Self-Preservation", "file": "/fire-safety/{id}"},
    {"title": "Emergency Contact", "file": "/emergency-contact/{id}"},
    {"title": "Emergency Evacuation", "file": "/emergency-evacuation/{id}"},
    {"title": "Guest Addendum", "file": "/guest-addendum/{id}"},
    {"title": "Common Area Security", "file": "/common-area-security/{id}"},
    {"title": "Personal Belongings", "file": "/personal-belongings/{id}"},
    {"title": "Property Belongings", "file": "/property-belongings/{id}"},
    {"title": "Pet Animal Information", "file": "/pet-animal/{id}"},
    {"title": "Privacy Acknowledgment", "file": "/privacy-acknowledgment/{id}"},
    {"title": "Privacy Noncommercial", "file": "/privacy-noncommercial/{id}"},
    {"title": "Vehicle Parking", "file": "/vehicle-parking/{id}"},
    {"title": "Transfer Form", "file": "/transfer/{id}"},
    {"title": "Security Camera Disclosure", "file": "/security-camera/{id}"},
    {"title": "Voluntary Participation", "file": "/voluntary-participation/{id}"},
    {"title": "Incident Report", "file": "/incident-report/{id}"}
]


def get_active_shared_housing_forms():
    """
    Hybrid shared-form registry.

    Keeps hardcoded shared housing forms as the safe default.
    Allows data/shared_forms.json to add future shared forms without editing app.py.
    """
    forms = list(SHARED_HOUSING_FORMS)

    try:
        saved = storage.get_shared_forms()
    except Exception:
        saved = {}

    extra_forms = saved.get("housing_forms", []) if isinstance(saved, dict) else []
    if not isinstance(extra_forms, list):
        extra_forms = []

    seen = {item.get("file") for item in forms if isinstance(item, dict)}

    for item in extra_forms:
        if not isinstance(item, dict):
            continue

        title = item.get("title")
        file_path = item.get("file")

        if not title or not file_path:
            continue

        if file_path in seen:
            continue

        forms.append({"title": title, "file": file_path})
        seen.add(file_path)

    return forms


def load_activation():
    return storage.get_activation()

def save_activation(data):
    return storage.save_activation(data)

PARTICIPANTS_FILE = "data/participants.json"

def load_participants():
    return storage.get_participants()

def save_participants(data):
    return storage.save_participants(data)


LICENSE_REQUESTS_FILE = "data/license_requests.json"

def load_license_requests():
    return storage.get_license_requests()

def save_license_requests(data):
    return storage.save_license_requests(data)

def generate_license_number(existing_requests):
    year = datetime.now().year
    next_number = len(existing_requests) + 1
    return f"NILPF-{year}-{next_number:04d}"


AUDIT_LOG_FILE = "data/audit_log.json"

def write_audit(action, participant_id=None, form_key="", details=""):
    logs = storage.get_audit_logs()

    activated_system = load_activation()

    logs.append({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "owner_operator_email": activated_system.get("email", ""),
        "business_or_property_name": activated_system.get("property_name", ""),
        "program_type": activated_system.get("program_type", ""),
        "action": action,
        "participant_id": participant_id,
        "form_key": form_key,
        "route": request.path,
        "method": request.method,
        "details": details
    })

    storage.save_audit_logs(logs)

@app.route("/")
def home():
    activated_system = load_activation()
    if not activated_system:
        return redirect(url_for("activate"))
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return render_template("home.html", activation=activated_system)

@app.route("/activate", methods=["GET", "POST"])
def activate():
    if request.method == "POST":
        property_name = request.form.get("property_name")
        license_number = request.form.get("license_number")
        email = request.form.get("email")
        password = request.form.get("password")

        if not property_name or not password:
            flash("Property and password are required.")
            return redirect(url_for("activate"))

        activated_system = {
            "property_name": property_name,
            "license_number": license_number,
            "email": email,
            "password": password,
            "program_type": "Transitional"  # placeholder for future expansion
        }
        save_activation(activated_system)
        return redirect(url_for("login"))

    return render_template("activation.html")

@app.route("/request-access", methods=["GET", "POST"])
def request_access():
    submitted_request = None

    if request.method == "POST":
        requests_data = load_license_requests()

        full_name = request.form.get("full_name", "").strip()
        business_name = request.form.get("business_name", "").strip()
        paypal_email = request.form.get("paypal_email", "").strip().lower()
        site_address = request.form.get("site_address", "").strip()
        city = request.form.get("city", "").strip()
        state = request.form.get("state", "").strip()
        zip_code = request.form.get("zip_code", "").strip()
        phone = request.form.get("phone", "").strip()
        notes = request.form.get("notes", "").strip()

        if not full_name or not business_name or not paypal_email or not site_address or not city or not state or not zip_code:
            flash("Name, business name, PayPal email, and full physical site address are required.")
            return redirect(url_for("request_access"))

        license_number = generate_license_number(requests_data)

        now = datetime.now()
        grace_expires_at = now + timedelta(days=3)

        submitted_request = {
            "license_number": license_number,
            "status": "pending_payment_verification",
            "payment_due_status": "payment_due_within_3_days",
            "grace_started_at": now.isoformat(timespec="seconds"),
            "grace_expires_at": grace_expires_at.isoformat(timespec="seconds"),
            "full_name": full_name,
            "business_name": business_name,
            "paypal_email": paypal_email,
            "site_address": site_address,
            "city": city,
            "state": state,
            "zip_code": zip_code,
            "phone": phone,
            "notes": notes,
            "created_at": now.isoformat(timespec="seconds")
        }

        requests_data.append(submitted_request)
        save_license_requests(requests_data)

        # Carry only public-facing license display info into the active app record.
        # Full address and request details stay stored behind the scenes in license_requests.json.
        activated_system = load_activation()
        activated_system["property_name"] = business_name
        activated_system["license_number"] = license_number
        activated_system["email"] = paypal_email
        activated_system["license_status"] = "pending_payment_verification"
        activated_system["payment_due_status"] = "payment_due_within_3_days"
        activated_system["grace_started_at"] = now.isoformat(timespec="seconds")
        activated_system["grace_expires_at"] = grace_expires_at.isoformat(timespec="seconds")
        activated_system["business_name"] = business_name
        activated_system["licensed_site_address"] = site_address
        activated_system["licensed_site_city"] = city
        activated_system["licensed_site_state"] = state
        activated_system["licensed_site_zip"] = zip_code

        # Do not change the customer's selected housing/program type.
        if not activated_system.get("program_type"):
            activated_system["program_type"] = "Transitional"

        # Do not change the existing password behavior.
        if "password" not in activated_system:
            activated_system["password"] = ""

        save_activation(activated_system)

        return render_template("request_access.html", submitted_request=submitted_request)

    return render_template("request_access.html", submitted_request=submitted_request)



@app.route("/paypal-webhook", methods=["POST"])
def paypal_webhook():
    """
    PayPal webhook receiver for NILPF Housing OS.

    Purpose:
    - Receives PayPal subscription/payment events.
    - Saves a local webhook log for Mr. IR / admin review.
    - Matches buyer email to license_requests.json when possible.
    - Marks matching license request as payment_received / active.
    """

    payload = request.get_json(silent=True) or {}
    event_type = payload.get("event_type", "UNKNOWN_PAYPAL_EVENT")
    resource = payload.get("resource", {}) or {}

    payer_email = (
        resource.get("subscriber", {}).get("email_address")
        or resource.get("payer", {}).get("email_address")
        or resource.get("payer_email")
        or ""
    ).strip().lower()

    subscription_id = (
        resource.get("id")
        or resource.get("billing_agreement_id")
        or resource.get("subscription_id")
        or ""
    )

    webhook_events = storage.get_paypal_webhook_events()

    webhook_record = {
        "received_at": datetime.now().isoformat(timespec="seconds"),
        "event_type": event_type,
        "payer_email": payer_email,
        "subscription_id": subscription_id,
        "raw_event": payload
    }

    webhook_events.append(webhook_record)

    storage.save_paypal_webhook_events(webhook_events)

    updated_license = None

    paid_event_types = {
        "BILLING.SUBSCRIPTION.ACTIVATED",
        "BILLING.SUBSCRIPTION.RE-ACTIVATED",
        "PAYMENT.SALE.COMPLETED",
        "CHECKOUT.ORDER.APPROVED",
        "PAYMENT.CAPTURE.COMPLETED"
    }

    if payer_email and event_type in paid_event_types:
        requests_data = load_license_requests()

        for item in requests_data:
            if item.get("paypal_email", "").strip().lower() == payer_email:
                item["status"] = "payment_received_active"
                item["paypal_event_type"] = event_type
                item["paypal_subscription_id"] = subscription_id
                item["payment_verified_at"] = datetime.now().isoformat(timespec="seconds")
                updated_license = item.get("license_number")
                break

        save_license_requests(requests_data)

        activated_system = load_activation()
        if activated_system.get("email", "").strip().lower() == payer_email:
            activated_system["license_status"] = "payment_received_active"
            activated_system["paypal_event_type"] = event_type
            activated_system["paypal_subscription_id"] = subscription_id
            activated_system["payment_verified_at"] = datetime.now().isoformat(timespec="seconds")
            save_activation(activated_system)

    write_audit(
        "paypal_webhook_received",
        form_key="paypal_webhook",
        details=f"event={event_type}; payer_email={payer_email}; updated_license={updated_license}"
    )

    return jsonify({
        "status": "received",
        "event_type": event_type,
        "payer_email_found": bool(payer_email),
        "updated_license": updated_license
    }), 200


@app.route("/login", methods=["GET", "POST"])
def login():
    activated_system = load_activation()
    if not activated_system:
        return redirect(url_for("activate"))

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        stored_email = str(activated_system.get("email", "")).strip().lower()
        entered_email = str(email or "").strip().lower()

        if entered_email == stored_email and password == activated_system.get("password"):
            if not license_access_allowed(activated_system):
                flash("Your 3-day access window has ended. Payment is required to continue.")
                return redirect(url_for("login"))

            session.clear()
            session["logged_in"] = True
            session["owner_operator_email"] = stored_email
            write_audit("login")
            return redirect(url_for("home"))
        flash("Invalid email or password")
        return redirect(url_for("login"))

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login</title>
<style>
/* LOGIN_PHONE_FIX_20260608 */
* {{ box-sizing: border-box; }}

body {{
  background:#000;
  color:#fff;
  font-family:Arial;
  text-align:center;
  padding:28px;
  margin:0;
}}

.loginbox {{
  width:100%;
  max-width:520px;
  margin:auto;
  border:2px solid #d4af37;
  border-radius:22px;
  padding:22px;
  background:#070707;
  box-shadow:0 0 25px rgba(212,175,55,.3);
}}

.hero {{
  width:100%;
  max-width:420px;
  border-radius:16px;
  border:1px solid #6f5a16;
  margin-bottom:14px;
}}

input {{
  width:100%;
  max-width:390px;
  padding:12px;
  margin:8px 0;
  border-radius:8px;
  border:1px solid #d4af37;
  background:#111;
  color:#fff;
  font-size:16px;
}}

button {{
  width:100%;
  max-width:390px;
  padding:14px 20px;
  background:#d4af37;
  border:none;
  border-radius:10px;
  font-weight:bold;
  font-size:16px;
}}

.eye {{
  cursor:pointer;
  margin-left:-30px;
}}

a {{
  color:#d4af37;
  font-size:14px;
}}

@media (max-width: 700px) {{
  body {{
    padding:12px;
  }}

  .loginbox {{
    width:100%;
    max-width:none;
    margin:16px auto;
    padding:18px 14px;
    border-radius:18px;
  }}

  .hero {{
    max-width:100%;
    border-radius:14px;
  }}

  h2 {{
    font-size:22px;
    margin:12px 0;
  }}

  input {{
    max-width:none;
    min-height:50px;
    font-size:18px;
    padding:14px;
  }}

  button {{
    max-width:none;
    min-height:52px;
    font-size:18px;
    padding:14px;
    margin-top:8px;
  }}

  .eye {{
    display:inline-block;
    font-size:20px;
    margin-left:-34px;
  }}

  a {{
    font-size:16px;
  }}
}}
</style>
<script>
function togglePass() {{
  var x = document.getElementById("pw");
  x.type = x.type === "password" ? "text" : "password";
}}
</script>
</head>
<body>
<div class="loginbox">
<img class="hero" src="/static/images/nilpf-os-login-hero-web.png" alt="NILPF OS">
<h2>NILPF OS Login</h2>

<form method="POST">
<input name="email" value="{activated_system.get("email","")}" readonly><br>

<input id="pw" type="password" name="password" placeholder="Password">
<span class="eye" onclick="togglePass()">👁</span><br>

<button>Login</button>
</form>

<br>
<a href="#">Forgot Password?</a>
</div>

</body>
</html>
"""

@app.route("/logout")
def logout():
    write_audit("logout")
    session.clear()
    return redirect(url_for("login"))

@app.route("/operations")
def operations():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    activated_system = load_activation()
    return render_template("operations.html", activation=activated_system)

@app.route("/core-docs")
def core_docs():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    activated_system = load_activation()
    return render_template("core_docs.html", activation=activated_system, program_type=activated_system.get("program_type", "ILH"), docs=CORE_DOCS_BY_PROGRAM.get(activated_system.get("program_type", "ILH"), []))


PARTICIPANTS_FILE = "data/participants.json"

def load_participants_file():
    """
    Compatibility wrapper only.
    Uses the main participant storage path.
    """
    return load_participants()

def save_participants_file():
    """
    Compatibility wrapper only.
    Saves the current global participant list through the main storage path.
    Old routes that still call save_participants_file() will now use Postgres-first storage.
    """
    global participants
    return save_participants(participants)

participants = load_participants_file()

@app.before_request
def refresh_participants_from_storage():
    """
    Postgres-first live participant refresh.

    Many older routes still read the global participants list directly.
    This keeps those routes synced with Postgres/JSON before each request,
    so newly added participants appear in Packet Builder, forms, Operations,
    Mr.IR checks, and auto-fill paths.
    """
    global participants

    # Avoid unnecessary storage reads for static files.
    if request.endpoint == "static":
        return

    try:
        participants = load_participants_file()
    except Exception as exc:
        logger.exception("Participant refresh failed before request: %s", exc)

@app.route("/add_participant", methods=["GET", "POST"])
def add_participant():
    participants = load_participants()

    # ILH now works like Intake:
    # clicking Add Participant assigns a PID first,
    # then Entry Screening collects/saves the name.
    program_type = "ILH"

    new_id = len(participants)
    participants.append({
        "pid": new_id,
        "participant_id": new_id,
        "form_data": {},
        "forms": {},
        "name": "",
        "participant_name": "",
        "program_type": program_type,
    })

    save_participants(participants)
    session["current_pid"] = new_id

    return redirect(url_for("entry_screening", id=new_id))


PROPERTY_PAPERS_FILE = "data/property_papers.json"

def load_property_papers():
    return storage.get_property_papers()

def save_property_papers(data):
    return storage.save_property_papers(data)

def is_property_paper_route(route_name):
    return route_name in [
        "board-resolution",
        "mou-partner-agreement",
        "waiver-financial-justification",
        "master-lease-transitional",
        "ilh-master-lease",
        "triple-net-lease",
        "program-housing-covenants"
    ]

def property_paper_bucket(route):
    return {
        "board-resolution": "board_resolutions",
        "mou-partner-agreement": "mou_partner_agreements",
        "waiver-financial-justification": "waiver_financial_justifications",
        "master-lease-transitional": "th_master_leases",
        "ilh-master-lease": "ilh_master_leases",
        "triple-net-lease": "triple_net_leases",
        "program-housing-covenants": "program_housing_covenants"
    }.get(route)


def save_locked_property_paper(route, form_data):
    bucket = property_paper_bucket(route)
    if not bucket:
        return

    papers = load_property_papers()
    entry = dict(form_data or {})
    entry["route"] = route
    entry["doc_type"] = {
        "board-resolution": "board",
        "mou-partner-agreement": "mou",
        "waiver-financial-justification": "waiver",
        "master-lease-transitional": "lease",
        "ilh-master-lease": "lease",
        "triple-net-lease": "lease",
        "program-housing-covenants": "covenant"
    }.get(route, "")

    papers.setdefault(bucket, []).append(entry)
    save_property_papers(papers)


def property_paper_templates(route):
    return {
        "ilh-master-lease": ("ilh_master_lease_form.html", "ilh_master_lease_print.html"),
        "master-lease-transitional": ("master_lease_transitional_form.html", "master_lease_transitional_print.html"),
        "board-resolution": ("board_resolution_form.html", "board_resolution_print.html"),
        "mou-partner-agreement": ("mou_partner_agreement_form.html", "mou_partner_agreement_print.html"),
        "waiver-financial-justification": ("waiver_financial_justification_form.html", "waiver_financial_justification_print.html"),
        "triple-net-lease": ("triple_net_lease_form.html", "triple_net_lease_print.html"),
        "program-housing-covenants": ("program_housing_covenants_form.html", "program_housing_covenants_print.html")
    }.get(route)


@app.route("/property-paper/<route>", methods=["GET", "POST"])
def property_paper_form(route):
    if not is_property_paper_route(route_name):
        return redirect("/property-papers")

    templates = property_paper_templates(route)
    if not templates:
        return redirect("/property-papers")

    form_template, print_template = templates
    bucket = property_paper_bucket(route)
    papers = load_property_papers()

    if request.method == "POST":
        record = request.form.to_dict()
        record["route"] = route
        record["doc_type"] = {
            "board-resolution": "board",
            "mou-partner-agreement": "mou",
            "waiver-financial-justification": "waiver",
            "master-lease-transitional": "lease",
            "ilh-master-lease": "lease",
            "triple-net-lease": "lease",
            "program-housing-covenants": "covenant"
        }.get(route, "")
        record["completed"] = True
        record["locked"] = False

        papers.setdefault(bucket, []).append(record)
        save_property_papers(papers)
        record_id = len(papers[bucket]) - 1
        return redirect(f"/property-paper-print/{route}/{record_id}")

    return render_template(form_template, id="", participant={}, d={}, locked=False)


@app.route("/property-paper-edit/<route>/<int:record_id>", methods=["GET", "POST"])
def property_paper_edit(route, record_id):
    if not is_property_paper_route(route_name):
        return redirect("/property-papers")

    templates = property_paper_templates(route)
    if not templates:
        return redirect("/property-papers")

    form_template, print_template = templates
    bucket = property_paper_bucket(route)
    papers = load_property_papers()
    records = papers.get(bucket, [])

    if record_id < 0 or record_id >= len(records):
        return redirect("/property-papers")

    if request.method == "POST":
        updated = request.form.to_dict()
        updated["route"] = route
        updated["doc_type"] = {
            "board-resolution": "board",
            "mou-partner-agreement": "mou",
            "waiver-financial-justification": "waiver",
            "master-lease-transitional": "lease",
            "ilh-master-lease": "lease",
            "triple-net-lease": "lease",
            "program-housing-covenants": "covenant"
        }.get(route, "")
        updated["completed"] = True
        updated["locked"] = False
        records[record_id] = updated
        save_property_papers(papers)
        return redirect(f"/property-paper-print/{route}/{record_id}")

    records[record_id]["locked"] = False
    save_property_papers(papers)
    return render_template(form_template, id=record_id, route=route, participant={}, d=records[record_id], locked=False)


@app.route("/property-paper-print/<route>/<int:record_id>")
def property_paper_print(route, record_id):
    if not is_property_paper_route(route_name):
        return redirect("/property-papers")

    templates = property_paper_templates(route)
    if not templates:
        return redirect("/property-papers")

    form_template, print_template = templates
    bucket = property_paper_bucket(route)
    papers = load_property_papers()
    records = papers.get(bucket, [])

    if record_id < 0 or record_id >= len(records):
        return redirect("/property-papers")

    record = records[record_id]
    return render_template(print_template, id=record_id, route=route, participant={}, d=record, locked=record.get("locked", False))


@app.route("/property-paper-final/<route>/<int:record_id>", methods=["POST"])
def property_paper_final(route, record_id):
    if not is_property_paper_route(route_name):
        return redirect("/property-papers")

    bucket = property_paper_bucket(route)
    papers = load_property_papers()
    records = papers.get(bucket, [])

    if record_id < 0 or record_id >= len(records):
        return redirect("/property-papers")

    records[record_id]["locked"] = True
    records[record_id]["completed"] = True
    save_property_papers(papers)

    return redirect("/property-papers")


@app.route("/property-paper-unlock/<route>/<int:record_id>", methods=["GET", "POST"])
def property_paper_unlock(route, record_id):
    if not is_property_paper_route(route_name):
        return redirect("/property-papers")

    bucket = property_paper_bucket(route)
    papers = load_property_papers()
    records = papers.get(bucket, [])

    if record_id < 0 or record_id >= len(records):
        return redirect("/property-papers")

    records[record_id]["locked"] = False
    save_property_papers(papers)

    return redirect(f"/property-paper-print/{route}/{record_id}")


@app.route("/property-papers")
def property_papers():
    org = request.args.get("org", "").strip().lower()
    doc_type = request.args.get("doc_type", "").strip().lower()

    papers = load_property_papers()
    results = []

    for bucket, records in papers.items():
        for idx, record in enumerate(records):
            text = " ".join(str(v) for v in record.values()).lower()
            record_doc_type = str(record.get("doc_type", "")).lower()
            route = record.get("route", "")

            if org and org not in text:
                continue
            if doc_type and doc_type != record_doc_type:
                continue

            results.append({
                "bucket": bucket,
                "record_id": idx,
                "route": route,
                "doc_type": record_doc_type,
                "locked": record.get("locked", False),
                "primary_org_name": record.get("primary_org_name", ""),
                "partner_org_name": record.get("partner_org_name", ""),
                "agreement_date": record.get("agreement_date", ""),
                "agreement_type": record.get("agreement_type", "")
            })

    return render_template("property_papers.html", results=results, org=org, doc_type=doc_type)


@app.route("/tsh-program-tools")
def tsh_program_tools():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return render_template("tsh_program_tools.html")




EMPLOYEE_CERTS_FILE = "data/employee_certifications.json"

def load_employee_certs():
    return storage.get_employee_certs()

def save_employee_certs(records):
    return storage.save_employee_certs(records)

def employee_cert_status(expiration_date):
    from datetime import datetime, timedelta, date
    if not expiration_date:
        return "No Expiration"
    try:
        exp = datetime.strptime(expiration_date, "%Y-%m-%d").date()
    except ValueError:
        return "Date Error"

    today = date.today()
    days_left = (exp - today).days

    if days_left < 0:
        return "Expired"
    if days_left <= 30:
        return "Expiring Soon"
    return "Current"



def build_apb_hmis_readiness_summary():
    import json
    from pathlib import Path
    from datetime import datetime, timedelta

    try:
        participants = storage.get_participants()
    except Exception:
        participants = []

    if not isinstance(participants, list):
        participants = []

    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
        "total_participants": len(participants),
        "program_counts": {},
        "completed_forms": 0,
        "locked_forms": 0,
        "intake_entry_records": 0,
        "service_coordination_records": 0,
        "exit_discharge_records": 0,
        "checkin_checkout_records": 0,
        "uploads_proof_records": 0,
        "audit_log_activity": 0,
        "participants": [],
        "missing_field_total": 0,
    }

    audit_files = [
        Path("data/audit_log.json"),
        Path("data/audit_logs.json"),
        Path("data/mr_ir_log.json"),
        Path("data/mr_ir_records.json"),
        Path("data/change_log.json"),
    ]

    for audit_file in audit_files:
        if audit_file.exists():
            try:
                audit_data = json.loads(audit_file.read_text() or "[]")
                if isinstance(audit_data, list):
                    summary["audit_log_activity"] += len(audit_data)
                elif isinstance(audit_data, dict):
                    summary["audit_log_activity"] += len(audit_data.keys())
            except Exception:
                pass

    for pid, person in enumerate(participants):
        if not isinstance(person, dict):
            continue

        name = person.get("name") or person.get("participant_name") or ""
        program = person.get("program_type") or "Not Selected"
        forms = person.get("forms") or {}
        uploads = person.get("uploads") or []
        check_logs = person.get("checkin_checkout_logs") or []

        summary["program_counts"][program] = summary["program_counts"].get(program, 0) + 1

        completed_keys = []
        locked_keys = []

        for form_key, record in forms.items():
            if not isinstance(record, dict):
                continue
            if record.get("completed"):
                summary["completed_forms"] += 1
                completed_keys.append(form_key)
            if record.get("locked"):
                summary["locked_forms"] += 1
                locked_keys.append(form_key)

        intake = forms.get("intake_assessment") or forms.get("entry_screening") or {}
        intake_data = intake.get("data") if isinstance(intake, dict) else {}
        service = forms.get("service_activity_record") or {}
        service_data = service.get("data") if isinstance(service, dict) else {}
        isp = forms.get("individual_service_plan") or {}
        exit_record = forms.get("exit_discharge_summary") or forms.get("exit_summary") or forms.get("discharge_summary") or {}

        if isinstance(intake, dict) and intake.get("completed"):
            summary["intake_entry_records"] += 1

        if isinstance(service, dict) and service.get("completed"):
            summary["service_coordination_records"] += 1

        if isinstance(isp, dict) and isp.get("completed"):
            summary["service_coordination_records"] += 1

        if isinstance(exit_record, dict) and exit_record.get("completed"):
            summary["exit_discharge_records"] += 1

        if isinstance(check_logs, list):
            summary["checkin_checkout_records"] += len(check_logs)
        elif isinstance(check_logs, dict):
            summary["checkin_checkout_records"] += len(check_logs.keys())

        if isinstance(uploads, list):
            summary["uploads_proof_records"] += len(uploads)
        elif isinstance(uploads, dict):
            summary["uploads_proof_records"] += len(uploads.keys())

        missing = []

        if not name:
            missing.append("Participant name")
        if not program or program == "Not Selected":
            missing.append("Program type")
        if not (isinstance(intake, dict) and intake.get("completed")):
            missing.append("Intake / entry record")
        if not (intake_data or {}).get("assessment_date"):
            missing.append("Intake / entry date")
        if not (intake_data or {}).get("housing_status"):
            missing.append("Housing status")
        if not (isinstance(service, dict) and service.get("completed")) and not (isinstance(isp, dict) and isp.get("completed")):
            missing.append("Service coordination record")
        if not (isinstance(exit_record, dict) and exit_record.get("completed")):
            missing.append("Exit / discharge record")
        if not check_logs:
            missing.append("Check-in / check-out records")
        if not uploads:
            missing.append("Uploads / proof records")

        summary["missing_field_total"] += len(missing)

        summary["participants"].append({
            "pid": pid,
            "name": name or "Unnamed Participant",
            "program": program,
            "completed_count": len(completed_keys),
            "locked_count": len(locked_keys),
            "intake_status": "Present" if isinstance(intake, dict) and intake.get("completed") else "Missing",
            "service_status": "Present" if ((isinstance(service, dict) and service.get("completed")) or (isinstance(isp, dict) and isp.get("completed"))) else "Missing",
            "exit_status": "Present" if isinstance(exit_record, dict) and exit_record.get("completed") else "Missing",
            "checkin_count": len(check_logs) if hasattr(check_logs, "__len__") else 0,
            "upload_count": len(uploads) if hasattr(uploads, "__len__") else 0,
            "missing": missing,
        })

    return summary



@app.route("/audit-packet-builder")
def audit_packet_builder():
    return render_template("audit_packet_builder.html")

@app.route("/audit-packet-builder-summary")
def audit_packet_builder_summary():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    participant_scope = request.args.get("participant_scope", "")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    requesting_entity = request.args.get("requesting_entity", "")
    record_types = request.args.getlist("record_types")

    readiness = build_apb_hmis_readiness_summary()

    return render_template(
        "audit_packet_builder_summary.html",
        participant_scope=participant_scope,
        start_date=start_date,
        end_date=end_date,
        requesting_entity=requesting_entity,
        record_types=record_types,
        readiness=readiness
    )


@app.route("/employee-certifications", methods=["GET", "POST"])
def employee_certifications():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    from datetime import datetime, timedelta

    records = load_employee_certs()

    if request.method == "POST":
        record = {
            "employee_name": request.form.get("employee_name", "").strip(),
            "role": request.form.get("role", "").strip(),
            "certification_name": request.form.get("certification_name", "").strip(),
            "completed_date": request.form.get("completed_date", "").strip(),
            "expiration_date": request.form.get("expiration_date", "").strip(),
            "alert": request.form.get("alert", "").strip(),
            "created_at": datetime.now().strftime("%Y-%m-%d %I:%M %p")
        }

        record["status"] = employee_cert_status(record["expiration_date"])
        records.append(record)
        save_employee_certs(records)
        return redirect(url_for("employee_certifications"))

    for record in records:
        record["status"] = employee_cert_status(record.get("expiration_date", ""))

    counts = {
        "Current": sum(1 for r in records if r.get("status") == "Current"),
        "Expiring Soon": sum(1 for r in records if r.get("status") == "Expiring Soon"),
        "Expired": sum(1 for r in records if r.get("status") == "Expired"),
        "No Expiration": sum(1 for r in records if r.get("status") == "No Expiration")
    }

    return render_template("employee_certifications.html", records=records, counts=counts)


@app.route("/participant-checkin-checkout", methods=["GET", "POST"])
def participant_checkin_checkout():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    from datetime import datetime, timedelta

    participants = load_participants()

    if request.method == "POST":
        pid = request.form.get("participant_id", "").strip()
        action = request.form.get("action", "").strip()
        notes = request.form.get("notes", "").strip()
        destination = request.form.get("destination", "").strip()
        expected_return = request.form.get("expected_return", "").strip()

        if pid.isdigit() and int(pid) < len(participants):
            participant = participants[int(pid)]
            logs = participant.setdefault("checkin_checkout_logs", [])

            logs.append({
                "timestamp": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
                "action": action,
                "destination": destination,
                "expected_return": expected_return,
                "notes": notes
            })

            if action == "Check In":
                participant["current_check_status"] = "Checked In"
            elif action == "Check Out":
                participant["current_check_status"] = "Checked Out"

            save_participants(participants)
            return redirect(url_for("participant_checkin_checkout"))

    return render_template("participant_checkin_checkout.html", participants=participants)


@app.route("/individual-service-plan/<int:id>", methods=["GET", "POST"])
def individual_service_plan(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    participants = load_participants()
    participant = participants[id]
    forms = participant.setdefault("forms", {})
    record = forms.setdefault("individual_service_plan", {})
    d = record.get("data", {})

    if record.get("locked"):
        return redirect(url_for("individual_service_plan_print", id=id))

    if request.method == "POST":
        d = request.form.to_dict()
        _identity_participant = locals().get("participant") or locals().get("p")
        if _identity_participant:
            d = lock_participant_identity_fields(d, _identity_participant)
        record["data"] = d
        record["completed"] = True
        record["locked"] = False
        save_participants(participants)
        return redirect(url_for("individual_service_plan_print", id=id))

    today = datetime.now().strftime("%Y-%m-%d")
    return render_template(
        "individual_service_plan_form.html",
        id=id,
        participant=participant,
        d=d,
        today=today
    )



@app.route("/individual-service-plan-print/<int:id>")
def individual_service_plan_print(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    participants = load_participants()
    participant = participants[id]
    record = participant.get("forms", {}).get("individual_service_plan", {})
    d = record.get("data", {})
    locked = record.get("locked", False)

    return render_template(
        "individual_service_plan_print.html",
        id=id,
        participant=participant,
        d=d,
        locked=locked
    )



@app.route("/individual-service-plan-final/<int:id>")
def individual_service_plan_final(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    participants = load_participants()
    participant = participants[id]
    forms = participant.setdefault("forms", {})
    record = forms.setdefault("individual_service_plan", {})
    record["locked"] = True
    record["completed"] = True
    save_participants(participants)

    return redirect(url_for("individual_service_plan_print", id=id))


@app.route("/service-activity-record/<int:id>", methods=["GET", "POST"])
def service_activity_record(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    participants = load_participants()
    participant = participants[id]
    forms = participant.setdefault("forms", {})
    record = forms.setdefault("service_activity_record", {})

    # Repeatable PID-linked record:
    # Service Activity is an ongoing dated log, so each save creates a new entry.
    entries = record.setdefault("entries", [])

    # Safety migration: preserve any older single saved record before switching to entries.
    if record.get("data") and not entries:
        old_entry = dict(record.get("data", {}))
        old_entry.setdefault("locked", record.get("locked", False))
        entries.append(old_entry)
        record["current_entry"] = 0
        record.pop("data", None)

    if request.method == "POST":
        d = request.form.to_dict()
        _identity_participant = locals().get("participant") or locals().get("p")
        if _identity_participant:
            d = lock_participant_identity_fields(d, _identity_participant)
        d["locked"] = False
        entries.append(d)
        record["current_entry"] = len(entries) - 1
        record["completed"] = True
        record["locked"] = False
        save_participants(participants)
        return redirect(url_for("service_activity_record_print", id=id))

    # Blank form for each new service activity entry.
    d = {}
    today = datetime.now().strftime("%Y-%m-%d")
    return render_template(
        "service_activity_record_form.html",
        id=id,
        participant=participant,
        d=d,
        today=today
    )


@app.route("/service-activity-record-print/<int:id>")
def service_activity_record_print(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    participants = load_participants()
    participant = participants[id]
    record = participant.get("forms", {}).get("service_activity_record", {})
    entries = record.get("entries", [])

    # Safety fallback for old saved data.
    if not entries and record.get("data"):
        entries = [record.get("data", {})]

    current_entry = record.get("current_entry", len(entries) - 1 if entries else 0)
    if entries:
        try:
            current_entry = int(current_entry)
        except Exception:
            current_entry = len(entries) - 1
        current_entry = max(0, min(current_entry, len(entries) - 1))
        d = entries[current_entry]
    else:
        d = {}

    locked = d.get("locked", False)

    return render_template(
        "service_activity_record_print.html",
        id=id,
        participant=participant,
        d=d,
        locked=locked,
        entries=entries,
        current_entry=current_entry
    )


@app.route("/service-activity-record-final/<int:id>")
def service_activity_record_final(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    participants = load_participants()
    participant = participants[id]
    forms = participant.setdefault("forms", {})
    record = forms.setdefault("service_activity_record", {})
    entries = record.setdefault("entries", [])

    # Safety migration for old single-record data.
    if record.get("data") and not entries:
        old_entry = dict(record.get("data", {}))
        entries.append(old_entry)
        record["current_entry"] = 0
        record.pop("data", None)

    current_entry = record.get("current_entry", len(entries) - 1 if entries else 0)
    if entries:
        try:
            current_entry = int(current_entry)
        except Exception:
            current_entry = len(entries) - 1
        current_entry = max(0, min(current_entry, len(entries) - 1))
        entries[current_entry]["locked"] = True

    record["completed"] = True
    record["locked"] = False
    save_participants(participants)

    return redirect(url_for("service_activity_record_print", id=id))


@app.route("/participant-program-adherence-review/<int:id>", methods=["GET", "POST"])
def participant_program_adherence_review(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    participants = load_participants()
    participant = participants[id]
    forms = participant.setdefault("forms", {})
    record = forms.setdefault("participant_program_adherence_review", {})
    d = record.get("data", {})

    if record.get("locked"):
        return redirect(url_for("participant_program_adherence_review_print", id=id))

    if request.method == "POST":
        d = request.form.to_dict()
        _identity_participant = locals().get("participant") or locals().get("p")
        if _identity_participant:
            d = lock_participant_identity_fields(d, _identity_participant)
        record["data"] = d
        record["completed"] = True
        record["locked"] = False
        save_participants(participants)
        return redirect(url_for("participant_program_adherence_review_print", id=id))

    today = datetime.now().strftime("%Y-%m-%d")
    return render_template(
        "participant_program_adherence_review_form.html",
        id=id,
        participant=participant,
        d=d,
        today=today
    )


@app.route("/participant-program-adherence-review-print/<int:id>")
def participant_program_adherence_review_print(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    participants = load_participants()
    participant = participants[id]
    record = participant.get("forms", {}).get("participant_program_adherence_review", {})
    d = record.get("data", {})
    locked = record.get("locked", False)

    return render_template(
        "participant_program_adherence_review_print.html",
        id=id,
        participant=participant,
        d=d,
        locked=locked
    )


@app.route("/participant-program-adherence-review-final/<int:id>")
def participant_program_adherence_review_final(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    participants = load_participants()
    participant = participants[id]
    forms = participant.setdefault("forms", {})
    record = forms.setdefault("participant_program_adherence_review", {})
    record["locked"] = True
    record["completed"] = True
    save_participants(participants)

    return redirect(url_for("participant_program_adherence_review_print", id=id))


@app.route("/service-coordination")
def service_coordination():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    participants = load_participants()
    return render_template("service_coordination.html", participants=participants)


@app.route("/single-form-print")
def single_form_print_center():
    activated_system = load_activation()
    current_participants = load_participants()

    single_form_routes = [
        {"title": "Initial Intake Assessment", "key": "intake_assessment", "print_route": "/intake-assessment-print/{id}"},
        {"title": "Master License Agreement", "key": "mla", "print_route": "/mla-print/{id}"},
        {"title": "Release of Information / Authorization to Communicate", "key": "release_of_information", "print_route": "/release-of-information-print/{id}"},
        {"title": "Program Compliance Addendum", "key": "program_compliance_addendum", "print_route": "/program-compliance-addendum-print/{id}"},
        {"title": "Program Participation Agreement", "key": "program_participation_agreement", "print_route": "/program-participation-agreement-print/{id}"},
        {"title": "House Rules & Community Standards", "key": "house_rules", "print_route": "/house-rules-print/{id}"},
        {"title": "Fire Safety & Self-Preservation", "key": "fire_safety", "print_route": "/fire-safety-print/{id}"},
        {"title": "Emergency Contact", "key": "emergency_contact", "print_route": "/emergency-contact-print/{id}"},
        {"title": "Emergency Evacuation", "key": "emergency_evacuation", "print_route": "/emergency-evacuation-print/{id}"},
        {"title": "Guest Addendum", "key": "guest_addendum", "print_route": "/guest-addendum-print/{id}"},
        {"title": "Common Area Security", "key": "common_area_security", "print_route": "/common-area-security-print/{id}"},
        {"title": "Personal Belongings", "key": "personal_belongings", "print_route": "/personal-belongings-print/{id}"},
        {"title": "Property Belongings", "key": "property_belongings", "print_route": "/property-belongings-print/{id}"},
        {"title": "Pet Animal Information", "key": "pet_animal", "print_route": "/pet-animal-print/{id}"},
        {"title": "Privacy Acknowledgment", "key": "privacy_acknowledgment", "print_route": "/privacy-acknowledgment-print/{id}"},
        {"title": "Privacy Noncommercial", "key": "privacy_noncommercial", "print_route": "/privacy-noncommercial-print/{id}"},
        {"title": "Vehicle Parking", "key": "vehicle_parking", "print_route": "/vehicle-parking-print/{id}"},
        {"title": "Transfer Form", "key": "transfer", "print_route": "/transfer-print/{id}"},
        {"title": "Security Camera Disclosure", "key": "security_camera", "print_route": "/security-camera-print/{id}"},
        {"title": "Voluntary Participation", "key": "voluntary_participation", "print_route": "/voluntary-participation-print/{id}"},
        {"title": "Incident Report", "key": "incident_report", "print_route": "/incident-report-print/{id}"},
        {"title": "ACH Authorization", "key": "ach_authorization", "print_route": "/ach-print/{id}"},
        {"title": "No Services / No Supervision", "key": "no_services_supervision", "print_route": "/no-services-supervision-print/{id}"},
        {"title": "Independent Living Disclosure", "key": "independent_living_disclosure", "print_route": "/independent-living-disclosure-print/{id}"},
        {"title": "Vehicle Parking Information", "key": "vehicle_parking", "print_route": "/vehicle-parking-print/{id}"},
        {"title": "Member Bill of Dignity", "key": "bill_of_dignity", "print_route": "/bill-of-dignity-print/{id}"}
    ]

    return render_template(
        "single_form_print.html",
        activation=activated_system,
        participants=current_participants,
        single_form_routes=single_form_routes
    )


@app.route("/single-form-upload", methods=["POST"])
def single_form_upload():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    participants = load_participants()

    pid_raw = request.form.get("upload_pid", "").strip()
    category = request.form.get("document_category", "Other").strip() or "Other"
    note = request.form.get("upload_note", "").strip()
    uploaded_file = request.files.get("participant_document")

    if not pid_raw.isdigit():
        return redirect(url_for("single_form_print_center"))

    pid = int(pid_raw)
    if pid < 0 or pid >= len(participants):
        return redirect(url_for("single_form_print_center"))

    if not uploaded_file or uploaded_file.filename == "":
        return redirect(url_for("single_form_print_center"))

    original_name = uploaded_file.filename
    safe_name = secure_filename(original_name)

    if not safe_name:
        return redirect(url_for("single_form_print_center"))

    allowed_extensions = {"pdf", "png", "jpg", "jpeg", "doc", "docx", "txt", "xls", "xlsx", "csv"}
    ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""

    if ext not in allowed_extensions:
        return redirect(url_for("single_form_print_center"))

    upload_dir = Path("static/uploads") / f"participant_{pid}"
    upload_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stored_name = f"{timestamp}_{safe_name}"
    upload_path = upload_dir / stored_name
    uploaded_file.save(upload_path)

    participants[pid].setdefault("uploads", [])
    participants[pid]["uploads"].append({
        "category": category,
        "note": note,
        "original_name": original_name,
        "stored_name": stored_name,
        "path": str(upload_path),
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

    save_participants(participants)
    return redirect(url_for("single_form_print_center"))


@app.route("/participant-upload/<int:pid>/<path:filename>")
def participant_upload_file(pid, filename):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    folder = Path("static/uploads") / f"participant_{pid}"
    return send_from_directory(folder, filename)



@app.route("/admin-forms")
def admin_forms():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    activated_system = load_activation()
    current_participants = load_participants()
    return render_template("admin_forms.html", activation=activated_system, participants=current_participants)

@app.route("/switch-program/<program>")
def switch_program(program):
    allowed = ["ILH", "Transitional", "VA_GPD_Aligned", "DOC_Reentry_Aligned", "Reentry"]
    if program not in allowed:
        return "Program not allowed", 400

    activated_system = load_activation()
    if not activated_system:
        return redirect(url_for("activate"))

    activated_system["program_type"] = program
    save_activation(activated_system)
    return redirect("/")





# ============================================================





# ============================================================
# ENTRY SCREENING NORMALIZED FINAL
# Stores under participant["forms"]["entry_screening"]
# Saves PDF under static/filled/participant_<PID>/entry_screening.pdf
# ============================================================

ENTRY_SCREENING_CHECKBOX_KEYS = [
    "core_independent_housing",
    "core_daily_living",
    "core_no_services",
    "core_seek_help",
    "core_responsibility",
    "daily_hygiene",
    "daily_meals",
    "daily_laundry",
    "daily_schedule",
    "health_meds",
    "health_appointments",
    "health_emergency",
    "health_911",
    "decision_risks",
    "decision_choices",
    "decision_refuse",
    "housing_lease",
    "housing_shared",
    "housing_violence",
    "housing_conflicts",
    "visitor_control",
    "visitor_not_staff",
    "visitor_not_supervised",
    "visitor_responsibility",
    "boundary_no_personal_care",
    "boundary_no_medical",
    "boundary_no_monitoring",
    "boundary_no_intervention",
    "boundary_no_relationships",
    "boundary_no_outcomes",
]

ENTRY_SCREENING_TEXT_KEYS = [
    "applicant_name",
    "applicant_signature",
    "applicant_date",
    "admin_determination",
    "staff_signature",
    "staff_date",
]


def _entry_screening_save(current_participants):
    try:
        save_participants(current_participants)
    except TypeError:
        globals()["participants"] = current_participants
        save_participants()


def _entry_screening_folder(id):
    from pathlib import Path
    folder = Path("static") / "filled" / f"participant_{id}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _entry_screening_pdf(id):
    return _entry_screening_folder(id) / "entry_screening.pdf"


def _entry_screening_collect_form_data():
    form_data = {}

    for key in ENTRY_SCREENING_CHECKBOX_KEYS:
        form_data[key] = "yes" if request.form.get(key) else ""

    for key in ENTRY_SCREENING_TEXT_KEYS:
        form_data[key] = request.form.get(key, "").strip()

    return form_data


def _entry_screening_make_pdf(id):
    from pathlib import Path

    pdf_path = _entry_screening_pdf(id)

    # Remove old wrong-location completed PDF if it exists.
    for old in [
        Path("static/forms/entry_screening.pdf"),
        Path("static/forms/entry-screening.pdf"),
        Path("static/forms/Entry_Screening.pdf"),
    ]:
        try:
            if old.exists():
                old.unlink()
        except Exception:
            pass

    # Preferred true-to-sight app PDF engine.
    if "generate_true_to_sight_pdf" in globals():
        attempts = [
            (id, "/entry-screening-print/{id}", str(pdf_path)),
            (id, f"/entry-screening-print/{id}", str(pdf_path)),
            (id, "entry_screening_print.html", str(pdf_path)),
        ]
        for args in attempts:
            try:
                generate_true_to_sight_pdf(*args)
                if pdf_path.exists() and pdf_path.stat().st_size > 0:
                    return str(pdf_path)
            except Exception:
                pass

    # Fallback PDF so a PDF is still created in the right folder.
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch

    current_participants = load_participants()
    participant = current_participants[id]
    data = participant.get("forms", {}).get("entry_screening", {}).get("data", {})

    c = canvas.Canvas(str(pdf_path), pagesize=letter)
    width, height = letter
    y = height - 0.55 * inch

    def line(text="", size=8.5, gap=12):
        nonlocal y
        if y < 0.65 * inch:
            c.showPage()
            y = height - 0.55 * inch
        c.setFont("Helvetica", size)
        c.drawString(0.55 * inch, y, str(text)[:112])
        y -= gap

    def heading(text):
        line("", 8, 5)
        line(text, 10.5, 15)

    def check(key, label):
        mark = "YES" if data.get(key) else "NO"
        line(f"[{mark}] {label}", 8.2, 10.5)

    line("ENTRY SCREENING & SELF-DETERMINATION ACKNOWLEDGMENT", 12, 17)
    line("© 2026 Pearlzz LLC. All Rights Reserved.", 8, 13)
    line("Purpose: This screening confirms capacity for self-directed living without services or supervision.", 8.5, 11)
    line("It does not assess worthiness, behavior control, social preferences, or relationship choices.", 8.5, 14)

    heading("SECTION 1 — CORE ELIGIBILITY")
    check("core_independent_housing", "I understand this is independent housing, not assisted or supervised living.")
    check("core_daily_living", "I am capable of performing all daily living activities independently.")
    check("core_no_services", "I understand no personal care, medical care, or supervision is provided.")
    check("core_seek_help", "I can independently seek help when needed.")
    check("core_responsibility", "I accept full responsibility for my health, safety, and personal decisions.")

    heading("SECTION 2 — FUNCTIONAL SELF-MANAGEMENT SCREEN")
    check("daily_hygiene", "Manages personal hygiene independently")
    check("daily_meals", "Prepares or obtains meals independently")
    check("daily_laundry", "Manages laundry and housekeeping independently")
    check("daily_schedule", "Maintains a personal schedule without reminders")
    check("health_meds", "Manages medications independently, if applicable")
    check("health_appointments", "Schedules and attends medical appointments independently")
    check("health_emergency", "Can identify when emergency services are needed")
    check("health_911", "Can call 911 or emergency contacts without assistance")
    check("decision_risks", "Understands personal risks and consequences")
    check("decision_choices", "Makes informed choices without staff direction")
    check("decision_refuse", "Can refuse services or seek them externally if desired")

    heading("SECTION 3 — HOUSING & COMMUNITY COMPATIBILITY")
    check("housing_lease", "Can comply with a normal lease or occupancy agreement")
    check("housing_shared", "Can coexist in shared housing without supervision")
    check("housing_violence", "No history of violent behavior toward others")
    check("housing_conflicts", "Understands conflicts are handled personally, not by staff intervention")

    heading("SECTION 4 — VISITOR & RELATIONSHIP AFFIRMATION")
    check("visitor_control", "I understand I control my personal relationships and visitors.")
    check("visitor_not_staff", "I understand visitors are not staff, not services, and not program activity.")
    check("visitor_not_supervised", "I understand the program does not supervise or manage my visitors.")
    check("visitor_responsibility", "I understand I am responsible for my guests' conduct.")

    heading("SECTION 5 — PROGRAM BOUNDARIES ACKNOWLEDGMENT")
    check("boundary_no_personal_care", "Does not provide personal care")
    check("boundary_no_medical", "Does not provide medical services")
    check("boundary_no_monitoring", "Does not monitor daily activities")
    check("boundary_no_intervention", "Does not intervene in personal decisions")
    check("boundary_no_relationships", "Does not manage relationships or visitors")
    check("boundary_no_outcomes", "Is not responsible for participant outcomes")

    heading("SECTION 6 — SELF-DETERMINATION STATEMENT")
    line('"I am choosing Independent Living voluntarily. I understand that autonomy includes risk, responsibility,', 8, 10)
    line('and personal decision-making. I do not expect supervision, care, or control from this program."', 8, 13)
    line(f"Applicant Name: {data.get('applicant_name', '')}", 9, 13)
    line(f"Signature: {data.get('applicant_signature', '')}", 9, 13)
    line(f"Date: {data.get('applicant_date', '')}", 9, 13)

    heading("SECTION 7 — ADMINISTRATIVE DETERMINATION")
    line(f"Determination: {data.get('admin_determination', '')}", 9, 13)
    line("Decision is based solely on functional independence and informed choice.", 8, 12)
    line(f"Staff Signature: {data.get('staff_signature', '')}", 9, 13)
    line(f"Date: {data.get('staff_date', '')}", 9, 13)

    line("", 8, 8)
    line("This screening verifies independence by documenting self-direction and informed choice,", 8, 10)
    line("not by restricting adult freedoms.", 8, 13)
    line("© 2026 Pearlzz LLC. All Rights Reserved. Single-Site License Granted to Purchaser.", 7, 9)
    line("Unauthorized Reproduction, Distribution, or Derivative Use Prohibited.", 7, 9)

    c.save()
    return str(pdf_path)



@app.route("/entry-screening/<int:id>", methods=["GET", "POST"])
def entry_screening(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    current_participants = load_participants()

    if id < 0 or id >= len(current_participants):
        return redirect(url_for("add_participant"))

    participant = current_participants[id]
    participant.setdefault("forms", {})
    entry = participant["forms"].get("entry_screening", {})
    data = entry.get("data", {})

    if request.method == "POST":
        form_data = _entry_screening_collect_form_data()

        old_pdf = entry.get("pdf", "")

        participant["forms"]["entry_screening"] = {
            "data": form_data,
            "locked": False,
            "completed": False,
            "pdf": old_pdf
        }

        # Remove old loose/special storage.
        participant.pop("entry_screening", None)
        participant.pop("entry_screening_pdf", None)

        _entry_screening_save(current_participants)

        return redirect(url_for("entry_screening_print", id=id))

    if entry.get("locked"):
        return redirect(url_for("entry_screening_print", id=id))

    return render_template(
        "entry_screening_form.html",
        participant=participant,
        id=id,
        data=data,
        entry=entry
    )


@app.route("/entry-screening-print/<int:id>")
def entry_screening_print(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    current_participants = load_participants()

    if id < 0 or id >= len(current_participants):
        return redirect(url_for("add_participant"))

    participant = current_participants[id]
    participant.setdefault("forms", {})
    entry = participant["forms"].get("entry_screening", {})
    data = entry.get("data", {})

    return render_template(
        "entry_screening_print.html",
        participant=participant,
        id=id,
        data=data,
        entry=entry
    )


@app.route("/entry-screening-final/<int:id>", methods=["GET", "POST"])
def entry_screening_final(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    current_participants = load_participants()

    if id < 0 or id >= len(current_participants):
        return redirect(url_for("add_participant"))

    participant = current_participants[id]
    participant.setdefault("forms", {})
    entry = participant["forms"].get("entry_screening", {})
    data = entry.get("data", {})

    if not data:
        return redirect(url_for("entry_screening", id=id))

    pdf_path = _entry_screening_make_pdf(id)

    participant["forms"]["entry_screening"] = {
        "data": data,
        "locked": True,
        "completed": True,
        "pdf": pdf_path
    }

    # Remove old loose/special storage.
    participant.pop("entry_screening", None)
    participant.pop("entry_screening_pdf", None)

    _entry_screening_save(current_participants)

    return redirect(url_for("packet_builder", id=id))


@app.route("/entry-screening-unlock/<int:id>", methods=["GET", "POST"])
def entry_screening_unlock(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    current_participants = load_participants()

    if id < 0 or id >= len(current_participants):
        return redirect(url_for("add_participant"))

    participant = current_participants[id]
    participant.setdefault("forms", {})
    participant["forms"].setdefault("entry_screening", {"data": {}, "pdf": ""})
    participant["forms"]["entry_screening"]["locked"] = False
    participant["forms"]["entry_screening"]["completed"] = False

    participant.pop("entry_screening", None)
    participant.pop("entry_screening_pdf", None)

    _entry_screening_save(current_participants)

    return redirect(url_for("entry_screening", id=id))

# ============================================================
# END ENTRY SCREENING NORMALIZED FINAL
# ============================================================



@app.route("/packet-builder", methods=["GET", "POST"])
def packet_builder_select():
    live_participants = load_participants()

    if request.method == "POST":
        pid = request.form.get("pid", "").strip()
        action = request.form.get("action", "open")

        if pid.isdigit():
            pid = int(pid)
            if 0 <= pid < len(live_participants):
                if action == "download":
                    return redirect(url_for("download_packet", id=pid))
                return redirect(url_for("packet_builder", id=pid))

    return render_template("packet_builder_select.html", participants=live_participants)


@app.route("/packet-builder/<int:id>")
def packet_builder(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    participants = load_participants()

    if id < 0 or id >= len(participants):
        return redirect(url_for("add_participant"))

    activated_system = load_activation()
    participant = participants[id]
    program_type = participant.get("program_type") or participant.get("housing_type") or activated_system.get("program_type", "ILH")
    docs = CORE_DOCS_BY_PROGRAM.get(program_type, [])
    if program_type in DUAL_ENTITY_PROGRAMS:
        base_docs = CORE_DOCS_BY_PROGRAM.get("Transitional", [])
        extra_docs = CORE_DOCS_BY_PROGRAM.get(program_type, [])
        seen = set()
        docs = []
        for item in base_docs + extra_docs:
            key = item.get("file")
            if key not in seen:
                docs.append(item)
                seen.add(key)
    if program_type not in ["ILH", "PSH"]:
        existing_files = {item.get("file") for item in docs}
        for shared_doc in get_active_shared_housing_forms():
            if shared_doc.get("file") not in existing_files:
                docs.append(shared_doc)
                existing_files.add(shared_doc.get("file"))

    items = ""
    for doc in docs:
        if doc["file"].startswith("/"):
            link = doc["file"].replace("{id}", str(id))
        elif doc["file"] == "06_direct_deposit_ach_authorization.pdf":
            link = f"/ach-test/{id}"
        else:
            link = f"/form/{id}/{doc['file']}"

        form_key = doc["file"].strip("/").replace("/{id}", "").replace("{id}", "").strip("/").replace("-", "_").replace(".pdf", "")
        forms_status = participant.get("forms", {}).get(form_key, {})
        legacy_status = participant.get("form_data", {}).get(doc["file"], {})
        completed = "Completed" if forms_status.get("completed") or legacy_status.get("completed") else "Not Completed"
        if doc.get("vault"):
            items += f"<li class='vault-card'><a href='{link}'>🔒 ⛓️ {doc['title']}</a><div class='vault-subtitle'>Restricted Identity / PII Record</div><div class='vault-status'>Status: {completed}</div></li>"
        else:
            items += f"<li><a href='{link}'>{doc['title']}</a> - {completed}</li>"

    return f"""
    <html>
    <head>
        <title>Packet Builder</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f5f5f5;
                margin: 0;
                padding: 30px;
                color: #111;
            }}
            .page {{
                max-width: 950px;
                margin: auto;
                background: white;
                padding: 30px;
                border-radius: 14px;
                box-shadow: 0 4px 14px rgba(0,0,0,0.12);
            }}
            h1 {{
                text-align: center;
                margin-bottom: 25px;
            }}
            .info {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 12px;
                background: #fafafa;
                border: 1px solid #ddd;
                padding: 18px;
                border-radius: 10px;
                margin-bottom: 24px;
            }}
            .forms-box {{
                border: 1px solid #ddd;
                padding: 20px;
                border-radius: 10px;
                background: #fff;
            }}
            li {{
                margin: 10px 0;
                line-height: 1.4;
            }}
            a {{
                color: #4b2aa8;
                font-weight: bold;
            }}
              .vault-card {{ list-style:none; margin:16px 0; padding:16px 18px; background:#050505; border:2px solid #D4AF37; border-radius:14px; }}
              .vault-card a {{ color:#D4AF37; font-size:20px; text-decoration:none; }}
              .vault-subtitle {{ color:white; font-size:13px; margin-top:6px; font-weight:bold; }}
              .vault-status {{ color:#D4AF37; font-size:13px; margin-top:6px; }}
            .bottom-buttons {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 20px;
                margin-top: 28px;
            }}
            button {{
                padding: 14px 24px;
                font-size: 16px;
                background: #D4AF37;
                color: black;
                border: 0;
                border-radius: 8px;
                cursor: pointer;
                font-weight: bold;
            }}
            button:hover {{
                opacity: 0.9;
            }}
            .note {{
                margin-top: 24px;
                font-style: italic;
                color: #444;
            }}
        </style>
    </head>
    <body>
        <div class="page">
            <h1>Packet Builder Page</h1>

            <div class="info">
                <div><strong>Participant:</strong> {participant["name"]}</div>
                <div><strong>PID:</strong> {id}</div>
                <div><strong>Program Type:</strong> {activated_system.get("program_type","ILH")}</div>
            </div>

            <div class="forms-box">
                <h2>Forms in Packet</h2>
                <ol>
                    {items}
                </ol>
            </div>

            <div class="bottom-buttons">
                <a href="/operations">
                    <button>Exit to Operations</button>
                </a>

                <a href="/billing-invoice-setup/{id}">
                    <button>Billing / Invoice Setup</button>
                </a>

                <a href="/download-packet/{id}">
                    <button>Download Packet</button>
                </a>
            </div>

            <p class="note">Entry process continues here.</p>
        </div>
    </body>
    </html>
    """









@app.route("/participant-complete/<int:id>")
def participant_complete(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if id < 0 or id >= len(participants):
        return redirect(url_for("add_participant"))

    participant = participants[id]
    return render_template("participant_complete.html", participant=participant, id=id)


@app.route("/form/<int:id>/ach_authorization", methods=["GET", "POST"])
def ach_authorization(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if id >= len(participants):
        flash("Participant not found")
        return redirect(url_for("add_participant"))

    participant = participants[id]
    participant.setdefault("forms", {})
    participant["forms"].setdefault("ach_authorization", {})

    if request.method == "POST":
        data = request.form.to_dict(flat=True)
        _identity_participant = locals().get("participant") or locals().get("p")
        if _identity_participant:
            data = lock_participant_identity_fields(data, _identity_participant)
        action = data.pop("action", "save")
        participant["forms"]["ach_authorization"]["data"] = data

        print("ACH SAVED FOR:", participant.get("name", "UNKNOWN"))
        print("ACH DATA:", data)

        pdf_data = {
            "participant_name": data.get("participant_name", ""),
            "bank_name": data.get("bank_name", ""),
            "routing_number": data.get("routing_number", ""),
            "account_number": data.get("account_number", ""),
            "account_holder_If_not_participant": data.get("account_holder_If_not_participant", ""),
            "participant_signature": data.get("participant_signature", ""),
            "signature_date": data.get("signature_date", "")
        }

        account_type = data.get("account_type", "").strip().lower()
        if account_type == "checking":
            pdf_data["account_type"] = "/checking"
        elif account_type == "savings":
            pdf_data["account_type"] = "/savings"
        else:
            pdf_data["account_type"] = "/Off"

        src = os.path.join("static", "forms", "06_direct_deposit_ach_authorization.pdf")
        out = os.path.join("static", "filled", f"{id}_06_direct_deposit_ach_authorization_filled.pdf")

        try:
            fill_one_pdf_universal(src, out, participant)
            participant["forms"]["ach_authorization"]["pdf"] = out
            print("ACH PDF CREATED:", out)
        except Exception as e:
            print("ACH PDF ERROR:", e)
            flash(f"ACH saved, but PDF failed: {e}")
            return redirect(url_for("ach_authorization", id=id))

        if action == "next":
            flash("ACH saved and PDF created.")
            return redirect(url_for("ach_authorization", id=id))

        flash("ACH saved and PDF created.")
        return redirect(url_for("ach_authorization", id=id))

    form_data = participant["forms"]["ach_authorization"].get("data", {})
    return render_template(
        "ach_authorization.html",
        participant=participant,
        form_data=form_data
    )



def _safe_str(value):
    if value is None:
        return ""
    return str(value).strip()

def _load_mapper():
    path = Path("static/mapper/mappings.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _participant_common_data(participant):
    return {
        "participant_name": _safe_str(participant.get("name", "")),
        "participant_signature": _safe_str(participant.get("signature", participant.get("name", ""))),
        "signature_date": _safe_str(participant.get("signature_date", "")),
        "bank_account_holder": _safe_str(participant.get("name", "")),
    }

def _form_specific_data(participant, form_stem):
    forms = participant.get("forms", {}) or {}

    aliases = {
        "06_direct_deposit_ach_authorization": "ach_authorization",
        "08_reported_occurrence_form": "reported_occurrence",
        "09_guest_addendum": "guest_addendum",
        "10_pet_animal_information_sheet": "pet_animal_information_sheet",
        "12_transfer_form": "transfer_form",
        "14_pre_opening_waitlist_form": "pre_opening_waitlist_form",
        "common_area_security": "common_area_security",
        "communication_consent": "communication_consent",
        "emergency_contact": "emergency_contact",
        "financial_responsibility": "financial_responsibility",
        "fire_safety_self_preservation": "fire_safety_self_preservation",
        "house_rules": "house_rules",
        "independent_living_disclosure": "independent_living_disclosure",
        "no_payee_financial_control": "no_payee_financial_control",
        "personal_belongings": "personal_belongings",
        "privacy_non_commercialization": "privacy_non_commercialization",
        "voluntary_participation": "voluntary_participation",
    }

    form_key = aliases.get(form_stem, form_stem)
    payload = forms.get(form_key, {}) or {}
    if isinstance(payload, dict):
        payload = payload.get("data", payload)
    if not isinstance(payload, dict):
        return {}
    return {k: _safe_str(v) for k, v in payload.items()}

def _draw_overlay(src_pdf, writer, mapping_key, draw_data):
    mapper = _load_mapper()
    page_maps = mapper.get(mapping_key, {}) or {}
    if not page_maps:
        return False

    src_reader = PdfReader(src_pdf)

    for page_index_str, marks in page_maps.items():
        try:
            page_index = int(page_index_str)
        except Exception:
            continue

        if page_index >= len(writer.pages) or page_index >= len(src_reader.pages):
            continue

        src_page = src_reader.pages[page_index]
        width = float(src_page.mediabox.width)
        height = float(src_page.mediabox.height)

        mapper_doc = fitz.open(src_pdf)
        mapper_page = mapper_doc[page_index]
        pix = mapper_page.get_pixmap()
        img_w = float(pix.width)
        img_h = float(pix.height)

        x_scale = width / img_w if img_w else 1.0
        y_scale = height / img_h if img_h else 1.0

        packet = BytesIO()
        c = canvas.Canvas(packet, pagesize=(width, height))
        drew_any = False

        for mark in marks:
            field = _safe_str(mark.get("field"))
            raw_x = float(mark.get("x", 0))
            raw_y = float(mark.get("y", 0))

            x = raw_x * x_scale
            y = raw_y * y_scale

            value = _safe_str(draw_data.get(field, ""))
            if not value or value.lower() == field.lower():
                continue

            c.setFont("Helvetica", 10)
            c.drawCentredString(x, height - y, value)
            drew_any = True

        c.save()
        packet.seek(0)

        if drew_any:
            overlay_reader = PdfReader(packet)
            writer.pages[page_index].merge_page(overlay_reader.pages[0])

    return True

def fill_one_pdf_universal(src_pdf, out_pdf, participant):
    os.makedirs(os.path.dirname(out_pdf), exist_ok=True)

    common_data = _participant_common_data(participant)
    form_stem = Path(src_pdf).stem
    specific_data = _form_specific_data(participant, form_stem)

    merged_data = {}
    merged_data.update(common_data)
    merged_data.update(specific_data)

    reader = PdfReader(src_pdf)
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)

    mapping_candidates = []
    for key in [
        src_pdf,
        str(Path(src_pdf)),
        str(Path(src_pdf).resolve()),
        "static/forms/" + Path(src_pdf).name,
    ]:
        if key not in mapping_candidates:
            mapping_candidates.append(key)

    for key in mapping_candidates:
        used = _draw_overlay(src_pdf, writer, key, merged_data)
        if used:
            break

    with open(out_pdf, "wb") as f:
        writer.write(f)

def fill_all_forms_for_participant(participant_id):
    if participant_id >= len(participants):
        raise IndexError("Participant not found")

    participant = participants[participant_id]
    forms_dir = Path("static/forms")
    out_dir = Path("static/filled") / f"participant_{participant_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(forms_dir.glob("*.pdf"))
    made = []

    for pdf in pdfs:
        out_pdf = out_dir / pdf.name
        try:
            fill_one_pdf_universal(str(pdf), str(out_pdf), participant)
            made.append(str(out_pdf))
        except Exception as e:
            fail_path = out_dir / f"FAILED__{pdf.stem}.txt"
            fail_path.write_text(str(e))
            made.append(str(fail_path))

    return made



def fill_ach_pdf_hardcoded(src_pdf, out_pdf, data):
    os.makedirs(os.path.dirname(out_pdf), exist_ok=True)

    reader = PdfReader(src_pdf)
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)

    packet = BytesIO()
    c = canvas.Canvas(packet, pagesize=(612, 792))
    c.setFont("Helvetica", 8)

    participant_name = data.get("participant_name", "")
    bank_name = data.get("bank_name", "")
    routing_number = data.get("routing_number", "")
    account_number = data.get("account_number", "")
    signature = data.get("participant_signature", "")
    signature_date = data.get("signature_date", "")
    holder = data.get("account_holder_If_not_participant") or data.get("account_holder") or ""

    c.drawString(190, 592, participant_name)
    c.drawString(220, 565, bank_name)

    account_type = (data.get("account_type", "") or "").lower()
    if account_type == "checking":
        c.drawString(74, 522, "X")
    if account_type == "savings":
        c.drawString(74, 508, "X")

    c.drawString(190, 475, routing_number)
    c.drawString(190, 448, account_number)

    c.drawString(185, 315, signature)
    c.drawString(98, 288, signature_date)
    c.drawString(330, 262, holder)

    c.save()
    packet.seek(0)

    overlay = PdfReader(packet)
    writer.pages[0].merge_page(overlay.pages[0])

    with open(out_pdf, "wb") as f:
        writer.write(f)



@app.route("/ach-print/<int:id>")
def ach_print(id):
    p = participants[id]
    d = (p.get("forms", {}).get("ach_test", {}).get("data", {})) or {}
    return render_template("ach_print.html", d=d)


@app.route("/ach-test/<int:id>", methods=["GET", "POST"])
def ach_test(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    p.setdefault("forms", {})
    p["forms"].setdefault("ach_test", {})

    if request.method == "POST":
        data = request.form.to_dict()
        _identity_participant = locals().get("participant") or locals().get("p")
        if _identity_participant:
            data = lock_participant_identity_fields(data, _identity_participant)
        p["forms"]["ach_test"]["data"] = data
        p["forms"]["ach_authorization"] = {"data": data}

        src = "static/forms/06_direct_deposit_ach_authorization.pdf"
        out = f"static/filled/participant_{id}/06_direct_deposit_ach_authorization.pdf"

        try:
            fill_ach_pdf_hardcoded(src, out, data)
            p["forms"]["ach_test"]["pdf"] = out
            print("ACH TEST PDF CREATED:", out)
        except Exception as e:
            print("ACH TEST PDF ERROR:", e)

        save_participants_file()
        print("SAVED:", data)

    data = p["forms"]["ach_test"].get("data", {})

    return render_template("ach_hardcoded.html", data=data)


@app.route("/no-services-supervision/<int:id>", methods=["GET", "POST"])
def no_services_supervision(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    p.setdefault("forms", {})
    p["forms"].setdefault("no_services_supervision", {})

    if request.method == "POST":
        data = request.form.to_dict(flat=True)
        _identity_participant = locals().get("participant") or locals().get("p")
        if _identity_participant:
            data = lock_participant_identity_fields(data, _identity_participant)
        data["acknowledge_no_services"] = request.form.get("acknowledge_no_services", "")
        p["forms"]["no_services_supervision"]["data"] = data
        save_participants_file()
        return redirect(url_for("no_services_supervision_print", id=id))

    if p["forms"]["no_services_supervision"].get("locked"):
        return redirect(url_for("no_services_supervision_print", id=id))

    d = p["forms"]["no_services_supervision"].get("data", {})
    return render_template("no_services_supervision_form.html", participant=p, d=d, id=id)


@app.route("/no-services-supervision-print/<int:id>")
def no_services_supervision_print(id):
    p = participants[id]
    locked = p.get("forms", {}).get("no_services_supervision", {}).get("locked", False)
    d = p.get("forms", {}).get("no_services_supervision", {}).get("data", {}) or {}
    return render_template("no_services_supervision_print.html", d=d, id=id, locked=locked)


@app.route("/no-services-supervision-final/<int:id>", methods=["POST"])
def no_services_supervision_final(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    p.setdefault("forms", {})
    p["forms"].setdefault("no_services_supervision", {})
    p["forms"]["no_services_supervision"]["locked"] = True
    p["forms"]["no_services_supervision"]["completed"] = True
    save_participants_file()

    return redirect(f"/common-area-security/{id}")


@app.route("/independent-living-disclosure/<int:id>", methods=["GET", "POST"])
def independent_living_disclosure(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    p.setdefault("forms", {})
    p["forms"].setdefault("independent_living_disclosure", {})

    if request.method == "POST":
        data = request.form.to_dict(flat=True)
        _identity_participant = locals().get("participant") or locals().get("p")
        if _identity_participant:
            data = lock_participant_identity_fields(data, _identity_participant)
        data["acknowledge_disclosure"] = request.form.get("acknowledge_disclosure", "")
        p["forms"]["independent_living_disclosure"]["data"] = data
        save_participants_file()
        return redirect(url_for("independent_living_disclosure_print", id=id))

    if p["forms"]["independent_living_disclosure"].get("locked"):
        return redirect(url_for("independent_living_disclosure_print", id=id))

    d = p["forms"]["independent_living_disclosure"].get("data", {})
    return render_template("independent_living_disclosure_form.html", participant=p, d=d, id=id)


@app.route("/independent-living-disclosure-print/<int:id>")
def independent_living_disclosure_print(id):
    p = participants[id]
    locked = p.get("forms", {}).get("independent_living_disclosure", {}).get("locked", False)
    d = p.get("forms", {}).get("independent_living_disclosure", {}).get("data", {}) or {}
    return render_template("independent_living_disclosure_print.html", d=d, id=id, locked=locked)


@app.route("/independent-living-disclosure-final/<int:id>", methods=["POST"])
def independent_living_disclosure_final(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    p.setdefault("forms", {})
    p["forms"].setdefault("independent_living_disclosure", {})
    p["forms"]["independent_living_disclosure"]["locked"] = True
    p["forms"]["independent_living_disclosure"]["completed"] = True
    save_participants_file()

    return redirect(f"/house-rules/{id}")


@app.route("/vehicle-parking/<int:id>", methods=["GET", "POST"])
def vehicle_parking(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    p.setdefault("forms", {})
    p["forms"].setdefault("vehicle_parking", {})

    if request.method == "POST":
        data = request.form.to_dict(flat=True)
        _identity_participant = locals().get("participant") or locals().get("p")
        if _identity_participant:
            data = lock_participant_identity_fields(data, _identity_participant)

        for cb in [
            "park_designated_spaces",
            "keep_vehicle_registered",
            "no_blocking_driveways",
            "report_vehicle_changes",
        ]:
            data[cb] = request.form.get(cb, "")

        p["forms"]["vehicle_parking"]["data"] = data
        save_participants_file()
        print("VEHICLE PARKING SAVED:", data)
        return redirect(url_for("vehicle_parking_print", id=id))

    if p["forms"]["vehicle_parking"].get("locked"):
        return redirect(url_for("vehicle_parking_print", id=id))

    d = p["forms"]["vehicle_parking"].get("data", {})
    return render_template("vehicle_parking_form.html", participant=p, d=d, id=id)


@app.route("/vehicle-parking-print/<int:id>")
def vehicle_parking_print(id):
    p = participants[id]
    locked = p.get("forms", {}).get("vehicle_parking", {}).get("locked", False)
    d = (p.get("forms", {}).get("vehicle_parking", {}).get("data", {})) or {}
    return render_template("vehicle_parking_print.html", d=d, id=id, locked=locked)



@app.route("/vehicle-parking-unlock/<int:id>")
def vehicle_parking_unlock(id):
    p = participants[id]
    if "vehicle_parking" in p.get("forms", {}):
        p["forms"]["vehicle_parking"]["locked"] = False
        p["forms"]["vehicle_parking"]["completed"] = False
        save_participants(participants)
    return redirect(url_for("vehicle_parking", id=id))

@app.route("/vehicle-parking-final/<int:id>", methods=["POST"])
def vehicle_parking_final(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    p.setdefault("forms", {})
    p["forms"].setdefault("vehicle_parking", {})
    p["forms"]["vehicle_parking"]["locked"] = True
    p["forms"]["vehicle_parking"]["completed"] = True
    save_participants_file()


    return redirect(f"/transfer/{id}")


@app.route("/bill-of-dignity/<int:id>", methods=["GET", "POST"])
def bill_of_dignity(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    p.setdefault("forms", {})
    p["forms"].setdefault("bill_of_dignity", {})

    if request.method == "POST":
        data = request.form.to_dict(flat=True)
        _identity_participant = locals().get("participant") or locals().get("p")
        if _identity_participant:
            data = lock_participant_identity_fields(data, _identity_participant)
        p["forms"]["bill_of_dignity"]["data"] = data
        save_participants_file()
        return redirect(url_for("bill_of_dignity_print", id=id))

    if p["forms"]["bill_of_dignity"].get("locked"):
        return redirect(url_for("bill_of_dignity_print", id=id))

    d = p["forms"]["bill_of_dignity"].get("data", {})
    return render_template("bill_of_dignity_form.html", participant=p, d=d, id=id)


@app.route("/bill-of-dignity-print/<int:id>")
def bill_of_dignity_print(id):
    p = participants[id]
    locked = p.get("forms", {}).get("bill_of_dignity", {}).get("locked", False)
    d = p.get("forms", {}).get("bill_of_dignity", {}).get("data", {})
    return render_template("bill_of_dignity_print.html", d=d, id=id, locked=locked)



@app.route("/bill-of-dignity-unlock/<int:id>")
def bill_of_dignity_unlock(id):
    p = participants[id]
    if "bill_of_dignity" in p.get("forms", {}):
        p["forms"]["bill_of_dignity"]["locked"] = False
        p["forms"]["bill_of_dignity"]["completed"] = False
        save_participants(participants)
    return redirect(url_for("bill_of_dignity", id=id))

@app.route("/bill-of-dignity-final/<int:id>", methods=["POST"])
def bill_of_dignity_final(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    p.setdefault("forms", {})
    p["forms"].setdefault("bill_of_dignity", {})
    p["forms"]["bill_of_dignity"]["locked"] = True
    p["forms"]["bill_of_dignity"]["completed"] = True
    save_participants_file()


    return redirect(f"/participant-complete/{id}")





# =========================
# BAD UNIVERSAL FORM SYSTEM REMOVED — true-to-sight routes preserved


@app.route("/render-pdf-diagnostic")
def render_pdf_diagnostic():
    import os
    from pathlib import Path
    from datetime import datetime, timedelta
    from flask import send_file

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return f"PLAYWRIGHT IMPORT FAILED: {e}", 500

    out_dir = Path("static/filled")
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pdf_path = out_dir / "MR_IR_TIMESTAMP_PDF_DIAGNOSTIC.pdf"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>Mr.IR PDF Diagnostic</title>
      <style>
        body {{
          font-family: Arial, sans-serif;
          padding: 60px;
          color: #111827;
        }}
        .box {{
          border: 4px solid #d4af37;
          border-radius: 18px;
          padding: 35px;
        }}
        h1 {{
          margin-top: 0;
          color: #111827;
        }}
        .pass {{
          font-size: 26px;
          font-weight: bold;
          color: #166534;
        }}
        .small {{
          margin-top: 30px;
          font-size: 13px;
          color: #6b7280;
        }}
      </style>
    </head>
    <body>
      <div class="box">
        <h1>Mr.IR PDF Diagnostic Test</h1>
        <p><strong>App:</strong> NILPF Housing OS</p>
        <p><strong>Engine:</strong> Playwright Chromium PDF</p>
        <p><strong>Timestamp:</strong> {timestamp}</p>
        <p class="pass">STATUS: PASS</p>
        <p>If this PDF opened in the browser, the Render/Docker PDF engine is alive.</p>
        <p class="small">Generated by Mr.IR — Mr. Internal Repairman</p>
      </div>
    </body>
    </html>
    """

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            page = browser.new_page()
            page.set_content(html, wait_until="domcontentloaded")
            page.pdf(
                path=str(pdf_path),
                format="Letter",
                print_background=True
            )
            browser.close()

        if not pdf_path.exists() or pdf_path.stat().st_size == 0:
            return "PDF DIAGNOSTIC FAILED: PDF was not created.", 500

        return send_file(
            str(pdf_path),
            mimetype="application/pdf",
            as_attachment=False,
            download_name="MR_IR_TIMESTAMP_PDF_DIAGNOSTIC.pdf"
        )

    except Exception as e:
        return f"PDF DIAGNOSTIC FAILED: {type(e).__name__}: {e}", 500


@app.route("/download-packet/<int:id>")
def download_packet(id):
    activated_system = load_activation()
    if not activated_system:
        return redirect(url_for("activate"))

    program_type = activated_system.get("program_type", "ILH")
    docs = PACKET_MANIFEST.get(program_type, [])
    if program_type in DUAL_ENTITY_PROGRAMS:
        base_docs = PACKET_MANIFEST.get("Transitional", [])
        extra_docs = PACKET_MANIFEST.get(program_type, [])
        seen = set()
        docs = []
        for item in base_docs + extra_docs:
            key = item.get("completed_file")
            if key not in seen:
                docs.append(item)
                seen.add(key)

    folder = f"static/filled/participant_{id}"
    os.makedirs(folder, exist_ok=True)

    # Build a simple completed-forms summary PDF from participants.json.
    # This makes Download Packet work even before every true-to-sight HTML form
    # has its own individual PDF export.
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch

    if id < 0 or id >= len(participants):
        return "Participant not found for packet download", 404

    participant = participants[id]
    forms = participant.get("forms", {})

    # Generate true-to-sight PDFs during Download Packet.
    # This makes Packet Builder create the real form PDFs instead of only making a summary list.
    FORM_PDF_EXPORTS = {
        "intake_assessment": ("/intake-assessment-print/{id}", "intake_assessment_true_to_sight.pdf"),
        "mla": ("/mla-print/{id}", "mla_true_to_sight.pdf"),
        "program_compliance_addendum": ("/program-compliance-addendum-print/{id}", "program_compliance_addendum_true_to_sight.pdf"),
        "program_participation_agreement": ("/program-participation-agreement-print/{id}", "program_participation_agreement_true_to_sight.pdf"),
        "house_rules": ("/house-rules-print/{id}", "house_rules_true_to_sight.pdf"),
        "fire_safety": ("/fire-safety-print/{id}", "fire_safety_true_to_sight.pdf"),
        "emergency_contact": ("/emergency-contact-print/{id}", "emergency_contact_true_to_sight.pdf"),
        "emergency_evacuation": ("/emergency-evacuation-print/{id}", "emergency_evacuation_true_to_sight.pdf"),
        "guest_addendum": ("/guest-addendum-print/{id}", "guest_addendum_true_to_sight.pdf"),
        "common_area_security": ("/common-area-security-print/{id}", "common_area_security_true_to_sight.pdf"),
        "personal_belongings": ("/personal-belongings-print/{id}", "personal_belongings_true_to_sight.pdf"),
        "property_belongings": ("/property-belongings-print/{id}", "property_belongings_true_to_sight.pdf"),
        "pet_animal": ("/pet-animal-print/{id}", "pet_animal_true_to_sight.pdf"),
        "privacy_acknowledgment": ("/privacy-acknowledgment-print/{id}", "privacy_acknowledgment_true_to_sight.pdf"),
        "privacy_noncommercial": ("/privacy-noncommercial-print/{id}", "privacy_noncommercial_true_to_sight.pdf"),
        "vehicle_parking": ("/vehicle-parking-print/{id}", "vehicle_parking_true_to_sight.pdf"),
        "transfer": ("/transfer-print/{id}", "transfer_true_to_sight.pdf"),
        "security_camera": ("/security-camera-print/{id}", "security_camera_true_to_sight.pdf"),
        "voluntary_participation": ("/voluntary-participation-print/{id}", "voluntary_participation_true_to_sight.pdf"),
        "incident_report": ("/incident-report-print/{id}", "incident_report_true_to_sight.pdf"),
        "release_of_information": ("/release-of-information-print/{id}", "release_of_information_true_to_sight.pdf"),
        "bill_of_dignity": ("/bill-of-dignity-print/{id}", "bill_of_dignity_true_to_sight.pdf"),
    }

    generation_errors = []
    missing_generated_pdfs = []

    for form_key, form_record in forms.items():
        if not (form_record.get("completed") or form_record.get("locked")):
            continue

        export_info = FORM_PDF_EXPORTS.get(form_key)
        if not export_info:
            print(f"No PDF export route mapped for completed form: {form_key}")
            continue

        route_template, pdf_name = export_info
        pdf_path = os.path.join(folder, pdf_name)

        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            print(f"True-to-sight PDF already exists: {pdf_path}")
            continue

        try:
            created_pdf = generate_true_to_sight_pdf(id, route_template, pdf_name)
            print(f"Download Packet generated true-to-sight PDF: {created_pdf}")
        except Exception as e:
            error_msg = f"{form_key}: {type(e).__name__}: {e}"
            generation_errors.append(error_msg)
            print(f"Download Packet could not generate PDF for {form_key}: {e}")

    summary_pdf = os.path.join(folder, "00_COMPLETED_PACKET_SUMMARY.pdf")

    c = canvas.Canvas(summary_pdf, pagesize=letter)
    width, height = letter
    y = height - inch

    c.setFont("Helvetica-Bold", 16)
    c.drawString(inch, y, "NILPF Completed Packet Summary")
    y -= 24

    c.setFont("Helvetica", 10)
    c.drawString(inch, y, f"Participant ID: {id}")
    y -= 16
    c.drawString(inch, y, f"Participant Name: {participant.get('name', '')}")
    y -= 16
    c.drawString(inch, y, f"Program Type: {participant.get('program_type') or participant.get('housing_type') or program_type}")
    y -= 24

    c.setFont("Helvetica-Bold", 12)
    c.drawString(inch, y, "Completed / Locked Forms")
    y -= 18

    c.setFont("Helvetica", 9)
    completed_count = 0
    for form_key, form_record in forms.items():
        if form_record.get("completed") or form_record.get("locked"):
            completed_count += 1
            line = f"{completed_count}. {form_key} | completed={form_record.get('completed')} | locked={form_record.get('locked')}"
            c.drawString(inch, y, line[:110])
            y -= 14
            if y < inch:
                c.showPage()
                y = height - inch
                c.setFont("Helvetica", 9)

    if completed_count == 0:
        c.drawString(inch, y, "No completed forms found in participant record.")

    c.save()

    merger = PdfMerger()
    added = 0

    if os.path.exists(summary_pdf):
        merger.append(summary_pdf)
        added += 1

    # Merge true-to-sight PDFs in FORM_PDF_EXPORTS order.
    # This avoids alphabetical scrambling and prevents duplicate appending.
    for form_key, export_info in FORM_PDF_EXPORTS.items():
        form_record = forms.get(form_key, {})
        if not (form_record.get("completed") or form_record.get("locked")):
            continue

        route_template, pdf_name = export_info
        true_pdf_path = os.path.join(folder, pdf_name)

        if os.path.exists(true_pdf_path) and os.path.getsize(true_pdf_path) > 0:
            try:
                merger.append(true_pdf_path)
                added += 1
                print(f"Added ordered true-to-sight packet PDF: {true_pdf_path}")
            except Exception as e:
                print(f"Could not add ordered true-to-sight PDF {true_pdf_path}: {e}")
        else:
            missing_generated_pdfs.append(f"{form_key} -> {true_pdf_path}")
            print(f"Completed form missing generated true-to-sight PDF: {form_key} -> {true_pdf_path}")

    true_pdf_count = added - 1 if os.path.exists(summary_pdf) else added

    if true_pdf_count <= 0:
        report = [
            "TRUE-TO-SIGHT PDF GENERATION FAILED",
            "",
            "The app did NOT create any real form PDFs.",
            "The summary PDF is being blocked from pretending to be a completed packet.",
            "",
            "Generation errors:"
        ]
        report.extend(generation_errors or ["No Python exception was captured."])
        report.append("")
        report.append("Missing generated PDFs:")
        report.extend(missing_generated_pdfs or ["No missing PDF list was captured."])
        return "<pre>" + "\n".join(report) + "</pre>", 500

    if added == 0:
        return "No completed PDFs found for this program packet", 404

    safe_program = program_type.replace(" ", "_").replace("/", "_")
    output_path = f"static/filled/participant_{id}_{safe_program}_CLEAN_PACKET.pdf"

    

    # --- ADD PRN SECTION AT END ---
    prn_cover = "static/forms/PRN_COVER_PAGE.pdf"
    if os.path.exists(prn_cover):
        merger.append(prn_cover)
    else:
        print("PRN cover page missing")

    PRN_FORMS = [
        "08_reported_occurrence_form.pdf",
        "12_transfer_form.pdf",
        "09_guest_addendum.pdf",
        "17_vehicle_parking_information_form.pdf",
        "07_emergency_contact_form.pdf"
    ]

    for prn_file in PRN_FORMS:
        prn_path = os.path.join(folder, prn_file)
        if os.path.exists(prn_path):
            merger.append(prn_path)

    merger.write(output_path)
    merger.close()

    from flask import send_file
    return send_file(
        output_path,
        as_attachment=True,
        download_name=f"participant_{id}_{safe_program}_CLEAN_PACKET.pdf"
    )



# PROGRAM STRUCTURE RULES
DUAL_ENTITY_PROGRAMS = ["Transitional", "VA_GPD_Aligned", "DOC_Reentry_Aligned", "Reentry"]


STANDARD_NEXT_FORMS = {
    "house-rules": "fire-safety",
    "fire-safety": "emergency-contact",
    "emergency-contact": "emergency-evacuation",
    "emergency-evacuation": "guest-addendum",
    "guest-addendum": "common-area-security",
    "common-area-security": "personal-belongings",
    "personal-belongings": "property-belongings",
    "property-belongings": "pet-animal",
    "pet-animal": "privacy-acknowledgment",
    "privacy-acknowledgment": "privacy-noncommercial",
    "privacy-noncommercial": "vehicle-parking",
    "vehicle-parking": "transfer",
    "transfer": "security-camera",
    "security-camera": "voluntary-participation",
    "voluntary-participation": "incident-report",
    "incident-report": "bill-of-dignity",
    "bill-of-dignity": None,
}

# STANDARD SAVE / REVIEW / FINAL LOCK ROUTE BUILDER
def make_standard_routes(route_name, form_key, template_name, print_template_name=None):
    if print_template_name is None:
        print_template_name = template_name

    endpoint_base = form_key

    def form_view(id):
        participant = participants[id]
        participant.setdefault("forms", {})
        participant["forms"].setdefault(form_key, {"data": {}, "locked": False, "completed": False})

        if participant["forms"][form_key].get("locked"):
            return redirect(url_for(endpoint_base + "_print", id=id))

        if request.method == "POST":
            participant["forms"][form_key] = {
                "data": dict(request.form),
                "locked": False,
                "completed": False
            }
            save_participants(participants)
            return redirect(url_for(endpoint_base + "_print", id=id))

        d = participant["forms"][form_key].get("data", {})
        return render_template(template_name + "_form.html", participant=participant, id=id, d=d)

    form_view.__name__ = endpoint_base
    app.add_url_rule(f"/{route_name}/<int:id>", endpoint_base, form_view, methods=["GET", "POST"])

    def print_view(id):
        participant = participants[id]
        d = participant.get("forms", {}).get(form_key, {}).get("data", {})
        locked = participant.get("forms", {}).get(form_key, {}).get("locked", False)
        return render_template(print_template_name + "_print.html", participant=participant, id=id, d=d, locked=locked)

    print_view.__name__ = endpoint_base + "_print"
    app.add_url_rule(f"/{route_name}-print/<int:id>", endpoint_base + "_print", print_view)

    def final_view(id):
        participants = load_participants()
        if id < 0 or id >= len(participants):
            return redirect(url_for("add_participant"))

        participant = participants[id]
        participant.setdefault("forms", {})
        participant["forms"].setdefault(form_key, {"data": {}})
        participant["forms"][form_key]["locked"] = True
        participant["forms"][form_key]["completed"] = True
        save_participants(participants)

        # Final Lock now saves state only. Packet PDFs are generated during Download Packet.
        next_form = STANDARD_NEXT_FORMS.get(route_name)
        if next_form:
            return redirect(f"/{next_form}/{id}")

        if is_property_paper_route(route_name):
            save_locked_property_paper(route, participant["forms"][form_key].get("data", {}))
            return redirect("/property-papers")

        return redirect(f"/packet-builder/{id}")

    final_view.__name__ = endpoint_base + "_final"
    app.add_url_rule(f"/{route_name}-final/<int:id>", endpoint_base + "_final", final_view, methods=["POST"])

    def unlock_view(id):
        participant = participants[id]
        if form_key in participant.get("forms", {}):
            participant["forms"][form_key]["locked"] = False
            participant["forms"][form_key]["completed"] = False
            save_participants(participants)
        return redirect(url_for(endpoint_base, id=id))

    unlock_view.__name__ = endpoint_base + "_unlock"
    app.add_url_rule(f"/{route_name}-unlock/<int:id>", endpoint_base + "_unlock", unlock_view)


# STANDARD ROUTE REGISTRATION FOR REMAINING FORMS
make_standard_routes("sensitive-identity-record", "sensitive_identity_record", "sensitive_identity_record")
make_standard_routes("release-of-information", "release_of_information", "release_of_information")
make_standard_routes("emergency-contact", "emergency_contact", "emergency_contact")
make_standard_routes("emergency-evacuation", "emergency_evacuation", "emergency_evacuation")
make_standard_routes("guest-addendum", "guest_addendum", "guest_addendum")
make_standard_routes("incident-report", "incident_report", "incident_report")
make_standard_routes("transfer", "transfer", "transfer")
make_standard_routes("fire-safety", "fire_safety", "fire_safety")
make_standard_routes("house-rules", "house_rules", "house_rules")
make_standard_routes("pet-animal", "pet_animal", "pet_animal")
make_standard_routes("privacy-acknowledgment", "privacy_acknowledgment", "privacy_acknowledgment")
make_standard_routes("privacy-noncommercial", "privacy_noncommercial", "privacy_noncommercial")
make_standard_routes("common-area-security", "common_area_security", "common_area_security")
make_standard_routes("property-belongings", "property_belongings", "property_belongings")
make_standard_routes("personal-belongings", "personal_belongings", "personal_belongings")
make_standard_routes("security-camera", "security_camera", "security_camera")
make_standard_routes("voluntary-participation", "voluntary_participation", "voluntary_participation")
make_standard_routes("ilh-master-lease", "ilh_master_lease", "ilh_master_lease")
make_standard_routes("ilh-mla", "ilh_mla", "ilh_mla")


# MLA TRUE-TO-SIGHT ROUTE
@app.route("/mla/<int:id>", methods=["GET","POST"])
def mla_form(id):
    if id < 0 or id >= len(participants):
        return redirect(url_for("add_participant"))

    participant = participants[id]
    state = participant.setdefault("forms", {}).setdefault("mla", {"data": {}, "locked": False, "completed": False})

    if state.get("locked"):
        return redirect(f"/mla-print/{id}")

    if request.method == "POST":
        state["data"] = request.form.to_dict()
        state["completed"] = True
        th_save_participants(participants)
        return redirect(f"/mla-print/{id}")

    return render_template("mla_transitional_form.html", id=id, participant=participant, d=state.get("data", {}), locked=state.get("locked", False))


@app.route("/mla-print/<int:id>")
def mla_print(id):
    if id < 0 or id >= len(participants):
        return redirect(url_for("add_participant"))

    participant = participants[id]
    state = participant.setdefault("forms", {}).setdefault("mla", {"data": {}, "locked": False, "completed": False})

    return render_template("mla_transitional_print.html", id=id, participant=participant, d=state.get("data", {}), locked=state.get("locked", False))


@app.route("/mla-final/<int:id>", methods=["POST"])
def mla_final(id):
    if id < 0 or id >= len(participants):
        return redirect(url_for("add_participant"))

    participant = participants[id]
    state = participant.setdefault("forms", {}).setdefault("mla", {"data": {}, "locked": False, "completed": False})
    state["locked"] = True
    state["completed"] = True
    th_save_participants(participants)


    next_form = TH_NEXT_FORMS.get("mla")
    if next_form:
        return redirect(f"/{next_form}/{id}")

    return redirect(f"/packet-builder/{id}")


@app.route("/mla-unlock/<int:id>", methods=["GET", "POST"])
def mla_unlock(id):
    if id < 0 or id >= len(participants):
        return redirect(url_for("add_participant"))

    participant = participants[id]
    state = participant.setdefault("forms", {}).setdefault("mla", {"data": {}, "locked": False, "completed": False})
    state["locked"] = False
    th_save_participants(participants)

    return redirect(f"/mla/{id}")


# ===== TH ROUTE-CLEAN ADDITIONS =====

def th_form_state(id, key, participants):
    participant = participants[id]
    return participant.setdefault("forms", {}).setdefault(key, {"data": {}, "locked": False, "completed": False})

def th_save_participants(participants):
    save_participants(participants)

def make_th_routes(route, key, form_template, print_template):
    @app.route(f"/{route}/<int:id>", methods=["GET", "POST"], endpoint=f"{route}_form")
    def th_form(id, route=route, key=key, form_template=form_template):
        participants = load_participants_file()
        if id < 0 or id >= len(participants):
            return redirect(url_for("add_participant"))
        state = th_form_state(id, key, participants)
        if state.get("locked"):
            return redirect(f"/{route}-print/{id}")
        if request.method == "POST":
            state["data"] = request.form.to_dict()

            # If this is Intake Assessment, copy participant_name into the main participant record.
            # This allows later forms to auto-populate the participant name.
            if key == "intake_assessment":
                intake_name = state["data"].get("participant_name", "").strip()
                if intake_name:
                    participants[id]["name"] = intake_name.title()

            state["completed"] = True
            th_save_participants(participants)
            return redirect(f"/{route}-print/{id}")
        return render_template(form_template, id=id, participant=participants[id], d=state.get("data", {}), locked=state.get("locked", False))

    @app.route(f"/{route}-print/<int:id>", endpoint=f"{route}_print")
    def th_print(id, route=route, key=key, print_template=print_template):
        participants = load_participants_file()
        if id < 0 or id >= len(participants):
            return redirect(url_for("add_participant"))
        state = th_form_state(id, key, participants)
        return render_template(print_template, id=id, participant=participants[id], d=state.get("data", {}), locked=state.get("locked", False))

    @app.route(f"/{route}-final/<int:id>", methods=["POST"], endpoint=f"{route}_final")
    def th_final(id, route=route, key=key):
        participants = load_participants_file()
        if id < 0 or id >= len(participants):
            return redirect(url_for("add_participant"))
        state = th_form_state(id, key, participants)
        state["locked"] = True
        state["completed"] = True
        th_save_participants(participants)

        # Final Lock now saves state only. Packet PDFs are generated during Download Packet.

        next_form = TH_NEXT_FORMS.get(route)

        if next_form:
            return redirect(f"/{next_form}/{id}")

        if is_property_paper_route(route_name):
            save_locked_property_paper(route, state.get("data", {}))
            return redirect("/property-papers")

        return redirect(f"/packet-builder/{id}")

    @app.route(f"/{route}-unlock/<int:id>", methods=["GET", "POST"], endpoint=f"{route}_unlock")
    def th_unlock(id, route=route, key=key):
        participants = load_participants_file()
        if id < 0 or id >= len(participants):
            return redirect(url_for("add_participant"))
        state = th_form_state(id, key, participants)
        state["locked"] = False
        th_save_participants(participants)
        return redirect(f"/{route}/{id}")


TH_NEXT_FORMS = {
    "intake-assessment": "mla",
    "mla": "program-compliance-addendum",
    "program-compliance-addendum": "program-participation-agreement",
    "program-participation-agreement": "house-rules",
}

make_th_routes("intake-assessment", "intake_assessment", "intake_assessment_form.html", "intake_assessment_print.html")
make_th_routes("master-lease-transitional", "master_lease_transitional", "master_lease_transitional_form.html", "master_lease_transitional_print.html")
make_th_routes("program-compliance-addendum", "program_compliance_addendum", "program_compliance_addendum_form.html", "program_compliance_addendum_print.html")
make_th_routes("program-participation-agreement", "program_participation_agreement", "program_participation_agreement_form.html", "program_participation_agreement_print.html")
make_th_routes("board-resolution", "board_resolution", "board_resolution_form.html", "board_resolution_print.html")
make_th_routes("mou-partner-agreement", "mou_partner_agreement", "mou_partner_agreement_form.html", "mou_partner_agreement_print.html")
make_th_routes("waiver-financial-justification", "waiver_financial_justification", "waiver_financial_justification_form.html", "waiver_financial_justification_print.html")
make_th_routes("va-coordination-acknowledgment", "va_coordination_acknowledgment", "va_coordination_acknowledgment_form.html", "va_coordination_acknowledgment_print.html")

# ===== END TH ROUTE-CLEAN ADDITIONS =====


@app.route("/payor-funding-record/<int:id>", methods=["GET", "POST"])
def payor_funding_record(id):
    participants = load_participants()
    activation = load_activation()

    if id < 0 or id >= len(participants):
        return "Participant not found", 404

    participant = participants[id]
    form_key = "payor_funding_record"

    participant.setdefault("forms", {})
    existing = participant["forms"].get(form_key, {})
    d = existing.get("data", {})

    if request.method == "POST":
        d = request.form.to_dict()
        _identity_participant = locals().get("participant") or locals().get("p")
        if _identity_participant:
            d = lock_participant_identity_fields(d, _identity_participant)
        participant["forms"][form_key] = {
            "data": d,
            "completed": True,
            "locked": False
        }
        save_participants(participants)
        return redirect(f"/payor-funding-record-print/{id}")

    return render_template(
        "payor_funding_record_form.html",
        id=id,
        participant=participant,
        activation=activation,
        d=d
    )


@app.route("/payor-funding-record-print/<int:id>")
def payor_funding_record_print(id):
    participants = load_participants()
    activation = load_activation()

    if id < 0 or id >= len(participants):
        return "Participant not found", 404

    participant = participants[id]
    form_key = "payor_funding_record"

    participant.setdefault("forms", {})
    existing = participant["forms"].get(form_key, {})
    d = existing.get("data", {})

    return render_template(
        "payor_funding_record_print.html",
        id=id,
        participant=participant,
        activation=activation,
        d=d
    )


@app.route("/payor-funding-record-final/<int:id>", methods=["POST"])
def payor_funding_record_final(id):
    participants = load_participants()

    if id < 0 or id >= len(participants):
        return "Participant not found", 404

    participant = participants[id]
    form_key = "payor_funding_record"

    participant.setdefault("forms", {})
    participant["forms"].setdefault(form_key, {})
    participant["forms"][form_key]["completed"] = True
    participant["forms"][form_key]["locked"] = True

    save_participants(participants)
    return redirect(f"/billing-invoice-setup/{id}")


@app.route("/billing-invoice-setup/<int:id>", methods=["GET", "POST"])
def billing_invoice_setup(id):
    participants = load_participants()
    activation = load_activation()

    if id < 0 or id >= len(participants):
        return "Participant not found", 404

    participant = participants[id]
    form_key = "billing_invoice_setup"

    participant.setdefault("forms", {})
    existing = participant["forms"].get(form_key, {})
    data = existing.get("data", {})

    if request.method == "POST":
        data = request.form.to_dict()
        _identity_participant = locals().get("participant") or locals().get("p")
        if _identity_participant:
            data = lock_participant_identity_fields(data, _identity_participant)
        participant["forms"][form_key] = {
            "data": data,
            "completed": True,
            "locked": False
        }
        save_participants(participants)
        return redirect(f"/billing-invoice-setup-print/{id}")

    return render_template(
        "billing_invoice_setup_form.html",
        id=id,
        participant=participant,
        activation=activation,
        data=data
    )


@app.route("/billing-invoice-setup-print/<int:id>")
def billing_invoice_setup_print(id):
    participants = load_participants()
    activation = load_activation()

    if id < 0 or id >= len(participants):
        return "Participant not found", 404

    participant = participants[id]
    form_key = "billing_invoice_setup"

    participant.setdefault("forms", {})
    existing = participant["forms"].get(form_key, {})
    data = existing.get("data", {})

    return render_template(
        "billing_invoice_setup_print.html",
        id=id,
        participant=participant,
        activation=activation,
        data=data
    )


@app.route("/billing-invoice-setup-final/<int:id>", methods=["POST"])
def billing_invoice_setup_final(id):
    participants = load_participants()

    if id < 0 or id >= len(participants):
        return "Participant not found", 404

    participant = participants[id]
    form_key = "billing_invoice_setup"

    participant.setdefault("forms", {})
    participant["forms"].setdefault(form_key, {})
    participant["forms"][form_key]["completed"] = True
    participant["forms"][form_key]["locked"] = True

    save_participants(participants)
    return redirect("/admin-forms")


@app.route("/referral-source-record/<int:id>", methods=["GET", "POST"])
def referral_source_record(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    p.setdefault("forms", {})
    p["forms"].setdefault("referral_source_record", {})

    if request.method == "POST":
        p["forms"]["referral_source_record"]["data"] = request.form.to_dict()
        p["forms"]["referral_source_record"]["completed"] = True
        p["forms"]["referral_source_record"]["locked"] = False
        save_participants_file()
        return redirect(f"/referral-source-record-print/{id}")

    data = p["forms"]["referral_source_record"].get("data", {})
    return render_template("referral_source_record_form.html", id=id, participant=p, data=data)


@app.route("/referral-source-record-print/<int:id>")
def referral_source_record_print(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    form = p.get("forms", {}).get("referral_source_record", {})
    data = form.get("data", {})
    locked = form.get("locked", False)

    return render_template("referral_source_record_print.html", id=id, participant=p, data=data, locked=locked)


@app.route("/referral-source-record-final/<int:id>", methods=["POST"])
def referral_source_record_final(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    p.setdefault("forms", {})
    p["forms"].setdefault("referral_source_record", {})
    p["forms"]["referral_source_record"]["locked"] = True
    p["forms"]["referral_source_record"]["completed"] = True
    save_participants_file()

    if "agency_sponsorship_record" in app.view_functions:
        return redirect(f"/agency-sponsorship-record/{id}")

    return redirect("/admin-forms")


@app.route("/agency-sponsorship-record/<int:id>", methods=["GET", "POST"])
def agency_sponsorship_record(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    p.setdefault("forms", {})
    p["forms"].setdefault("agency_sponsorship_record", {})

    if request.method == "POST":
        p["forms"]["agency_sponsorship_record"]["data"] = request.form.to_dict()
        p["forms"]["agency_sponsorship_record"]["completed"] = True
        p["forms"]["agency_sponsorship_record"]["locked"] = False
        save_participants_file()
        return redirect(f"/agency-sponsorship-record-print/{id}")

    data = p["forms"]["agency_sponsorship_record"].get("data", {})
    return render_template("agency_sponsorship_record_form.html", id=id, participant=p, data=data, program_type="")


@app.route("/agency-sponsorship-record-print/<int:id>")
def agency_sponsorship_record_print(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    form = p.get("forms", {}).get("agency_sponsorship_record", {})
    data = form.get("data", {})
    locked = form.get("locked", False)

    return render_template("agency_sponsorship_record_print.html", id=id, participant=p, data=data, locked=locked)


@app.route("/agency-sponsorship-record-final/<int:id>", methods=["POST"])
def agency_sponsorship_record_final(id):
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    p.setdefault("forms", {})
    p["forms"].setdefault("agency_sponsorship_record", {})
    p["forms"]["agency_sponsorship_record"]["locked"] = True
    p["forms"]["agency_sponsorship_record"]["completed"] = True
    save_participants_file()

    return redirect(f"/payor-funding-record/{id}")




def load_rolodex():
    return storage.get_rolodex()

def save_rolodex(contacts):
    return storage.save_rolodex(contacts)


@app.route("/rolodex", methods=["GET","POST"])
def rolodex():
    contacts = load_rolodex()
    if request.method == "POST":
        contact = {
            "organization_name": request.form.get("organization_name","").strip(),
            "contact_person": request.form.get("contact_person","").strip(),
            "role_title": request.form.get("role_title","").strip(),
            "phone": request.form.get("phone","").strip(),
            "email": request.form.get("email","").strip(),
            "organization_type": request.form.get("organization_type","").strip(),
            "notes": request.form.get("notes","").strip()
        }
        contacts.append(contact)
        save_rolodex(contacts)
        return redirect(url_for("rolodex"))
    return render_template("rolodex.html", contacts=contacts)


@app.route("/mr-ir/scan")
def mr_ir_scan():
    scan_targets = [
        {"name": "Mr.IR Dashboard", "path": "/mr-ir", "purpose": "Internal repair dashboard"},
        {"name": "Operations", "path": "/operations", "purpose": "Main operations/control area"},
        {"name": "Rolodex", "path": "/rolodex", "purpose": "Organization contact list"},
        {"name": "PDF Diagnostic", "path": "/render-pdf-diagnostic", "purpose": "Render/Docker PDF generation check"},
        {"name": "Storage Check", "path": "/mr-ir/storage-check", "purpose": "Storage coordinator and JSON fallback diagnostic"},
        {"name": "Packet Builder PID 0", "path": "/packet-builder/0", "purpose": "Participant packet builder test"},
    ]

    results = []

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["owner_operator_email"] = "mr.ir@internal.local"

        for item in scan_targets:
            try:
                response = client.get(item["path"])
                status_code = response.status_code

                if status_code in (200, 302):
                    result = "PASS"
                    possible_issue = "No immediate issue detected."
                    suggested_fix = "No action needed."
                elif status_code == 404:
                    result = "WARNING"
                    possible_issue = "The route may be missing or the URL may have changed."
                    suggested_fix = "Check app.py for the route and confirm the link path is correct."
                elif status_code >= 500:
                    result = "FAIL"
                    possible_issue = "The route exists but crashed while loading."
                    suggested_fix = "Check the terminal/logs for the error, then inspect the route function and template."
                else:
                    result = "CHECK"
                    possible_issue = "The route returned an unusual status code."
                    suggested_fix = "Review whether this response is expected."

                results.append({
                    "name": item["name"],
                    "path": item["path"],
                    "purpose": item["purpose"],
                    "status_code": status_code,
                    "result": result,
                    "possible_issue": possible_issue,
                    "suggested_fix": suggested_fix,
                })

            except Exception as e:
                results.append({
                    "name": item["name"],
                    "path": item["path"],
                    "purpose": item["purpose"],
                    "status_code": "CRASH",
                    "result": "FAIL",
                    "possible_issue": f"Python error: {type(e).__name__}",
                    "suggested_fix": str(e)[:250],
                })

    return render_template("mr_ir_scan.html", results=results)


@app.route("/mr-ir/storage-check")
def mr_ir_storage_check():
    from pathlib import Path

    data_dir = Path("data")
    checks = []

    storage_type = type(storage).__name__
    active_engine = storage.active_engine() if hasattr(storage, "active_engine") else "unknown"

    file_checks = [
        ("activation", data_dir / "activation.json", storage.get_activation),
        ("participants", data_dir / "participants.json", storage.get_participants),
        ("license_requests", data_dir / "license_requests.json", storage.get_license_requests),
        ("audit_logs", data_dir / "audit_log.json", storage.get_audit_logs),
        ("property_papers", data_dir / "property_papers.json", storage.get_property_papers),
        ("employee_certs", data_dir / "employee_certifications.json", storage.get_employee_certs),
        ("paypal_webhook_events", data_dir / "paypal_webhook_events.json", storage.get_paypal_webhook_events),
        ("rolodex", data_dir / "rolodex.json", storage.get_rolodex),
    ]

    for name, path, reader in file_checks:
        try:
            data = reader()
            if isinstance(data, list):
                count = len(data)
                shape = "list"
            elif isinstance(data, dict):
                count = len(data.keys())
                shape = "dict"
            else:
                count = "n/a"
                shape = type(data).__name__

            checks.append({
                "name": name,
                "file": str(path),
                "exists": path.exists(),
                "shape": shape,
                "count": count,
                "status": "PASS",
            })
        except Exception as e:
            checks.append({
                "name": name,
                "file": str(path),
                "exists": path.exists(),
                "shape": "error",
                "count": "error",
                "status": f"FAIL: {type(e).__name__}: {str(e)[:120]}",
            })

    lines = [
        "MR. IR STORAGE CHECK",
        "====================",
        f"Storage object: {storage_type}",
        f"Active engine: {active_engine}",
        "",
        "Core storage files:",
    ]

    for item in checks:
        lines.append(
            f"- {item['name']}: {item['status']} | exists={item['exists']} | "
            f"shape={item['shape']} | count={item['count']} | file={item['file']}"
        )

    return "\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/mr-ir")
def mr_ir_dashboard():
    import os

    env_status = {
        "PAYPAL_SECRET": "✅ Loaded" if os.environ.get("PAYPAL_SECRET") else "❌ MISSING",
        "WEBHOOK_VERIFY_KEY": "✅ Loaded" if os.environ.get("WEBHOOK_VERIFY_KEY") else "❌ MISSING",
        "DATABASE_URL": "✅ Loaded" if os.environ.get("DATABASE_URL") else "⚠️ Not Found / Local File Mode",
        "PAYPAL_CLIENT_ID": "✅ Loaded" if os.environ.get("PAYPAL_CLIENT_ID") else "❌ MISSING",
        "PAYPAL_PLAN_ID": "✅ Loaded" if os.environ.get("PAYPAL_PLAN_ID") else "❌ MISSING",
    }

    return render_template("mr_ir_dashboard.html", env_status=env_status)


@app.route("/owner-license-approval", methods=["GET"])
def owner_license_approval():
    owner_key = request.args.get("key", "").strip()
    expected_key = os.environ.get("OWNER_APPROVAL_KEY", "owner-test-key")

    if owner_key and owner_key == expected_key:
        session["owner_approval_logged_in"] = True

    approved_owner = bool(session.get("owner_approval_logged_in"))

    requests_data = []
    if approved_owner:
        requests_data = load_license_requests()

    return render_template(
        "owner_license_approval.html",
        approved_owner=approved_owner,
        owner_key=owner_key,
        requests_data=requests_data
    )


@app.route("/owner-license-approval/activate", methods=["POST"])
def owner_license_approval_activate():
    owner_key = request.form.get("key", "").strip()
    expected_key = os.environ.get("OWNER_APPROVAL_KEY", "owner-test-key")

    if owner_key and owner_key == expected_key:
        session["owner_approval_logged_in"] = True

    if not session.get("owner_approval_logged_in"):
        flash("Owner approval key required.")
        return redirect(url_for("owner_license_approval"))

    license_number = request.form.get("license_number", "").strip()
    requests_data = load_license_requests()
    matched_request = None

    for item in requests_data:
        if item.get("license_number") == license_number:
            item["status"] = "payment_received_active"
            item["manual_payment_verified_at"] = datetime.now().isoformat(timespec="seconds")
            item["manual_payment_verified_by"] = "owner_license_approval"
            matched_request = item
            break

    if matched_request:
        save_license_requests(requests_data)

        activated_system = load_activation()
        activated_system["property_name"] = matched_request.get("business_name", activated_system.get("property_name", ""))
        activated_system["business_name"] = matched_request.get("business_name", "")
        activated_system["license_number"] = matched_request.get("license_number", "")
        activated_system["email"] = matched_request.get("paypal_email", "")
        activated_system["license_status"] = "payment_received_active"
        activated_system["payment_verified_at"] = datetime.now().isoformat(timespec="seconds")
        activated_system["licensed_site_address"] = matched_request.get("site_address", "")
        activated_system["licensed_site_city"] = matched_request.get("city", "")
        activated_system["licensed_site_state"] = matched_request.get("state", "")
        activated_system["licensed_site_zip"] = matched_request.get("zip_code", "")

        if not activated_system.get("program_type"):
            activated_system["program_type"] = "Transitional"

        if "password" not in activated_system:
            activated_system["password"] = ""

        save_activation(activated_system)
        flash("License activated after manual payment verification.")
    else:
        flash("License number not found.")

    return redirect(url_for("owner_license_approval"))



@app.route("/quick-service-update", methods=["GET", "POST"])
def quick_service_update():
    participants = load_participants()
    message = ""

    if request.method == "POST":
        d = request.form.to_dict(flat=False)
        _identity_participant = locals().get("participant") or locals().get("p")
        if _identity_participant:
            d = lock_participant_identity_fields(d, _identity_participant)
        participant_pid = request.form.get("participant_pid", "").strip()

        try:
            pid = int(participant_pid.split()[0].replace("PID", "").replace("-", "").strip())
        except Exception:
            pid = None

        if pid is None or pid < 0 or pid >= len(participants):
            message = "Please enter a valid PID number before saving."
        else:
            entry = {
                "participant_pid": participant_pid,
                "update_date": request.form.get("update_date", ""),
                "update_type": request.form.get("update_type", ""),
                "tags": d.get("tags", []),
                "service_note": request.form.get("service_note", ""),
                "follow_up": request.form.get("follow_up", ""),
                "staff_name": request.form.get("staff_name", "")
            }

            participant = participants[pid]
            forms = participant.setdefault("forms", {})
            history = forms.setdefault("quick_service_updates", [])
            history.append(entry)
            save_participants(participants)
            message = "Quick service update saved to participant history."

    return render_template("quick_service_update.html", message=message, participants=participants)



@app.route('/exit-discharge-summary/<int:id>', methods=['GET', 'POST'])
def exit_discharge_summary(id):
    participants = load_participants()
    participant = participants[id]
    forms = participant.setdefault("forms", {})
    record = forms.setdefault("exit_discharge_summary", {})
    d = record.get("data", {})

    if request.method == "POST":
        d = request.form.to_dict()
        _identity_participant = locals().get("participant") or locals().get("p")
        if _identity_participant:
            d = lock_participant_identity_fields(d, _identity_participant)
        record["data"] = d
        record["completed"] = True
        record["locked"] = False
        save_participants(participants)
        return redirect(url_for("service_coordination"))

    return render_template(
        "exit_discharge_summary_form.html",
        participant=participant,
        id=id,
        d=d
    )


@app.route("/version-check")
def version_check():
    return """
    <html>
    <head><title>NILPF Version Check</title></head>
    <body style="font-family:Arial; padding:40px;">
        <h1>NILPF Housing OS Version Check</h1>
        <p><strong>Commit Marker:</strong> b0771d6</p>
        <p><strong>Deploy Test:</strong> Render must show this page if latest code is live.</p>
        <p><strong>Time Marker:</strong> 2026-06-11 Render tragedy check</p>
    </body>
    </html>
    """


if __name__ == "__main__":
    app.run(debug=True)
