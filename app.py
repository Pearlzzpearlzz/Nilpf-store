from reportlab.pdfgen import canvas
import fitz
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, session
import json
import os
from datetime import date, datetime

app = Flask(__name__)

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


app.secret_key = "nilpf_secret_key"

@app.before_request
def require_login():
    public_routes = ["login", "activate", "logout"]
    if request.endpoint in public_routes or request.path == "/favicon.ico" or (request.path and request.path.startswith("/static/")):
        return

    if not session.get("logged_in"):
        return redirect(url_for("login"))

    view_args = request.view_args or {}
    if "id" in view_args:
        pid = view_args.get("id")
        if not isinstance(pid, int) or pid < 0 or pid >= len(participants):
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


def load_activation():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_activation(data):
    os.makedirs("data", exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

PARTICIPANTS_FILE = "data/participants.json"

def load_participants():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(PARTICIPANTS_FILE):
        with open(PARTICIPANTS_FILE, "r") as f:
            return json.load(f)
    return []

def save_participants(data):
    os.makedirs("data", exist_ok=True)
    with open(PARTICIPANTS_FILE, "w") as f:
        json.dump(data, f)


LICENSE_REQUESTS_FILE = "data/license_requests.json"

def load_license_requests():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(LICENSE_REQUESTS_FILE):
        with open(LICENSE_REQUESTS_FILE, "r") as f:
            return json.load(f)
    return []

def save_license_requests(data):
    os.makedirs("data", exist_ok=True)
    with open(LICENSE_REQUESTS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def generate_license_number(existing_requests):
    year = datetime.now().year
    next_number = len(existing_requests) + 1
    return f"NILPF-{year}-{next_number:04d}"


AUDIT_LOG_FILE = "data/audit_log.json"

def write_audit(action, participant_id=None, form_key="", details=""):
    os.makedirs("data", exist_ok=True)

    try:
        with open(AUDIT_LOG_FILE, "r") as f:
            logs = json.load(f)
            if not isinstance(logs, list):
                logs = []
    except Exception:
        logs = []

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

    with open(AUDIT_LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)

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

        submitted_request = {
            "license_number": license_number,
            "status": "pending_payment_verification",
            "full_name": full_name,
            "business_name": business_name,
            "paypal_email": paypal_email,
            "site_address": site_address,
            "city": city,
            "state": state,
            "zip_code": zip_code,
            "phone": phone,
            "notes": notes,
            "created_at": datetime.now().isoformat(timespec="seconds")
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
<title>Login</title>
<style>
body {{background:#000;color:#fff;font-family:Arial;text-align:center;padding:28px;}}
.loginbox {{max-width:520px;margin:auto;border:2px solid #d4af37;border-radius:22px;padding:22px;background:#070707;box-shadow:0 0 25px rgba(212,175,55,.3);}}
.hero {{width:100%;border-radius:16px;border:1px solid #6f5a16;margin-bottom:14px;}}
input {{width:100%;max-width:390px;padding:12px;margin:8px;border-radius:8px;border:1px solid #d4af37;background:#111;color:#fff;}}
button {{padding:12px 20px;background:#d4af37;border:none;border-radius:10px;font-weight:bold;}}
.eye {{cursor:pointer;margin-left:-30px;}}
a {{color:#d4af37;font-size:14px;}}
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
    if os.path.exists(PARTICIPANTS_FILE):
        with open(PARTICIPANTS_FILE, "r") as f:
            return json.load(f)
    return []

def save_participants_file():
    os.makedirs("data", exist_ok=True)
    with open(PARTICIPANTS_FILE, "w") as f:
        json.dump(participants, f, indent=2)

participants = load_participants_file()

@app.route("/add_participant", methods=["GET", "POST"])
def add_participant():

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        activated_system = load_activation()
        program_type = activated_system.get("program_type", "ILH")
        if name:
            participants.append({
                "form_data": {},
                "forms": {},
                "name": name,
                "program_type": program_type,
                "screening": {}
            })
            new_id = len(participants) - 1
            print("NEW PARTICIPANT:", name)
            print("ALL PARTICIPANTS:", participants)
            if program_type == "ILH":
                return redirect(url_for("entry_screening", id=new_id))
            if program_type == "PSH":
                  return "PSH module is parked and not active yet. Select ILH, Transitional, VA, DOC, or Reentry on the Dashboard."
            return redirect(f"/intake-assessment/{new_id}")
        return redirect(url_for("add_participant"))

    return redirect("/")

@app.route("/property-papers")
def property_papers():
    return render_template("property_papers.html")


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


@app.route("/packet-builder/<int:id>")
def packet_builder(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))
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
        for shared_doc in SHARED_HOUSING_FORMS:
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
            items += f"<li><a href='{link}'>{doc['title']} ({doc['file']})</a> - {completed}</li>"

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
                <div><strong>Screening Status:</strong> {participant.get("screening_status","pending")}</div>
                <div><strong>Program Type:</strong> {activated_system.get("program_type","ILH")}</div>
            </div>

            <div class="forms-box">
                <h2>Forms in Packet</h2>
                <ol>
                    {items}
                </ol>
            </div>

            <div class="bottom-buttons">
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




@app.route("/screening")
def screening():
    activated_system = load_activation()
    program_type = activated_system.get("program_type", "ILH")

    if program_type == "PSH":
        return "PSH module is parked and not active yet. Select ILH, Transitional, VA, DOC, or Reentry on the Dashboard."

    participants.append({
        "form_data": {},
        "forms": {},
        "name": "",
        "program_type": program_type,
        "screening": {}
    })
    save_participants_file()
    id = len(participants) - 1

    if program_type == "ILH":
        return redirect(url_for("entry_screening", id=id))

    return redirect(f"/intake-assessment/{id}")


@app.route("/entry-screening/<int:id>", methods=["GET", "POST"])
def entry_screening(id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if id < 0 or id >= len(participants):
        return redirect(url_for("add_participant"))

    participant = participants[id]


    if request.method == "POST":
        data = request.form.to_dict(flat=True)
        participant["entry_screening"] = data
        participant["entry_screening_pdf"] = "18_entry_screening.pdf"

        # FULL SCREENING (no 5-question limit)
        answers = {k: v for k, v in data.items() if k.lower().startswith("q")}
        passed = all(str(v).strip().lower() == "yes" for v in answers.values()) if answers else False

        participant["screening_status"] = "passed" if passed else "failed"
        save_participants_file()

        print("ENTRY SCREENING SAVED:", participant["name"])
        print("SCREENING STATUS:", participant["screening_status"])

        if passed:
            return redirect(url_for("packet_builder", id=id))

        return redirect(url_for("add_participant"))

    return render_template("entry_screening.html", participant=participant, participant_id=id)


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

    try:
        pdf_file = generate_true_to_sight_pdf(id, "/vehicle-parking-print/{id}", "vehicle_parking_true_to_sight.pdf")
        print(f"TRUE-TO-SIGHT PDF CREATED: {pdf_file}")
    except Exception as e:
        print(f"TRUE-TO-SIGHT PDF ERROR for vehicle_parking: {e}")

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

    try:
        pdf_file = generate_true_to_sight_pdf(id, "/bill-of-dignity-print/{id}", "bill_of_dignity_true_to_sight.pdf")
        print(f"TRUE-TO-SIGHT PDF CREATED: {pdf_file}")
    except Exception as e:
        print(f"TRUE-TO-SIGHT PDF ERROR for bill_of_dignity: {e}")

    return redirect(f"/packet-builder/{id}")





# =========================
# BAD UNIVERSAL FORM SYSTEM REMOVED — true-to-sight routes preserved


@app.route("/render-pdf-diagnostic")
def render_pdf_diagnostic():
    import os
    from pathlib import Path

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return f"PLAYWRIGHT IMPORT FAILED: {e}", 500

    port = os.environ.get("PORT", "5000")
    test_urls = [
        f"http://127.0.0.1:{port}/",
        request.url_root.rstrip("/") + "/"
    ]

    results = []
    out_dir = Path("static/filled")
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            page = browser.new_page()

            results.append("CHROMIUM LAUNCHED: YES")

            for url in test_urls:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    title = page.title()
                    test_pdf = out_dir / "RENDER_DIAGNOSTIC_TEST.pdf"
                    page.pdf(
                        path=str(test_pdf),
                        format="Letter",
                        print_background=True
                    )
                    results.append(f"URL OK: {url}")
                    results.append(f"PAGE TITLE: {title}")
                    results.append(f"PDF EXISTS: {test_pdf.exists()} | SIZE: {test_pdf.stat().st_size if test_pdf.exists() else 0}")
                except Exception as e:
                    results.append(f"URL FAILED: {url}")
                    results.append(f"ERROR: {e}")

            browser.close()

    except Exception as e:
        results.append(f"CHROMIUM / PDF TEST FAILED: {e}")

    return "<pre>" + "\n".join(results) + "</pre>"


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
        participant = participants[id]
        participant.setdefault("forms", {})
        participant["forms"].setdefault(form_key, {"data": {}})
        participant["forms"][form_key]["locked"] = True
        participant["forms"][form_key]["completed"] = True
        save_participants(participants)

        # Auto-generate true-to-sight PDF from this form print page.
        # Example: /fire-safety-print/4 -> static/filled/participant_4/fire_safety_true_to_sight.pdf
        try:
            pdf_file = generate_true_to_sight_pdf(
                id,
                f"/{route_name}-print/{{id}}",
                f"{form_key}_true_to_sight.pdf"
            )
            print(f"TRUE-TO-SIGHT PDF CREATED: {pdf_file}")
            if form_key == "sensitive_identity_record":
                import shutil
                vault_dir = os.path.join("static", "filled", f"participant_{id}", "sensitivity_vault")
                os.makedirs(vault_dir, exist_ok=True)
                vault_path = os.path.join(vault_dir, "sensitive_identity_record_true_to_sight.pdf")
                shutil.copy2(pdf_file, vault_path)
                print(f"SENSITIVITY VAULT COPY CREATED: {vault_path}")
        except Exception as e:
            print(f"TRUE-TO-SIGHT PDF ERROR for {form_key}: {e}")

        next_form = STANDARD_NEXT_FORMS.get(route_name)
        if next_form:
            return redirect(f"/{next_form}/{id}")
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

    try:
        pdf_file = generate_true_to_sight_pdf(id, "/mla-print/{id}", "mla_true_to_sight.pdf")
        print(f"TRUE-TO-SIGHT PDF CREATED: {pdf_file}")
    except Exception as e:
        print(f"TRUE-TO-SIGHT PDF ERROR for mla: {e}")

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

def th_form_state(id, key):
    participant = participants[id]
    return participant.setdefault("forms", {}).setdefault(key, {"data": {}, "locked": False, "completed": False})

def th_save_participants(participants):
    save_participants(participants)

def make_th_routes(route, key, form_template, print_template):
    @app.route(f"/{route}/<int:id>", methods=["GET", "POST"], endpoint=f"{route}_form")
    def th_form(id, route=route, key=key, form_template=form_template):
        if id < 0 or id >= len(participants):
            return redirect(url_for("add_participant"))
        state = th_form_state(id, key)
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
        if id < 0 or id >= len(participants):
            return redirect(url_for("add_participant"))
        state = th_form_state(id, key)
        return render_template(print_template, id=id, participant=participants[id], d=state.get("data", {}), locked=state.get("locked", False))

    @app.route(f"/{route}-final/<int:id>", methods=["POST"], endpoint=f"{route}_final")
    def th_final(id, route=route, key=key):
        if id < 0 or id >= len(participants):
            return redirect(url_for("add_participant"))
        state = th_form_state(id, key)
        state["locked"] = True
        state["completed"] = True
        th_save_participants(participants)

        try:
            pdf_file = generate_true_to_sight_pdf(
                id,
                f"/{route}-print/{{id}}",
                f"{key}_true_to_sight.pdf"
            )
            print(f"TRUE-TO-SIGHT PDF CREATED: {pdf_file}")
        except Exception as e:
            print(f"TRUE-TO-SIGHT PDF ERROR for {key}: {e}")

        next_form = TH_NEXT_FORMS.get(route)

        if next_form:
            return redirect(f"/{next_form}/{id}")

        return redirect(f"/packet-builder/{id}")

    @app.route(f"/{route}-unlock/<int:id>", methods=["GET", "POST"], endpoint=f"{route}_unlock")
    def th_unlock(id, route=route, key=key):
        if id < 0 or id >= len(participants):
            return redirect(url_for("add_participant"))
        state = th_form_state(id, key)
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
    path = Path("data/rolodex.json")
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []

def save_rolodex(contacts):
    Path("data").mkdir(exist_ok=True)
    Path("data/rolodex.json").write_text(json.dumps(contacts, indent=2))


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


if __name__ == "__main__":
    app.run(debug=True)
