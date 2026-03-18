

framework_groups = {
    "Program Foundations": [
        ("Member Bill of Rights", "00_Member_Bill_of_Rights_v2.1.pdf"),
        ("NILPF Charter", "01_NILPF_Charter_of_Human_Dignity_and_Independent_Living_v2.1.pdf"),
    ],
    "Getting Started": [
        ("Pre-Opening Waitlist Form", "14-Pre_Opening_Waitlist_Form.pdf"),
        ("Entry Screening", "18_Entry_Screening_v2.2.pdf"),
        ("Independent Living Disclosure", "1_Independent_Living_Disclosure_v2.1.pdf"),
        ("No Services / No Supervision Acknowledgement", "2_No_Services_No_Supervision_Acknowledgement.pdf"),
        ("Voluntary Participation Acknowledgement", "3_Voluntary_Participation_Acknowledgement.pdf"),
    ],
    "Program Standards": [
        ("House Rules and Community Standards", "4_House_Rules_and_Community_Standards.pdf"),
        ("Fire Safety and Self-Preservation", "5_Fire_Safety_and_Self-Preservation_Acknowledgement.pdf"),
        ("Emergency Evacuation Plan", "6_Emergency_Evacuation_Plan.pdf"),
        ("Emergency Contact Form", "7_Emergency_Contact_Form.pdf"),
        ("Incident Report Form", "8_Incident_Report_Form.pdf"),
        ("Guest Addendum", "9_Guest_Addendum.pdf"),
    ],
    "Operational Forms": [
        ("Pet / Animal Information", "10-Pet_Animal_Information_Sheet.pdf"),
        ("Vehicle / Parking Information", "11-Vehicle_Parking_Information_Form.pdf"),
        ("Transfer Form", "12-Transfer_Form.pdf"),
        ("Communication and Consent", "13-Communication_and_Consent_Form.pdf"),
    ],
    "Compliance and Financial": [
        ("Complaint / Grievance Procedure", "15_COMPLAINT_GRIEVANCE_PROCEDURE_FORM.pdf"),
        ("Important Notice and Disclaimer", "15_IMPORTANT_NOTICE_AND_DISCLAIMER_v2.1.pdf"),
        ("Participant Financial Responsibility", "16_Participant_Financial_Responsibility_Agreement.pdf"),
        ("No Payee Financial Control Disclosure", "17_NO_PAYEE_FINANCIAL_CONTROL_DISCLOSURE.pdf"),
        ("Common Area Security Disclosure", "19-Common_Area_Security_Disclosure.pdf"),
        ("Privacy and Non-Commercialization Acknowledgement", "20_Participant_Privacy_AND_Non_Commercialization_Acknowledgement.pdf"),
        ("Property and Personal Belongings Acknowledgement", "21_Property_AND_Personal_Belongings_Acknowledgement.pdf"),
    ],
    "Legal Authority": [
        ("Owner Acknowledgment and Program Boundary Handbook", "064_Owner_Acknowledgment_and_Program_Boundary_Handbook_v2.1.pdf"),
        ("Master License Agreement", "MASTER_LICENSE_AGREEMENT_MLA_v2.1.pdf"),
        ("Master Lease", "Master_Lease_v2.1.pdf"),
        ("Essential Forms Guide", "README_ESSENTIAL_FORMS_v2.1.pdf"),
    ],
}


from itsdangerous import URLSafeTimedSerializer


ALLOWED_EXTENSIONS = {'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

from flask import Flask, jsonify, redirect, request, send_file, abort, session, render_template_string, url_for

import os
import sqlite3
import json
import re
from pathlib import Path
from datetime import datetime
import requests
import io
import zipfile
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv(override=True)

from io import BytesIO

app = Flask(__name__)

@app.after_request
def strip_bad_unicode(response):
    try:
        if response.mimetype == "text/html":
            body = response.get_data(as_text=True)
            body = body.encode("utf-8", "ignore").decode("utf-8", "ignore")
            response.set_data(body)
    except Exception:
        pass
    return response


from datetime import timedelta
import os

app.secret_key = os.getenv('SECRET_KEY','dev-secret-change-me')
app.permanent_session_lifetime = timedelta(minutes=10)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True


def participant_has_notes(pid):
    conn=sqlite3.connect(DB_PATH)
    cur=conn.cursor()
    cur.execute("SELECT COUNT(*) FROM participant_notes WHERE participant_id=?", (str(pid),))
    n=cur.fetchone()[0]
    conn.close()
    return n>0

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret")
download_serializer = URLSafeTimedSerializer(app.secret_key)







from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch

def _safe_redirect(e=None):
    path = (request.path or "").lower()

    # PayPal checkout flow
    if path.startswith("/buy") or path.startswith("/success") or path.startswith("/cancel"):
        if session.get("licensed_location"):
            return redirect("/buy")
        return redirect("/address")

    # Product page should stay product page
    if path.startswith("/product"):
        return redirect("/product")

    # Documents or upgrade should not redirect away
    if path.startswith("/documents") or path.startswith("/upgrade"):
        return e if e is not None else ("Documents/upgrade request failed. Check session_id or purchase record.", 400)

    # Default: return the actual error instead of bouncing to /address
    return e if e is not None else ("Bad Request", 400)

@app.errorhandler(400)
def handle_400(e):
    return _safe_redirect(e)

@app.errorhandler(404)
def handle_404(e):
    return _safe_redirect(e)

@app.errorhandler(500)
def handle_500(e):
    return _safe_redirect(e)

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

# -------------------------
# Incident / Status Documentation Table
# -------------------------


# -------------------------
# Participant Form Tracking
# -------------------------

def ensure_participant_forms_table():
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS participant_forms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_id TEXT NOT NULL,
            form_name TEXT NOT NULL,
            is_complete INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT
        )
    """)
    cols = [r[1] for r in cur.execute("PRAGMA table_info(participant_forms)").fetchall()]
    if "is_complete" not in cols:
        cur.execute("ALTER TABLE participant_forms ADD COLUMN is_complete INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()


def seed_participant_forms(participant_id: str):
    import sqlite3
    from datetime import datetime

    forms = [
        "Entry Screening",
        "Participant Financial Responsibility Agreement",
        "Important Notice and Disclaimer",
        "Communication and Consent Form",
        "Transfer Form",
        "Pet / Animal Information Sheet",
        "Vehicle / Parking Information Form",
        "Complaint / Grievance Procedure Form"
    ]

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cols = [r[1] for r in cur.execute("PRAGMA table_info(participant_forms)").fetchall()]

    for form_name in forms:
        existing = cur.execute(
            "SELECT COUNT(*) FROM participant_forms WHERE participant_id = ? AND form_name = ?",
            (str(participant_id), form_name)
        ).fetchone()[0]

        if existing:
            continue

        data = {
            "participant_id": str(participant_id),
            "form_name": form_name
        }

        if "is_complete" in cols:
            data["is_complete"] = 0
        if "created_at" in cols:
            data["created_at"] = datetime.utcnow().isoformat()

        fields = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        cur.execute(
            f"INSERT INTO participant_forms ({fields}) VALUES ({placeholders})",
            tuple(data.values())
        )

    conn.commit()
    conn.close()


def get_participant_forms(pid: str):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT form_name, is_complete, completed_at
        FROM participant_forms
        WHERE participant_id=?
        ORDER BY id
    """, (str(pid),))
    rows = cur.fetchall()
    conn.close()
    return rows


def mark_participant_form_complete(pid: str, form_name: str):
    import sqlite3
    from datetime import datetime
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        UPDATE participant_forms
        SET is_complete=1, completed_at=?
        WHERE participant_id=? AND form_name=?
    """, (datetime.utcnow().isoformat(), str(pid), form_name))
    conn.commit()
    conn.close()


def ensure_participants_table():
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            dob TEXT,
            move_in_date TEXT,
            room_unit TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def ensure_notes_table():

    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS participant_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_name TEXT NOT NULL,
            staff_name TEXT,
            note_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

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
    if "price_paid" not in cols:
        cur.execute("ALTER TABLE licenses ADD COLUMN price_paid TEXT")
    conn.commit()
    conn.close()

# Product catalog (edit prices + file paths as you wish)

PRODUCTS = {
    "FIRST_PROPERTY": {
        "label": "NILPF First Property Access",
        "price": "1.00",
        "file": "",
        "kind": "one_time",
    },
    "ADDITIONAL_PROPERTY": {
        "label": "NILPF Additional Property Access",
        "price": "1.00",
        "file": "",
        "kind": "one_time",
    },
    "PROPERTY_MONTHLY": {
        "label": "NILPF Monthly Property Subscription",
        "price": "1.00",
        "file": "",
        "kind": "subscription",
        "plan_id": "P-93D65823U4853871NNGWD7NQ",
    }
}

# -------------------------
# Field Map Schema Loader
# -------------------------
FIELD_MAP_PATH = Path("field_map.json")

def load_field_map():
    """
    Loads the NILPF field schema from field_map.json.
    Returns a dict with sections like: golden_static_fields, emergency_contacts, etc.
    """
    try:
        return json.loads(FIELD_MAP_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        # If JSON is malformed or unreadable, fail safe
        return {}


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

    ensure_participants_table()
    ensure_participant_forms_table()
    ensure_notes_table()

def make_license_key(state_abbr: str, address: str) -> str:
    # Simple deterministic-ish key seed; you can replace later with stronger logic
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    safe_state = (state_abbr or "NA").upper()[:2]
    return f"NILPF-{safe_state}-{stamp}"



def transaction_id_used(transaction_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM licenses WHERE transaction_id=?", (transaction_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None

def upsert_license(session_id: str, email: str, name: str, address: str, state_abbr: str, product_sku: str = None, transaction_id: str = None, price_paid: str = None) -> str:
    license_key = make_license_key(state_abbr, address)
    conn = sqlite3.connect(DB_PATH)

    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO licenses (
            created_at, session_id, payer_email, payer_name,
            property_address, property_state, license_key, product_sku, price_paid
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.utcnow().isoformat(),
            session_id,
            email,
            name,
            address,
            state_abbr,
            license_key,
            product_sku,
            price_paid,
        ),
    )
    if transaction_id:
        try:
            cur.execute(
                "UPDATE licenses SET transaction_id=?, price_paid=COALESCE(?, price_paid), product_sku=COALESCE(?, product_sku) WHERE session_id=?",
                (transaction_id, price_paid, product_sku, session_id),
            )
        except Exception:
            try:
                cur.execute(
                    "UPDATE licenses SET transaction_id=?, price_paid=COALESCE(?, price_paid), product_sku=COALESCE(?, product_sku) WHERE license_key=?",
                    (transaction_id, price_paid, product_sku, license_key),
                )
            except Exception:
                pass

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


def get_license_session_by_email_address(email: str, address: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT session_id
        FROM licenses
        WHERE lower(trim(payer_email)) = lower(trim(?))
          AND lower(trim(property_address)) = lower(trim(?))
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (email, address),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

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
# In-App Form Definitions
# -------------------------
TEST_FORM_PDF_MAP = {
    "03_Voluntary_Participation_ Acknowledgement.pdf": {
        "participant_name": {"page": 0, "x": 170, "y": 145},
        "signature_name": {"page": 0, "x": 145, "y": 118},
        "signature_date": {"page": 0, "x": 430, "y": 118},
    }
}

FORM_DEFINITIONS = {
    "18_Entry_Screening_v2.2.pdf": {
        "title": "Entry Screening",
        "fields": [
            {"name": "legal_name", "label": "Legal Name", "type": "text"},
            {"name": "preferred_name", "label": "Preferred Name", "type": "text"},
            {"name": "date_of_birth", "label": "Date of Birth", "type": "date"},
            {"name": "move_in_date", "label": "Move-In Date", "type": "date"},
            {"name": "mobility_status", "label": "Mobility Status", "type": "text"},
            {"name": "self_preservation", "label": "Able to Self-Preserve?", "type": "text"},
            {"name": "orientation_status", "label": "Orientation Status", "type": "text"},
            {"name": "smoking_status", "label": "Smoking Status", "type": "text"},
            {"name": "safety_concerns", "label": "Safety Concerns", "type": "textarea"},
            {"name": "screening_notes", "label": "Screening Notes", "type": "textarea"},
            {"name": "screened_by", "label": "Screened By", "type": "text"},
            {"name": "screening_date", "label": "Screening Date", "type": "date"},
            {"name": "screening_result", "label": "Screening Result", "type": "text"},
        ]
    },

    "7_Emergency_Contact_Form.pdf": {
        "title": "Emergency Contact Form",
        "fields": [
            {"name": "legal_name", "label": "Legal Name", "type": "text"},
            {"name": "primary_contact_name", "label": "Primary Contact Name", "type": "text"},
            {"name": "primary_contact_phone", "label": "Primary Contact Phone", "type": "text"},
            {"name": "primary_contact_relationship", "label": "Relationship", "type": "text"},
            {"name": "secondary_contact_name", "label": "Secondary Contact Name", "type": "text"},
            {"name": "secondary_contact_phone", "label": "Secondary Contact Phone", "type": "text"},
            {"name": "secondary_contact_relationship", "label": "Secondary Relationship", "type": "text"},
            {"name": "hospital_preference", "label": "Preferred Hospital", "type": "text"},
            {"name": "emergency_notes", "label": "Emergency Notes", "type": "textarea"},
        ]
    },

    "16_Participant_Financial_Responsibility_Agreement.pdf": {
        "title": "Participant Financial Responsibility Agreement",
        "fields": [
            {"name": "legal_name", "label": "Legal Name", "type": "text"},
            {"name": "monthly_amount", "label": "Monthly Amount", "type": "text"},
            {"name": "payment_due_date", "label": "Payment Due Date", "type": "text"},
            {"name": "payment_method", "label": "Payment Method", "type": "text"},
            {"name": "responsible_party", "label": "Responsible Party", "type": "text"},
            {"name": "responsible_party_phone", "label": "Responsible Party Phone", "type": "text"},
            {"name": "financial_notes", "label": "Financial Notes", "type": "textarea"},
            {"name": "agreement_date", "label": "Agreement Date", "type": "date"},
        ]
    },

    "13-Communication_and_Consent_Form.pdf": {
        "title": "Communication and Consent Form",
        "fields": [
            {"name": "legal_name", "label": "Legal Name", "type": "text"},
            {"name": "contact_phone", "label": "Phone", "type": "text"},
            {"name": "contact_email", "label": "Email", "type": "text"},
            {"name": "preferred_contact_method", "label": "Preferred Contact Method", "type": "text"},
            {"name": "consent_to_call", "label": "Consent to Call", "type": "text"},
            {"name": "consent_to_text", "label": "Consent to Text", "type": "text"},
            {"name": "consent_to_email", "label": "Consent to Email", "type": "text"},
            {"name": "communication_notes", "label": "Communication Notes", "type": "textarea"},
        ]
    },

    "12-Transfer_Form.pdf": {
        "title": "Transfer / Exit Form",
        "fields": [
            {"name": "legal_name", "label": "Legal Name", "type": "text"},
            {"name": "transfer_date", "label": "Transfer / Exit Date", "type": "date"},
            {"name": "reason_for_exit", "label": "Reason for Exit", "type": "textarea"},
            {"name": "forwarding_destination", "label": "Forwarding Destination", "type": "text"},
            {"name": "property_returned", "label": "Property Returned?", "type": "text"},
            {"name": "keys_returned", "label": "Keys Returned?", "type": "text"},
            {"name": "room_condition", "label": "Room Condition", "type": "textarea"},
            {"name": "exit_notes", "label": "Exit Notes", "type": "textarea"},
            {"name": "completed_by", "label": "Completed By", "type": "text"},
        ]
    },
}


# -------------------------
# Participant PDF source lookup
# -------------------------
def get_source_pdf_relpath(form_name: str) -> str:
    from pathlib import Path

    raw = Path(form_name).name if form_name else ""
    if not raw:
        return ""

    source_hint = ""
    fname = raw
    if "|" in raw:
        source_hint, fname = raw.split("|", 1)
        source_hint = Path(source_hint).name.strip()
        fname = Path(fname).name.strip()
    else:
        fname = Path(raw).name.strip()

    source_map = {
        "EF": "EF_v2.2",
        "EF_v2.2": "EF_v2.2",
        "CORE": "Core-v2.1",
        "Core": "Core-v2.1",
        "Core-v2.1": "Core-v2.1",
    }

    if source_hint in source_map:
        preferred = Path(source_map[source_hint]) / fname
        if preferred.exists():
            return preferred.as_posix()

    # Only the 2 real source folders, plus static/documents as a last fallback
    direct = [
        Path("EF_v2.2") / fname,
        Path("Core-v2.1") / fname,
        Path("static/documents") / fname,
    ]
    for c in direct:
        if c.exists():
            return c.as_posix()

    # Search only inside EF_v2.2 and Core-v2.1
    for base in [Path("EF_v2.2"), Path("Core-v2.1")]:
        if base.exists():
            hits = list(base.rglob(fname))
            if hits:
                return hits[0].as_posix()

    return ""

def get_source_pdf_url(form_name: str) -> str:
    from urllib.parse import quote
    return f"/source-pdf/{quote(form_name)}" if form_name else ""

def candidate_form_keys(form_name: str):
    from pathlib import Path as _Path
    import re as _re

    raw = _Path(form_name).name if form_name else ""
    if not raw:
        return []

    stem = _Path(raw).stem
    suffix = _Path(raw).suffix or ".pdf"
    variants = []

    def add(name):
        if name and name not in variants:
            variants.append(name)

    def add_swaps(name):
        add(name)
        add(name.replace("&", "AND"))
        add(name.replace("AND", "&"))

    add_swaps(raw)

    m = _re.match(r"^(\d+)(.*)$", stem)
    if m:
        num = m.group(1)
        rest = m.group(2)
        try:
            n = int(num)
            for width in (1, 2, 3):
                add_swaps(f"{n:0{width}d}{rest}{suffix}")
        except Exception:
            pass

    norm = stem
    norm = norm.replace("&", " AND ")
    norm = norm.replace("-", " ")
    norm = norm.replace("_", " ")
    norm = _re.sub(r"\s+", " ", norm).strip()

    add_swaps(norm.replace(" ", "_") + suffix)

    m2 = _re.match(r"^(\d+)(.*)$", norm)
    if m2:
        num = m2.group(1)
        rest = m2.group(2)
        rest_us = rest.replace(" ", "_")
        try:
            n = int(num)
            for width in (1, 2, 3):
                add_swaps(f"{n:0{width}d}{rest_us}{suffix}")
        except Exception:
            pass

    return variants

def resolve_layout_path(layout_dir, form_name: str):
    from pathlib import Path as _Path
    for key in candidate_form_keys(form_name):
        candidate = _Path(layout_dir) / f"{_Path(key).name}.json"
        if candidate.exists():
            return candidate
    return _Path(layout_dir) / f"{_Path(form_name).name}.json"

@app.route("/source-pdf/<path:form_name>")
def source_pdf(form_name):
    raw_name = Path(form_name).name

    rel = get_source_pdf_relpath(raw_name)
    if not rel:
        for key in candidate_form_keys(raw_name):
            rel = get_source_pdf_relpath(key)
            if rel:
                break

    if not rel:
        abort(404)

    return send_file(rel, mimetype="application/pdf")

# -------------------------
# Participant Workflow UI
# -------------------------
FORM_LABELS = {
    "00_Member_Bill_of_Rights_v2.1.pdf": "Member Bill of Rights",
    "01_NILPF_Charter_of_Human_Dignity_and_Independent_Living_v2.1.pdf": "Charter of Human Dignity and Independent Living",
    "18_Entry_Screening_v2.2.pdf": "Entry Screening",
    "1_Independent_Living_Disclosure_v2.1.pdf": "Independent Living Disclosure",
    "2_No_Services_No_Supervision_Acknowledgement.pdf": "No Services / No Supervision Acknowledgement",
    "3_Voluntary_Participation_Acknowledgement.pdf": "Voluntary Participation Acknowledgement",
    "4_House_Rules_and_Community_Standards.pdf": "House Rules and Community Standards",
    "20_Participant_Privacy_AND_Non_Commercialization_Acknowledgement.pdf": "Participant Privacy and Non-Commercialization Acknowledgement",
    "21_Property_AND_Personal_Belongings_Acknowledgement.pdf": "Property and Personal Belongings Acknowledgement",
    "16_Participant_Financial_Responsibility_Agreement.pdf": "Participant Financial Responsibility Agreement",
    "15_IMPORTANT_NOTICE_AND_DISCLAIMER_v2.1.pdf": "Important Notice and Disclaimer",
    "17_NO_PAYEE_FINANCIAL_CONTROL_DISCLOSURE.pdf": "No Payee Financial Control Disclosure",
    "7_Emergency_Contact_Form.pdf": "Emergency Contact Form",
    "6_Emergency_Evacuation_Plan.pdf": "Emergency Evacuation Plan",
    "5_Fire_Safety_and_Self-Preservation_Acknowledgement.pdf": "Fire Safety and Self-Preservation Acknowledgement",
    "19-Common_Area_Security_Disclosure.pdf": "Common Area Security Disclosure",
    "13-Communication_and_Consent_Form.pdf": "Communication and Consent Form",
    "10-Pet_Animal_Information_Sheet.pdf": "Pet / Animal Information Sheet",
    "11-Vehicle_Parking_Information_Form.pdf": "Vehicle Parking Information Form",
    "9_Guest_Addendum.pdf": "Guest Addendum",
    "8_Incident_Report_Form.pdf": "Incident Report Form",
    "15_COMPLAINT_GRIEVANCE_PROCEDURE_FORM.pdf": "Complaint / Grievance Procedure Form",
    "12-Transfer_Form.pdf": "Transfer / Exit Form",
}

GROUP_ORDER = [
    "Foundation",
    "Entry",
    "Disclosures",
    "Program Agreements",
    "Safety",
    "Resident Info",
    "Administration",
    "Exit",
]

FORM_GROUPS = {
    "00_Member_Bill_of_Rights_v2.1.pdf": "Foundation",
    "01_NILPF_Charter_of_Human_Dignity_and_Independent_Living_v2.1.pdf": "Foundation",

    "18_Entry_Screening_v2.2.pdf": "Entry",

    "1_Independent_Living_Disclosure_v2.1.pdf": "Disclosures",
    "2_No_Services_No_Supervision_Acknowledgement.pdf": "Disclosures",
    "3_Voluntary_Participation_Acknowledgement.pdf": "Disclosures",

    "4_House_Rules_and_Community_Standards.pdf": "Program Agreements",
    "20_Participant_Privacy_AND_Non_Commercialization_Acknowledgement.pdf": "Program Agreements",
    "21_Property_AND_Personal_Belongings_Acknowledgement.pdf": "Program Agreements",
    "16_Participant_Financial_Responsibility_Agreement.pdf": "Program Agreements",
    "15_IMPORTANT_NOTICE_AND_DISCLAIMER_v2.1.pdf": "Program Agreements",
    "17_NO_PAYEE_FINANCIAL_CONTROL_DISCLOSURE.pdf": "Program Agreements",

    "7_Emergency_Contact_Form.pdf": "Safety",
    "6_Emergency_Evacuation_Plan.pdf": "Safety",
    "5_Fire_Safety_and_Self-Preservation_Acknowledgement.pdf": "Safety",
    "19-Common_Area_Security_Disclosure.pdf": "Safety",

    "13-Communication_and_Consent_Form.pdf": "Resident Info",
    "10-Pet_Animal_Information_Sheet.pdf": "Resident Info",
    "11-Vehicle_Parking_Information_Form.pdf": "Resident Info",
    "9_Guest_Addendum.pdf": "Resident Info",

    "8_Incident_Report_Form.pdf": "Administration",
    "15_COMPLAINT_GRIEVANCE_PROCEDURE_FORM.pdf": "Administration",

    "12-Transfer_Form.pdf": "Exit",
}


FORM_REQUIREMENTS = {
    "00_Member_Bill_of_Rights_v2.1.pdf": "required",
    "01_NILPF_Charter_of_Human_Dignity_and_Independent_Living_v2.1.pdf": "required",

    "18_Entry_Screening_v2.2.pdf": "required",

    "1_Independent_Living_Disclosure_v2.1.pdf": "required",
    "2_No_Services_No_Supervision_Acknowledgement.pdf": "required",
    "3_Voluntary_Participation_Acknowledgement.pdf": "required",

    "4_House_Rules_and_Community_Standards.pdf": "required",
    "MASTER_LICENSE_AGREEMENT_MLA_v2.2.pdf": "required",
    "20_Participant_Privacy_AND_Non_Commercialization_Acknowledgement.pdf": "required",
    "21_Property_AND_Personal_Belongings_Acknowledgement.pdf": "required",
    "16_Participant_Financial_Responsibility_Agreement.pdf": "conditional",
    "15_IMPORTANT_NOTICE_AND_DISCLAIMER_v2.1.pdf": "required",
    "17_NO_PAYEE_FINANCIAL_CONTROL_DISCLOSURE.pdf": "required",

    "7_Emergency_Contact_Form.pdf": "required",
    "6_Emergency_Evacuation_Plan.pdf": "required",
    "5_Fire_Safety_and_Self-Preservation_Acknowledgement.pdf": "required",
    "19-Common_Area_Security_Disclosure.pdf": "conditional",

    "13-Communication_and_Consent_Form.pdf": "conditional",
    "10-Pet_Animal_Information_Sheet.pdf": "conditional",
    "11-Vehicle_Parking_Information_Form.pdf": "conditional",
    "9_Guest_Addendum.pdf": "conditional",

    "8_Incident_Report_Form.pdf": "conditional",
    "15_COMPLAINT_GRIEVANCE_PROCEDURE_FORM.pdf": "required",

    "12-Transfer_Form.pdf": "conditional",
}


def participant_workflow(participant_id):
    import sqlite3
    from urllib.parse import quote

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    participant = cur.execute(
        """
        SELECT id, legal_name, preferred_name, dob, gender, phone, email,
               address, city, state, zip_code,
               emergency_contact_name, emergency_contact_phone,
               move_in_date, room_unit, created_at
        FROM participants
        WHERE id=?
        """,
        (participant_id,)
    ).fetchone()

    form_rows = cur.execute(
        "SELECT form_name, is_complete, completed_at FROM participant_forms WHERE participant_id=?",
        (str(participant_id),)
    ).fetchall()

    conn.close()

    if not participant:
        return None, {}, {"completed": 0, "total_required": 0, "percent": 0}

    completed_map = {
        row[0]: {"is_complete": bool(row[1]), "completed_at": row[2]}
        for row in form_rows
    }

    grouped_defs = get_grouped_participant_forms()
    grouped = {}
    total_required = 0
    completed_required = 0

    for group_name, forms in grouped_defs.items():
        items = []
        for form in forms:
            form_name = form.get("form_name") or form.get("name") or ""
            label = form.get("label") or form_name
            status = completed_map.get(form_name, {})
            is_complete = bool(status.get("is_complete"))
            completed_at = status.get("completed_at")
            href = f"/participant-form/{participant_id}/{quote(form_name)}"
            pdf_url = get_source_pdf_url(form_name)

            is_conditional = bool(form.get("conditional"))
            is_required = not is_conditional
            requirement_label = "Conditional" if is_conditional else "Required"

            if is_required:
                total_required += 1
                if is_complete:
                    completed_required += 1

            items.append({
                "form_name": form_name,
                "label": label,
                "href": href,
                "pdf_url": pdf_url,
                "is_complete": is_complete,
                "completed_at": completed_at,
                "is_required": is_required,
                "is_conditional": is_conditional,
                "requirement_label": requirement_label
            })
        items = sorted(items, key=lambda x: x.get("is_complete", False))
        grouped[group_name] = items

    percent = int((completed_required / total_required) * 100) if total_required else 0
    entry_ready = completed_required >= total_required and total_required > 0

    progress = {
        "completed": completed_required,
        "total_required": total_required,
        "percent": percent,
        "entry_ready": entry_ready
    }


    grouped = dict(sorted(
        grouped.items(),
        key=lambda g: all(item.get("is_complete", False) for item in g[1])
    ))

    return participant, grouped, progress



def get_grouped_participant_forms():
    return {
        "Foundation": [
            {"form_name": "00_Member_Bill_of_Rights_v2.1.pdf", "label": "Member Bill of Rights", "conditional": False},
            {"form_name": "01_NILPF_Charter_of_Human_Dignity_and_Independent_Living_v2.1.pdf", "label": "NILPF Charter of Human Dignity and Independent Living", "conditional": False}
        ],
        "Entry": [
            {"form_name": "18_Entry_Screening_v2.2.pdf", "label": "Entry Screening", "conditional": False},
            {"form_name": "3_Voluntary_Participation_Acknowledgement.pdf", "label": "Voluntary Participation Acknowledgement", "conditional": False},
            {"form_name": "13-Communication_and_Consent_Form.pdf", "label": "Communication and Consent Form", "conditional": True}
        ],
        "Disclosures": [
            {"form_name": "1_Independent_Living_Disclosure_v2.1.pdf", "label": "Independent Living Disclosure", "conditional": False},
            {"form_name": "2_No_Services_No_Supervision_Acknowledgement.pdf", "label": "No Services / No Supervision Acknowledgement", "conditional": False}
        ],
        "Program Agreements": [
            {"form_name": "MASTER_LICENSE_AGREEMENT_MLA_v2.2.pdf", "label": "Master License Agreement (MLA)", "conditional": False},
            {"form_name": "4_House_Rules_and_Community_Standards.pdf", "label": "House Rules and Community Standards", "conditional": False},
            {"form_name": "16_Participant_Financial_Responsibility_Agreement.pdf", "label": "Participant Financial Responsibility Agreement", "conditional": True},
            {"form_name": "15_IMPORTANT_NOTICE_AND_DISCLAIMER_v2.1.pdf", "label": "Important Notice and Disclaimer", "conditional": False},
            {"form_name": "17_NO_PAYEE_FINANCIAL_CONTROL_DISCLOSURE.pdf", "label": "No Payee Financial Control Disclosure", "conditional": False},
            {"form_name": "20_Participant_Privacy_AND_Non_Commercialization_Acknowledgement.pdf", "label": "Participant Privacy and Non-Commercialization Acknowledgement", "conditional": False},
            {"form_name": "21_Property_AND_Personal_Belongings_Acknowledgement.pdf", "label": "Property and Personal Belongings Acknowledgement", "conditional": False}
        ],
        "Safety": [
            {"form_name": "7_Emergency_Contact_Form.pdf", "label": "Emergency Contact Form", "conditional": False},
            {"form_name": "6_Emergency_Evacuation_Plan.pdf", "label": "Emergency Evacuation Plan", "conditional": False},
            {"form_name": "5_Fire_Safety_and_Self-Preservation_Acknowledgement.pdf", "label": "Fire Safety and Self-Preservation Acknowledgement", "conditional": False},
            {"form_name": "19-Common_Area_Security_Disclosure.pdf", "label": "Common Area Security Disclosure", "conditional": True}
        ],
        "Resident Info": [
            {"form_name": "10-Pet_Animal_Information_Sheet.pdf", "label": "Pet / Animal Information Sheet", "conditional": True},
            {"form_name": "11-Vehicle_Parking_Information_Form.pdf", "label": "Vehicle / Parking Information Form", "conditional": True},
            {"form_name": "9_Guest_Addendum.pdf", "label": "Guest Addendum", "conditional": True}
        ],
        "Administration": [
            {"form_name": "8_Incident_Report_Form.pdf", "label": "Incident Report Form", "conditional": True},
            {"form_name": "15_COMPLAINT_GRIEVANCE_PROCEDURE_FORM.pdf", "label": "Complaint / Grievance Procedure Form", "conditional": False}
        ],
        "Exit": [
            {"form_name": "12-Transfer_Form.pdf", "label": "Transfer / Exit Form", "conditional": True}
        ]
    }



GENERIC_FORM_FIELDS = [
    {"name": "participant_name", "label": "Participant Name", "type": "text"},
    {"name": "date", "label": "Date", "type": "date"},
    {"name": "notes", "label": "Notes", "type": "textarea"}
]

def get_form_definition(form_name):
    if form_name in FORM_DEFINITIONS:
        return FORM_DEFINITIONS[form_name]

    label = FORM_LABELS.get(form_name, form_name.replace(".pdf", "").replace("_", " "))
    return {
        "title": label,
        "fields": globals().get("GENERIC_FORM_FIELDS", [{"name":"participant_name","label":"Participant Name","type":"text"},{"name":"date","label":"Date","type":"date"},{"name":"notes","label":"Notes","type":"textarea"}]),
    }


def get_participant_form_values(participant_id, form_name):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT field_name, field_value
        FROM participant_form_data
        WHERE participant_id=? AND form_name=?
    """, (str(participant_id), form_name)).fetchall()
    conn.close()
    return {k: v for k, v in rows}

def save_participant_form_values(participant_id, form_name, form_data):
    import sqlite3
    from datetime import datetime
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    for field_name, field_value in form_data.items():
        cur.execute("""
            INSERT INTO participant_form_data
            (participant_id, form_name, field_name, field_value, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(participant_id, form_name, field_name)
            DO UPDATE SET field_value=excluded.field_value, updated_at=excluded.updated_at
        """, (
            str(participant_id),
            form_name,
            field_name,
            field_value,
            datetime.utcnow().isoformat()
        ))

    conn.commit()
    conn.close()

def auto_mark_form_complete_if_has_data(participant_id, form_name):
    import sqlite3
    from datetime import datetime
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    count = cur.execute("""
        SELECT COUNT(*)
        FROM participant_form_data
        WHERE participant_id=? AND form_name=? AND COALESCE(TRIM(field_value),'') <> ''
    """, (str(participant_id), form_name)).fetchone()[0]

    if count > 0:
        cur.execute("""
            UPDATE participant_forms
            SET is_complete=1, completed_at=?
            WHERE participant_id=? AND form_name=?
        """, (datetime.utcnow().isoformat(), str(participant_id), form_name))

    conn.commit()
    conn.close()



@app.route("/log-dignity-screen", methods=["POST"])
def log_dignity_screen():
    from datetime import datetime
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    with open("dignity_log.txt", "a") as f:
        f.write(f"Dignity screen shown: {ts}\n")
    return "ok"

@app.route("/participant-form/<int:participant_id>/<path:form_name>", methods=["GET", "POST"])
def participant_form_page(participant_id, form_name):
    form_name = unquote(form_name)

    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    participant = cur.execute(
        """
        SELECT id, legal_name, preferred_name, dob, gender, phone, email,
               address, city, state, zip_code,
               emergency_contact_name, emergency_contact_phone,
               move_in_date, room_unit, created_at
        FROM participants
        WHERE id=?
        """,
        (participant_id,)
    ).fetchone()
    conn.close()

    if not participant:
        abort(404, "Participant not found.")

    (
        pid, legal_name, preferred_name, dob, gender, phone, email,
        address, city, state, zip_code,
        emergency_contact_name, emergency_contact_phone,
        move_in_date, room_unit, created_at
    ) = participant
    display_name = preferred_name or legal_name
    form_def = get_form_definition(form_name)

    from pathlib import Path as _Path
    import json as _json
    layout_path = resolve_layout_path(_Path("form_builder_layouts"), form_name)
    pdf_layout = []
    if layout_path.exists():
        try:
            pdf_layout = _json.loads(layout_path.read_text())
        except Exception:
            pdf_layout = []

    if request.method == "POST":
        from datetime import datetime as _dt
        payload = {}
        for field in form_def["fields"]:
            payload[field["name"]] = request.form.get(field["name"], "").strip()

        signature_name = (request.form.get("signature_name") or "").strip()
        signature_date = (request.form.get("signature_date") or "").strip()
        signature_ack = "yes" if request.form.get("signature_ack") else ""
        signature_data = request.form.get("signature_data", "")
        payload["signature_name"] = signature_name
        payload["signature_date"] = signature_date
        payload["signature_ack"] = signature_ack
        payload["signature_data"] = signature_data

        existing_values = get_participant_form_values(participant_id, form_name)
        if signature_name and signature_ack:
            payload["signed_at"] = existing_values.get("signed_at") or _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            payload["signed_at"] = ""

        save_participant_form_values(participant_id, form_name, payload)
        auto_mark_form_complete_if_has_data(participant_id, form_name)
        return redirect(f"/participant-form/{participant_id}/{quote(form_name)}")

    values = get_participant_form_values(participant_id, form_name)
    # AUTO-FILL EMPTY DATE FIELDS
    from datetime import datetime as _dt
    for _field in form_def.get('fields', []):
        if _field.get('type') == 'date' and not values.get(_field.get('name', '')):
            values[_field['name']] = _dt.utcnow().strftime('%Y-%m-%d')

    # Inject participant demographics if not already set
    participant_demographics = {
        "legal_name": legal_name,
        "preferred_name": preferred_name,
        "dob": dob,
        "gender": gender,
        "phone": phone,
        "email": email,
        "address": address,
        "city": city,
        "state": state,
        "zip_code": zip_code,
        "emergency_contact_name": emergency_contact_name,
        "emergency_contact_phone": emergency_contact_phone,
        "move_in_date": move_in_date,
        "room_unit": room_unit,
    }
    for key, val in participant_demographics.items():
        if key not in values or not values[key]:
            values[key] = val


    if not values.get("legal_name"):
        values["legal_name"] = legal_name

    return render_template_string("""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{{ form_def.title }}</title>
      <style>
        body { font-family: Arial, sans-serif; margin: 0; background: #f6f7fb; color: #111; }
        .wrap { max-width: 920px; margin: 0 auto; padding: 18px; }
        .card { background: #fff; border: 2px solid #111; border-radius: 18px; padding: 18px; margin-bottom: 18px; }
        h1 { margin: 0 0 8px 0; }
        .note { color: #444; margin-bottom: 6px; }
        .field { margin-bottom: 14px; }
        label { display: block; font-weight: 700; margin-bottom: 6px; }
        input, textarea {
          width: 100%; box-sizing: border-box; padding: 10px 12px;
          border: 2px solid #111; border-radius: 12px; font-size: 14px;
          background: #fff;
        }
        textarea { min-height: 110px; resize: vertical; }
        .btnrow { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }
        .btn {
          display: inline-block; text-decoration: none; border: 2px solid #111;
          background: #111; color: #fff; padding: 10px 14px; border-radius: 999px;
          font-weight: 700; cursor: pointer;
        }
        .btn.alt { background: #fff; color: #111; }
      </style>
    </head>
    <body>


<div style="background:#111;color:#fff;padding:10px;">

<a href="/participants" style="color:#fff;margin-right:20px;">Add / View Participants</a>
<a href="/notes" style="color:#fff;">Incident / Status Documentation</a>
</div>

      <div class="wrap">
        <div class="card">
          <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px;">
            <a class="btn alt" href="/">Home</a>
            <a class="btn alt" href="/participant-workflow/{{ pid }}">Back to Workflow</a>
          </div>
          <h1>{{ form_def.title }}</h1>
          <div class="note">Participant: {{ display_name }}</div>
          <div class="note">Legal Name: {{ legal_name }}</div>
        </div>

        <div class="card">
          <h3 style="margin-top:0;">PDF Form (Live View)

<div id="pdf-viewer" style="position:relative;"></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
<script>
(async function() {
  const url = "{{ source_pdf_url }}";
  const pdfjsLib = window['pdfjsLib'];
  pdfjsLib.GlobalWorkerOptions.workerSrc =
    "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

  const container = document.getElementById("pdf-viewer");
  const pdf = await pdfjsLib.getDocument(url).promise;
  const layout = {{ pdf_layout|tojson }};
  const values = {{ values|tojson }};

  function fieldValue(name, type) {
    if (type === "checkbox") return values[name] === "yes";
    return values[name] || "";
  }

  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    const viewport = page.getViewport({ scale: 1.3 });

    const wrap = document.createElement("div");
    wrap.style.position = "relative";
    wrap.style.marginBottom = "20px";

    const canvas = document.createElement("canvas");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    canvas.style.display = "block";

    wrap.appendChild(canvas);
    container.appendChild(wrap);

    await page.render({
      canvasContext: canvas.getContext("2d"),
      viewport
    }).promise;

    const pageFields = layout.filter(f => Number(f.page || 1) === i);

    for (const field of pageFields) {
      const typeMap = {
        name: "text",
        date: "date",
        checkbox: "checkbox",
        signature: "text"
      };
      const nameMap = {
        name: "legal_name",
        date: "signature_date",
        checkbox: "signature_ack",
        signature: "signature_data"
      };

      const input = document.createElement("input");
      input.type = typeMap[field.type] || "text";
      input.name = field.field_name || nameMap[field.type] || field.type;

      input.style.position = "absolute";
      // Snap alignment logic
const snap = 6; // pixels

let x = posX;
let y = posY;

// Snap to grid
x = Math.round(x / snap) * snap;
y = Math.round(y / snap) * snap;

input.style.left = x + "px";
input.style.top = y + "px";
((Number(field.y || 0)) * canvas.height) + "px";
      input.style.zIndex = "9999";
      input.style.background = "rgba(255,255,0,0.92)";
      input.style.border = "3px solid red";
      input.style.borderRadius = "8px";
      input.style.boxSizing = "border-box";

      if (field.type === "realcheckbox") {
                const px = 12;

                el.style.width = px + "px";
                el.style.height = px + "px";
                el.style.minWidth = px + "px";
                el.style.minHeight = px + "px";
                el.style.border = "1px solid #111";
                el.style.borderRadius = "2px";
                el.style.background = "#fff";
                el.style.display = "block";
                el.innerHTML = "";
              } else if (field.type === "checkbox") {
        input.style.width = "8px";
        input.style.height = "8px";
        input.style.minWidth = "8px";
        input.style.minHeight = "8px";
        input.style.padding = "0";
        input.style.margin = "0";
        input.style.border = "1px solid #111";
        input.style.borderRadius = "2px";
        input.style.boxSizing = "border-box";
        input.style.background = "#fff";
        input.checked = fieldValue(input.name, "checkbox");

        input.style.cursor = "pointer";
        input.style.accentColor = "#000";

        input.addEventListener("change", () => {
          if (input.checked) {
            input.style.transform = "scale(1.2)";
          } else {
            input.style.transform = "scale(1)";
          }
        });
        input.value = "yes";
      } else if (field.type === "signature") {
        input.style.width = Math.max(220, (Number(field.width || 0.22) * canvas.width)) + "px";
        input.style.height = "42px";
        input.placeholder = "Signature";
        input.value = fieldValue(input.name, "text");
      } else {
        input.style.width = Math.max(140, (Number(field.width || 0.18) * canvas.width)) + "px";
        input.style.height = "34px";
        input.value = fieldValue(input.name, "text");
      }

      wrap.appendChild(input);
    }
  }
})();
</script>
        </div>
      </div>
    
<div style="
margin-top:40px;
padding-top:12px;
border-top:1px solid #ddd;
text-align:center;
font-size:13px;
color:#666;
">
<img src="/static/pearlzz-logo.png" style="height:28px;opacity:.9;"><br>Pearlzz
© Pearlzz
</div>

        


</body>

    </html>
    """,
    pid=pid,
    legal_name=legal_name,
    display_name=display_name,
    form_def=form_def,
    values=values,
    quoted_form_name=quote(form_name),
    form_name=form_name,
    source_pdf_url=get_source_pdf_url(form_name),
    pdf_layout=pdf_layout)

@app.route("/participant-form-print/<int:participant_id>/<path:form_name>")
def participant_form_print(participant_id, form_name):
    form_name = unquote(form_name)

    import re
    import sqlite3
    from io import BytesIO
    from flask import send_file
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.utils import simpleSplit
    import json
    import base64
    from pathlib import Path
    from reportlab.lib.utils import ImageReader
    from pypdf import PdfReader, PdfWriter

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    participant = cur.execute(
        """
        SELECT id, legal_name, preferred_name, dob, gender, phone, email,
               address, city, state, zip_code,
               emergency_contact_name, emergency_contact_phone,
               move_in_date, room_unit, created_at
        FROM participants
        WHERE id=?
        """,
        (participant_id,)
    ).fetchone()
    conn.close()

    if not participant:
        abort(404, "Participant not found.")

    (
        pid, legal_name, preferred_name, dob, gender, phone, email,
        address, city, state, zip_code,
        emergency_contact_name, emergency_contact_phone,
        move_in_date, room_unit, created_at
    ) = participant

    display_name = preferred_name or legal_name
    form_def = get_form_definition(form_name)
    values = get_participant_form_values(participant_id, form_name)

    source_pdf_path = Path("static/documents") / form_name
    original_pdf_exists = source_pdf_path.exists()

    layout_dir = Path("form_builder_layouts")
    layout_fields = []

    direct_file = layout_dir / (Path(form_name).name + ".json")
    if direct_file.exists():
        try:
            layout_fields = json.loads(direct_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    target_name = Path(form_name).name
    target_stem = Path(form_name).stem

    for f in layout_dir.glob("*.json"):
        try:
            json_name = f.name
            json_stem = f.stem
            if (
                json_name == target_name + ".json"
                or json_stem == target_name
                or json_stem == target_stem
                or json_stem.endswith("_" + target_name)
                or json_stem.endswith("_" + target_stem)
            ):
                layout_fields = json.loads(f.read_text(encoding="utf-8"))
                break
        except Exception:
            pass

    participant_demographics = {
        "legal_name": legal_name,
        "preferred_name": preferred_name,
        "dob": dob,
        "gender": gender,
        "phone": phone,
        "email": email,
        "address": address,
        "city": city,
        "state": state,
        "zip_code": zip_code,
        "emergency_contact_name": emergency_contact_name,
        "emergency_contact_phone": emergency_contact_phone,
        "move_in_date": move_in_date,
        "room_unit": room_unit,
    }
    for key, val in participant_demographics.items():
        if key not in values or not values[key]:
            values[key] = val

    if not values.get("legal_name"):
        values["legal_name"] = legal_name

    # --- Layout preview rendering ---
    if original_pdf_exists and layout_fields:
        try:
            base_reader = PdfReader(str(source_pdf_path))
            writer = PdfWriter()

            for page_index, page in enumerate(base_reader.pages, start=1):
                packet = BytesIO()
                c = canvas.Canvas(packet, pagesize=letter)

                for field in layout_fields:
                    if field.get("page") != page_index:
                        continue

                    x = field.get("x", 0.5) * 612
                    y = (1 - field.get("y", 0.5)) * 792

                    c.setFont("Helvetica", 10)

                    field_type = field.get("type", "")
                    field_name = field.get("field_name", "")
                    val = values.get(field_name, "")

                    if field_type in ("name", "text", "email", "phone", "address"):
                        c.drawString(x, y, str(val or values.get("legal_name", "")))

                    elif field_type == "date":
                        c.drawString(x, y, str(val or values.get("signature_date", "")))

                    elif field_type == "checkbox":
                        checked = str(val).lower() in ("yes", "true", "1", "on", "checked")
                        if checked:
                            c.setFont("Helvetica-Bold", 12)
                            c.drawString(x, y, "X")
                            c.setFont("Helvetica", 10)

                    elif field_type == "signature":
                        signature_data = values.get(field_name, "") or values.get("signature_data", "")
                        if isinstance(signature_data, str) and signature_data.startswith("data:image"):
                            try:
                                import base64
                                from io import BytesIO as _BytesIO
                                from reportlab.lib.utils import ImageReader

                                header, encoded = signature_data.split(",", 1)
                                img_bytes = base64.b64decode(encoded)
                                sig_img = ImageReader(_BytesIO(img_bytes))

                                sig_w = max(120, int(field.get("width", 0.42) * 612))
                                sig_h = 36
                                c.drawImage(
                                    sig_img,
                                    x,
                                    y - 18,
                                    width=sig_w,
                                    height=sig_h,
                                    preserveAspectRatio=True,
                                    mask='auto'
                                )
                            except Exception:
                                c.drawString(x, y, "[sig]")
                        elif signature_data:
                            c.drawString(x, y, "[sig]")

                c.showPage()
                c.save()
                packet.seek(0)

                overlay = PdfReader(packet)
                if len(overlay.pages) > 0:
                    page.merge_page(overlay.pages[0])
                writer.add_page(page)

            out = BytesIO()
            writer.write(out)

            return send_file(
                BytesIO(out.getvalue()),
                mimetype="application/pdf",
                as_attachment=True,
                download_name="layout_preview.pdf"
            )
        except Exception as e:
            print("PDF OVERLAY ERROR:", repr(e))
            raise

    from pathlib import Path
    from pypdf import PdfReader, PdfWriter

    source_pdf_path = Path("static/documents") / form_name
    original_pdf_exists = source_pdf_path.exists()

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    left = 0.75 * inch
    right = width - 0.75 * inch
    top = height - 0.75 * inch
    y = top

    def new_page():
        nonlocal y
        c.showPage()
        y = top
        c.setFont("Helvetica", 11)

    def draw_wrapped(label, value):
        nonlocal y
        label = str(label or "").strip()
        value = str(value or "").strip()
        if not value:
            value = ""

        label_lines = simpleSplit(label, "Helvetica-Bold", 11, right - left)
        value_lines = simpleSplit(value, "Helvetica", 11, right - left)

        needed = (len(label_lines) * 14) + (max(1, len(value_lines)) * 14) + 14
        if y - needed < 0.75 * inch:
            new_page()

        c.setFont("Helvetica-Bold", 11)
        for line in label_lines:
            c.drawString(left, y, line)
            y -= 14

        c.setFont("Helvetica", 11)
        if value_lines:
            for line in value_lines:
                c.drawString(left, y, line)
                y -= 14
        else:
            c.drawString(left, y, "")
            y -= 14

        y -= 8
        c.line(left, y, right, y)
        y -= 14

    c.setTitle(f"{form_def['title']} - {display_name}")

    c.setFont("Helvetica-Bold", 18)
    c.drawString(left, y, form_def["title"])
    y -= 24

    c.setFont("Helvetica", 11)
    c.drawString(left, y, f"Participant: {display_name}")
    y -= 16
    if legal_name != display_name:
        c.drawString(left, y, f"Legal Name: {legal_name}")
        y -= 16

    c.drawString(left, y, "Participant copy generated from the NILPF workflow.")
    y -= 22
    c.line(left, y, right, y)
    y -= 18

    for field in form_def.get("fields", []):
        draw_wrapped(field.get("label", ""), values.get(field.get("name", ""), ""))

    signer_ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.remote_addr
        or ""
    )

    if values.get("signature_name") or values.get("signature_date") or values.get("signed_at") or values.get("signature_data"):
        if y - 220 < 0.75 * inch:
            new_page()

        c.setFont("Helvetica-Bold", 13)
        c.drawString(left, y, "Participant Signature")
        y -= 20
        c.setFont("Helvetica", 11)

        signature_data = values.get("signature_data", "") or ""
        if signature_data.startswith("data:image"):
            try:
                import base64
                from io import BytesIO as _BytesIO
                from reportlab.lib.utils import ImageReader

                header, encoded = signature_data.split(",", 1)
                img_bytes = base64.b64decode(encoded)
                sig_img = ImageReader(_BytesIO(img_bytes))

                box_w = 4.8 * inch
                box_h = 1.5 * inch

                c.roundRect(left, y - box_h, box_w, box_h, 8, stroke=1, fill=0)
                c.drawImage(sig_img, left + 6, y - box_h + 6, width=box_w - 12, height=box_h - 12, preserveAspectRatio=True, mask='auto')
                y -= (box_h + 14)
            except Exception:
                draw_wrapped("Signature Image", "[Unable to render saved signature]")
        else:
            draw_wrapped("Signature Image", "[No handwritten signature captured]")

        draw_wrapped("Signer Full Legal Name", values.get("signature_name", ""))
        draw_wrapped("Signature Date", values.get("signature_date", ""))
        draw_wrapped("Signed At", values.get("signed_at", ""))
        draw_wrapped("IP Address", signer_ip)

    if y - 40 < 0.75 * inch:
        new_page()

    c.setFont("Helvetica-Oblique", 9)
    c.drawString(left, y, "This copy reflects the information currently saved in the participant workflow for this form.")

    c.showPage(); c.save(); buf.seek(0)
    overlay_bytes = buf.getvalue()

    source_pdf_bytes = Path(form_def["file"]).read_bytes()

    from pypdf import PdfReader, PdfWriter
    import io

    source_stream = io.BytesIO(source_pdf_bytes)
    overlay_stream = io.BytesIO(overlay_bytes)

    reader = PdfReader(source_stream)
    overlay_reader = PdfReader(overlay_stream)
    writer = PdfWriter()

    for i, page in enumerate(reader.pages):
        if i < len(overlay_reader.pages):
            page.merge_page(overlay_reader.pages[i])
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)

    final_bytes = out.getvalue()

    safe_display = re.sub(r"[^A-Za-z0-9_-]+", "_", display_name or "participant").strip("_") or "participant"
    safe_form = re.sub(r"[^A-Za-z0-9_-]+", "_", form_def["title"]).strip("_") or "form"
    filename = f"{safe_display}_{safe_form}_signed_copy.pdf"

    from pathlib import Path as _Path
    signed_dir = _Path("signed_docs")
    signed_dir.mkdir(parents=True, exist_ok=True)
    (signed_dir / filename).write_bytes(final_bytes)

    return send_file(
        BytesIO(final_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename
    )

@app.route("/home")
def app_home():
    return render_template_string("""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>NILPF Home</title>
        <style>
          body {
            font-family: Arial, sans-serif;
            margin: 0;
            background: #f6f7fb;
            color: #111;
          }
          .topbar {
            background: #111;
            color: #fff;
            padding: 10px 16px;
          }
          .topbar a {
            color: #fff;
            margin-right: 20px;
            text-decoration: none;
            font-weight: 700;
          }
          .wrap {
            max-width: 1000px;
            margin: 0 auto;
            padding: 28px;
          }
          .hero {
            background: #fff;
            border: 2px solid #111;
            border-radius: 20px;
            padding: 24px;
            margin-bottom: 20px;
          }
          .hero h1 {
            margin: 0 0 10px 0;
            font-size: 34px;
          }
          .hero p {
            margin: 0;
            color: #444;
            font-size: 18px;
          }
          .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 16px;
          }
          .card {
            background: #fff;
            border: 2px solid #111;
            border-radius: 18px;
            padding: 20px;
          }
          .card h2 {
            margin: 0 0 8px 0;
            font-size: 22px;
          }
          .card p {
            margin: 0 0 14px 0;
            color: #444;
          }
          .btn {
            display: inline-block;
            text-decoration: none;
            border: 2px solid #111;
            background: #111;
            color: #fff;
            padding: 10px 14px;
            border-radius: 999px;
            font-weight: 700;
          }
          .footer-note {
            margin-top: 20px;
            color: #555;
            font-size: 14px;
          }
          .home-float {
            position: fixed;
            right: 22px;
            bottom: 22px;
            z-index: 9999;
            display: inline-block;
            text-decoration: none;
            border: 2px solid #111;
            background: #fff;
            color: #111;
            padding: 10px 14px;
            border-radius: 999px;
            font-weight: 700;
            box-shadow: 0 2px 8px rgba(0,0,0,.15);
          }
        </style>
      </head>
      <body>

        

        <div class="topbar">
          <a href="/documents?tab=dashboard">Dashboard</a>
          <a href="/participants">Participants</a>
          <a href="/notes">Notes</a>
          <a href="/logout">Logout</a>
        </div>

        <div class="wrap">
          <div class="hero">
            <h1>NILPF Home</h1>
            <p>This is the face of the app. Return here anytime to safely leave participant material and navigate the system.</p>
          </div>

          <div class="grid">
            <div class="card">
              <h2>Add / View Participants</h2>
              <p>Open the participant manager and access participant workflow records.</p>
              <a class="btn" href="/participants">Open Participants</a>
            </div>

            <div class="card">
              <h2>Operational Framework</h2>
              <p>Return to the main framework and document workspace.</p>
              <a class="btn" href="/documents?tab=dashboard">Open Framework</a>
            </div>

            <div class="card">
              <h2>Incident / Status Documentation</h2>
              <p>Record participant decline, incidents, concerns, and follow-up notes.</p>
              <a class="btn" href="/notes">Open Notes</a>
            </div>

            <div class="card">
              <h2>Program Essentials</h2>
              <p>Quick access reminder for the core operator documents and licensed property details: Master License Agreement, Master Lease, property address, license key, buyer/operator, program standards, and Charter / Bill of Rights.</p>
              <a class="btn" href="/documents?tab=dashboard">View Essentials</a>
            </div>
          </div>

          <div class="footer-note">
            Protected workspace for licensed NILPF operators.
          </div>
        </div>
      </body>
    </html>
    """)



@app.route("/participant-form-complete", methods=["POST"])
def participant_form_complete():
    participant_id = request.form.get("participant_id", "").strip()
    form_name = request.form.get("form_name", "").strip()
    go_back = request.form.get("go_back", "").strip()

    if not participant_id or not form_name:
        abort(400, "Missing participant_id or form_name.")

    import sqlite3
    from datetime import datetime

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        UPDATE participant_forms
        SET is_complete=1, completed_at=?
        WHERE participant_id=? AND form_name=?
    """, (datetime.utcnow().isoformat(timespec="seconds"), str(participant_id), form_name))

    if cur.rowcount == 0:
        ts = datetime.utcnow().isoformat(timespec="seconds")
        cur.execute("""
            INSERT INTO participant_forms (participant_id, form_name, is_complete, completed_at, created_at)
            VALUES (?, ?, 1, ?, ?)
        """, (str(participant_id), form_name, ts, ts))

    conn.commit()
    conn.close()

    if go_back:
        return redirect(go_back)

    participant, grouped, progress = participant_workflow(int(participant_id))
    next_form = None

    for section, forms in grouped:
        for form in forms:
            if form.get("required") and not form.get("is_complete"):
                next_form = form
                break
        if next_form:
            break

    if next_form:
        return redirect(f"/participant-form/{participant_id}/{quote(next_form['name'])}")

    return redirect(f"/participant-workflow/{participant_id}")

@app.route("/participant-workflow/<int:participant_id>")
def participant_workflow_page(participant_id):
    participant, grouped, progress = participant_workflow(participant_id)
    if not participant:
        abort(404, "Participant not found.")

    pid = participant[0]
    legal_name = participant[1]
    preferred_name = participant[2]
    created_at = participant[-1]
    display_name = preferred_name or legal_name

    return render_template_string("""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Participant Workflow</title>
        <style>
          body { font-family: Arial, sans-serif; margin: 0; background: #f6f7fb; color: #111; }
          .wrap { max-width: 1150px; margin: 0 auto; padding: 24px; }
          .top { background: #fff; border: 2px solid #111; border-radius: 18px; padding: 18px; margin-bottom: 18px; }
          .top h1 { margin: 0 0 8px 0; font-size: 28px; }
          .note { color: #444; }
          .btnrow { display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }
          .btn { display:inline-block; padding:10px 14px; border-radius:12px; border:2px solid #111; background:#111; color:#fff; text-decoration:none; font-weight:700; cursor:pointer; }
          .btn.alt { background:#fff; color:#111; }
          .progressbox { margin-top:14px; }
          .progressmeta { display:flex; justify-content:space-between; gap:10px; font-weight:700; margin-bottom:8px; }
          .bar { width:100%; height:16px; background:#e5e7eb; border:2px solid #111; border-radius:999px; overflow:hidden; }
          .fill { height:100%; background:#111; }
          .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:16px; }
          .card { background:#fff; border:2px solid #111; border-radius:18px; padding:16px; }
          .card h2 { margin:0 0 12px 0; font-size:20px; }
          .row {
            border-left: 4px solid transparent;
          }
          .row.next-required {
            border-left: 6px solid gold;
            background: #fffbe6;
          } border:1px solid #d7dbe7; border-radius:14px; padding:12px; margin-bottom:10px; background:#fafafa; }
          .row.done { background:#eefbf1; border-color:#b7e4c7; }
          .title { font-weight:700; font-size:16px; margin-bottom:10px; line-height:1.3; }
          .title a { color:#111; text-decoration:none; }
          .title a:hover { text-decoration:underline; }
          .badges { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
          .badge { display:inline-block; padding:4px 8px; border-radius:999px; font-size:12px; font-weight:700; background:#e9ecef; }
          .stamp { font-size:12px; color:#555; margin-top:6px; }
          .actions { display:flex; gap:8px; flex-wrap:wrap; }
          form { margin:0; }
        </style>
      </head>
      <body>

<div id="dignityScreen" onclick="hideDignityScreen()" style="
display:none;
position:fixed;
inset:0;
background:rgba(17,17,17,.96);
color:#fff;
z-index:99999;
align-items:center;
justify-content:center;
text-align:center;
padding:30px;
font-family:Arial,sans-serif;
">
  <div>
    <div style="font-size:34px;font-weight:700;margin-bottom:14px;">Dignity & Privacy Protected</div>
    <div style="font-size:18px;max-width:700px;line-height:1.5;">
      Participant information has been hidden due to inactivity.
      Tap or click anywhere to continue.
    </div>
  </div>
</div>

<style>
.home-icon{
    position:fixed;
    top:60px;
    right:22px;
    font-size:22px;
    text-decoration:none;
    color:#111;
    font-weight:600;
}
.home-icon:hover{
    text-decoration:underline;
}
</style>


        <div class="wrap">
          <div class="top">
            <h1>Participant Workflow</h1>
            <div class="note"><strong>{{ display_name }}</strong> · Participant ID {{ pid }}</div>
            <div class="note">Created: {{ created_at }}</div>

            <div style="margin-bottom:14px;padding:10px 14px;border-radius:12px;font-weight:700;
              {% if progress.entry_ready %}
              background:#d1fae5;border:2px solid #10b981;color:#065f46;
              {% else %}
              background:#fee2e2;border:2px solid #ef4444;color:#7f1d1d;
              {% endif %}
            ">
              ENTRY STATUS:
              {% if progress.entry_ready %}
              READY
              {% else %}
              NOT READY
              {% endif %}
            </div>

            <div class="progressbox">
              <div class="progressmeta">
                <span>Required Forms: {{ progress.completed }} / {{ progress.total_required }} Complete</span>
                <span>{{ progress.percent }}%</span>
              </div>
              <div class="bar">
                <div class="fill" style="width: {{ progress.percent }}%;"></div>
              </div>
            </div>

            <div class="btnrow">
                            <a class="btn alt" href="/participants">Participant Manager</a>
              <a class="btn alt" href="/home">Home</a>
            </div>
          </div>

          <div class="grid">
            {% for group_name, items in grouped.items() %}
              <div class="card">
                <h2>{{ group_name }}</h2>
                {% for item in items %}
                  <div class="row {% if item.is_complete %}done{% elif item.is_required %}next-required{% endif %}">
                    <div class="title">
                      <a href="{{ item.href }}">{{ item.label }}</a>
                    </div>

                    <div class="badges">
                      <span class="badge">{{ item.requirement_label }}</span>
                      <span class="badge">{% if item.is_complete %}Complete{% else %}Pending{% endif %}</span>
                    </div>

                    {% if item.completed_at %}
                      <div class="stamp">Completed: {{ item.completed_at }}</div>
                    {% endif %}

                    <div class="actions">
                      <a class="btn alt" href="{{ item.href }}">Start Form</a>
                      {% if item.pdf_url %}
                      {% endif %}
                      {% if not item.is_complete %}
                      <form method="post" action="/participant-form-complete">
                        <input type="hidden" name="participant_id" value="{{ pid }}">
                        <input type="hidden" name="form_name" value="{{ item.form_name }}">
                        <input type="hidden" name="go_back" value="/participant-workflow/{{ pid }}">
                        <button class="btn alt" type="submit">Mark Complete</button>
                      </form>
                      {% endif %}

                      <a class="btn alt" href="/participant-form-print/{{ pid }}/{{ item.form_name|replace(' ', '%20') }}">Print</a>
                    </div>
                  </div>
                {% endfor %}
              </div>
            {% endfor %}
          </div>
        </div>

<script>
let idleTimer;
let logoutTimer;
const idleTimeLimit = 60000;
const logoutTimeLimit = 300000;

function resetIdleTimer() {
    clearTimeout(idleTimer);
    clearTimeout(logoutTimer);
    idleTimer = setTimeout(showDignityScreen, idleTimeLimit);
    logoutTimer = setTimeout(() => { window.location = "/logout"; }, logoutTimeLimit);
}

function showDignityScreen() {
    const el = document.getElementById("dignityScreen");
    if (el) el.style.display = "flex";
    fetch('/log-dignity-screen', {method:'POST'});
}

function hideDignityScreen() {
    const el = document.getElementById("dignityScreen");
    if (el) el.style.display = "none";
    resetIdleTimer();
}

window.addEventListener("load", resetIdleTimer);
document.addEventListener("mousemove", resetIdleTimer);
document.addEventListener("keypress", resetIdleTimer);
document.addEventListener("click", resetIdleTimer);
document.addEventListener("touchstart", resetIdleTimer);
</script>

      </body>
    </html>
    """, pid=pid, display_name=display_name, created_at=created_at, grouped=grouped, progress=progress)


@app.route("/participant-form-toggle/<int:participant_id>", methods=["POST"])
def participant_form_toggle(participant_id):
    import sqlite3
    is_complete = 1 if str(request.form.get("is_complete", "0")) == "1" else 0
    form_name = request.form.get("form_name", "").strip()
    go_back = request.form.get("go_back") or f"/participant-workflow/{participant_id}"

    if not form_name:
        abort(400, "Missing form_name.")

    completed_at = datetime.utcnow().isoformat() if is_complete else None

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        UPDATE participant_forms
        SET is_complete=?, completed_at=?
        WHERE participant_id=? AND form_name=?
    """, (is_complete, completed_at, str(participant_id), form_name))
    conn.commit()
    conn.close()

    return redirect(go_back)

# -------------------------
# Routes
# -------------------------


@app.route("/login", methods=["GET","POST"])
def login():
    error = None

    if request.method == "POST":
        email = request.form.get("email","").strip()
        address = request.form.get("address","").strip()

        if not email or not address:
            error = "Email and Business Address are required."
        else:
            session_id = get_license_session_by_email_address(email, address)

            if session_id:
                lic = get_license_by_session(session_id)
                if lic:
                    payer_email, payer_name, prop_addr, prop_state, license_key, created_at, product_sku = lic
                    session["licensed_session_id"] = session_id
                    session["license_key"] = license_key
                    session["payer_email"] = payer_email or email
                    session["payer_name"] = payer_name or ""
                    session["licensed_location"] = {
                        "email": payer_email or email,
                        "business_name": payer_name or "",
                        "street": prop_addr or address,
                        "city": "",
                        "state": prop_state or "",
                        "zip": "",
                    }
                    session["property_state"] = prop_state or ""
                    session["product_sku"] = product_sku or ""
                    return redirect(f"/documents?session_id={db_session_id}")
                else:
                    error = "License found, but record could not be opened."
            else:
                error = "No license found for that email and business address."

    return render_template_string("""
    <!doctype html>
    <html>
      <head>
        <title>NILPF Access</title>
        <style>
          body { font-family: Arial; max-width: 420px; margin: 80px auto; }
          input { width:100%; padding:10px; margin:6px 0; }
          button { padding:10px 18px; background:#111; color:#fff; border:none; }
          .error { color:red; margin-top:10px; }
        </style>
      </head>
      <body>

<style>
.home-icon{
    position:fixed;
    top:60px;
    right:22px;
    font-size:22px;
    text-decoration:none;
    color:#111;
    font-weight:600;
}
.home-icon:hover{
    text-decoration:underline;
}
</style>


<div style="background:#111;color:#fff;padding:10px;">

<a href="/documents?tab=dashboard" style="color:#fff;margin-right:20px;">Dashboard</a>
<a href="/participants" style="color:#fff;margin-right:20px;">Add / View Participants</a>
<a href="/notes" style="color:#fff;">Incident / Status Documentation</a>
</div>


        <h2>Access Your Operational Framework</h2>
        <p>Enter the email and business address used during purchase.</p>

        <form method="post">
          <input name="email" placeholder="Email address">
          <input name="address" placeholder="Business address">
          <button type="submit">Access Framework</button>
        </form>

        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}

      </body>
    </html>
    """, error=error)


@app.route("/health")
def health():
    return jsonify(ok=True)
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

import io
from urllib.parse import quote, unquote

@app.route("/certificate")
def certificate():
    session_id="LOCAL_TEST"

    lic=("demo@email.com","Demo User","Local Test Address","OH","TESTKEY","today","STANDARD_SET")

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
    c.showPage(); c.save(); buf.seek(0)

    buf.seek(0)
    filename = f"certificate_{session_id}.pdf"
    return send_file(buf, as_attachment=False, download_name=filename, mimetype="application/pdf")



@app.route("/")
def home():
    return render_template_string("""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>NILPF Operational Framework</title>
        <style>
          body {
            font-family: Arial, sans-serif;
            margin: 0;
            background: #f6f7fb;
            color: #111;
          }
          .wrap {
            max-width: 920px;
            margin: 0 auto;
            padding: 24px 18px 40px;
          }
          .card {
            background: #fff;
            border: 2px solid #111;
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 10px 30px rgba(0,0,0,.06);
          }
          h1 {
            margin-top: 0;
            font-size: 32px;
          }
          .sub {
            font-size: 16px;
            margin-bottom: 12px;
          }
          .note {
            background: #f2f4f8;
            border-left: 5px solid #111;
            padding: 10px 12px;
            border-radius: 10px;
            margin: 12px 0 16px;
          }
          ul {
            line-height: 1.7;
            padding-left: 22px;
          }
          .actions {
            margin-top: 16px;
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
          }
          .btn {
            display: inline-block;
            padding: 10px 14px;
            border: 2px solid #111;
            border-radius: 12px;
            text-decoration: none;
            color: #111;
            font-weight: bold;
            background: #fff;
          }
          .btn.primary {
            background: #111;
            color: #fff;
          }
        </style>
      </head>
      <body>

<style>
.home-icon{
    position:fixed;
    top:60px;
    right:22px;
    font-size:22px;
    text-decoration:none;
    color:#111;
    font-weight:600;
}
.home-icon:hover{
    text-decoration:underline;
}
</style>


<div style="background:#111;color:#fff;padding:10px;">

<a href="/documents?tab=dashboard" style="color:#fff;margin-right:20px;">Dashboard</a>
<a href="/participants" style="color:#fff;margin-right:20px;">Add / View Participants</a>
<a href="/notes" style="color:#fff;">Incident / Status Documentation</a>
</div>

        <div class="wrap">
          <div class="card">
            <h1>NILPF Operational Framework</h1>
            <p class="sub">One product. One licensed address. One app-first workflow.</p>

            <div class="note">
              Activate this framework for one licensed address, complete payment, and return directly into the app.
            </div>

            <ul>
              <li>Operational Framework access</li>
              <li>Framework forms and internal documents</li>
              <li>Download tools inside the app</li>
              <li>Address-based activation flow</li>
            </ul>

            <div class="actions">
              {% if session.get("licensed_location") %}
                <a class="btn primary" href="/activate">Add New Location</a>
              {% else %}
                <a class="btn primary" href="/activate">Activate Primary Location</a>
              {% endif %}
            </div>

            <div style="margin-top:16px;padding:14px;border:2px solid #111;border-radius:14px;">
              <h3 style="margin-top:0;margin-bottom:8px;">Business Address Login</h3>
              <p class="note">Enter your licensed business address to restore access.</p>
              <form method="POST" action="/restore-access">
                <input
                  name="business_address"
                  placeholder="Licensed Business Address"
                  required
                  style="width:100%;padding:10px;border:2px solid #111;border-radius:10px;box-sizing:border-box;margin-bottom:10px;"
                >
                <button class="btn primary" type="submit">Restore Access</button>
              </form>
            </div>

            <div style="margin-top:12px;padding:14px;border:2px solid #111;border-radius:14px;">
              <h3 style="margin-top:0;margin-bottom:8px;">Custom Form App</h3>
              <p class="note">Bonus operator tool for preparing your own PDFs with checkbox, date, time, and signature fields.</p>
              <a class="btn primary" href="/form-builder">Open Form App</a>
            </div>
          </div>
        </div>
      </body>
    </html>
    """)




def get_license_by_business_address(address: str):
    import sqlite3

    def normalize(v: str) -> str:
        v = (v or "").strip().lower()
        v = v.replace(",", " ").replace(".", " ")
        v = " ".join(v.split())
        return v

    addr = normalize(address)
    if not addr:
        return None

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    best_row = None
    best_rank = 999

    for candidate in cur.execute(
        """
        SELECT session_id, payer_email, payer_name, property_address, property_state, license_key, created_at, product_sku
        FROM licenses
        ORDER BY created_at DESC
        """
    ).fetchall():
        stored = normalize(candidate[3] or "")

        rank = None
        if stored == addr:
            rank = 1
        elif stored.startswith(addr):
            rank = 2
        elif addr in stored:
            rank = 3

        if rank is not None and rank < best_rank:
            best_row = candidate
            best_rank = rank
            if rank == 1:
                break

    conn.close()
    return best_row


@app.route("/restore-access", methods=["POST"])
def restore_access():
    business_address = (request.form.get("business_address") or "").strip()
    if not business_address:
        abort(400, "Business address is required.")

    lic = get_license_by_business_address(business_address)
    if not lic:
        return render_template_string("""
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Business Address Login</title>
            <style>
              body{font-family:Arial,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;background:#f6f7fb}
              .card{background:#fff;border:2px solid #111;border-radius:16px;padding:24px}
              .btn{display:inline-block;padding:12px 16px;border:2px solid #111;border-radius:12px;background:#111;color:#fff;text-decoration:none;font-weight:bold}
            </style>
          </head>
          <body>

<style>
.home-icon{
    position:fixed;
    top:60px;
    right:22px;
    font-size:22px;
    text-decoration:none;
    color:#111;
    font-weight:600;
}
.home-icon:hover{
    text-decoration:underline;
}
</style>


            <div class="card">
              <h1>Business Address Login</h1>
              <p>No paid record was found for that address.</p>
              <p>Please try the exact licensed business address used during activation.</p>
              <p><a class="btn" href="/">Return Home</a></p>
            </div>
          </body>
        </html>
        """), 404

    db_session_id, payer_email, payer_name, prop_addr, prop_state, license_key, created_at, product_sku = lic

    session["licensed_session_id"] = db_session_id
    session["product_sku"] = product_sku or "FIRST_PROPERTY"
    session["license_key"] = license_key
    session["licensed_location"] = {
        "business_name": (payer_name or "").strip(),
        "email": (payer_email or "").strip(),
        "street": (prop_addr or "").strip(),
        "city": "",
        "state": (prop_state or "").strip().upper(),
        "zip": "",
    }

    return redirect(f"/documents?session_id={db_session_id}")


@app.route("/activate", methods=["GET", "POST"])
def activate():
    if request.method == "POST":
        session["product_sku"] = "FIRST_PROPERTY"
        session["licensed_location"] = {
            "business_name": (request.form.get("business_name") or "").strip(),
            "email": (request.form.get("email") or "").strip(),
            "street": (request.form.get("street") or "").strip(),
            "city": (request.form.get("city") or "").strip(),
            "state": (request.form.get("state") or "").strip().upper(),
            "zip": (request.form.get("zip") or "").strip(),
        }
        loc = session["licensed_location"]
        required = ["business_name", "email", "street", "city", "state", "zip"]
        if any(not loc.get(k) for k in required):
            abort(400, "All address fields are required.")
        return redirect("/buy")

    return render_template_string("""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Activate Operational Framework</title>
        <style>
          body{font-family:Arial,sans-serif;max-width:1200px;margin:40px auto;padding:0 16px;background:#f6f7fb}
          .card{background:#fff;border:2px solid #111;border-radius:16px;padding:24px}
          .activate-grid{display:flex;flex-wrap:wrap;gap:12px;align-items:end}
          .field{display:flex;flex-direction:column;min-width:260px;flex:1}
          .field.street{min-width:260px;flex:2}
          label{display:block;margin:0 0 6px;font-weight:bold}
          input{width:100%;padding:12px;border:2px solid #111;border-radius:10px;box-sizing:border-box}
          .btn{display:inline-block;padding:14px 18px;border:2px solid #111;border-radius:12px;background:#111;color:#fff;text-decoration:none;font-weight:bold;white-space:nowrap}
        </style>
      </head>
      <body>

<style>
.home-icon{
    position:fixed;
    top:60px;
    right:22px;
    font-size:22px;
    text-decoration:none;
    color:#111;
    font-weight:600;
}
.home-icon:hover{
    text-decoration:underline;
}
</style>


<div style="background:#111;color:#fff;padding:10px;">

<a href="/documents?tab=dashboard" style="color:#fff;margin-right:20px;">Dashboard</a>
<a href="/participants" style="color:#fff;margin-right:20px;">Add / View Participants</a>
<a href="/notes" style="color:#fff;">Incident / Status Documentation</a>
</div>

        <div class="card">
          <h1>Activate Operational Framework</h1>
          <form method="post">
            <div class="activate-grid">
              <div class="field">
                <label>Business Name</label>
                <input name="business_name">
              </div>
              <div class="field">
                <label>Email</label>
                <input name="email" type="email">
              </div>
              <div class="field street">
                <label>Street</label>
                <input name="street">
              </div>
              <div class="field">
                <label>City</label>
                <input name="city">
              </div>
              <div class="field" style="max-width:110px;">
                <label>State</label>
                <input name="state">
              </div>
              <div class="field" style="max-width:130px;">
                <label>ZIP</label>
                <input name="zip">
              </div>
              <div class="field" style="flex:0 0 auto;min-width:auto;">
                <button class="btn" type="submit">Continue to Payment</button>
              </div>
            </div>
          </form>
        </div>
      </body>
    </html>
    """)

@app.route("/product", methods=["GET", "POST"])
def product():
    sku = (request.values.get("sku") or request.args.get("sku") or "FIRST_PROPERTY").strip()
    if sku not in PRODUCTS:
        abort(400, f"Unknown product sku: {sku}")

    session["product_sku"] = sku

    if sku == "ADDITIONAL_PROPERTY":
        return redirect("/add-property")

    return redirect(f"/buy?sku={sku}")


@app.route("/add-property", methods=["GET", "POST"])
def add_property():
    if request.method == "POST":
        session["product_sku"] = "ADDITIONAL_PROPERTY"
        session["pending_required_monthly_for"] = "ADDITIONAL_PROPERTY"
        session["licensed_location"] = {
            "business_name": (request.form.get("business_name") or "").strip(),
            "email": (request.form.get("email") or "").strip(),
            "street": (request.form.get("street") or "").strip(),
            "city": (request.form.get("city") or "").strip(),
            "state": (request.form.get("state") or "").strip().upper(),
            "zip": (request.form.get("zip") or "").strip(),
        }

        loc = session["licensed_location"]
        required = ["business_name", "email", "street", "city", "state", "zip"]
        if any(not loc.get(k) for k in required):
            abort(400, "All address fields are required.")

        return redirect("/buy?sku=ADDITIONAL_PROPERTY")

    return render_template_string("""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Add Another Property</title>
        <style>
          body{font-family:Arial,sans-serif;max-width:1200px;margin:40px auto;padding:0 16px;background:#f6f7fb}
          .card{background:#fff;border:2px solid #111;border-radius:16px;padding:24px}
          .activate-grid{display:flex;flex-wrap:wrap;gap:12px;align-items:end}
          .field{display:flex;flex-direction:column;min-width:260px;flex:1}
          .field.street{min-width:260px;flex:2}
          label{display:block;margin:0 0 6px;font-weight:bold}
          input{width:100%;padding:12px;border:2px solid #111;border-radius:10px;box-sizing:border-box}
          .btn{display:inline-block;padding:14px 18px;border:2px solid #111;border-radius:12px;background:#111;color:#fff;text-decoration:none;font-weight:bold;white-space:nowrap}
          .note{background:#f2f4f8;border-left:5px solid #111;padding:14px;border-radius:10px;margin:16px 0 22px;line-height:1.6}
        </style>
      </head>
      <body>
        <div class="card">
          <h1>Add Another Property</h1>
          <div class="note">
            Each added property requires two parts together: the Additional Property Access purchase and the active Monthly Plan.
            These are not sold separately. Both are required for the property to use the system.
          </div>

          <form method="POST" action="/add-property">
            <div class="activate-grid">
              <div class="field">
                <label>Business Name</label>
                <input name="business_name" required>
              </div>
              <div class="field">
                <label>Email</label>
                <input name="email" type="email" required>
              </div>
              <div class="field street">
                <label>Street</label>
                <input name="street" required>
              </div>
              <div class="field">
                <label>City</label>
                <input name="city" required>
              </div>
              <div class="field" style="max-width:110px;">
                <label>State</label>
                <input name="state" required>
              </div>
              <div class="field" style="max-width:130px;">
                <label>ZIP</label>
                <input name="zip" required>
              </div>
              <div class="field" style="flex:0 0 auto;min-width:auto;">
                <button class="btn" type="submit">Continue to Payment</button>
              </div>
            </div>
          </form>
        </div>
      </body>
    </html>
    """)

@app.route("/buy")
def buy():
    sku = (request.args.get("sku") or session.get("product_sku") or "FIRST_PROPERTY").strip()
    if sku not in PRODUCTS:
        abort(400, f"Unknown product sku: {sku}")

    session["product_sku"] = sku
    product = PRODUCTS.get(sku)
    if not product:
        abort(500, f"{sku} product is missing.")

    if not session.get("licensed_location"):
        return redirect("/activate")

    if sku in ("FIRST_PROPERTY", "ADDITIONAL_PROPERTY") and request.args.get("confirm") != "1":
        loc = session.get("licensed_location", {}) or {}
        return render_template_string("""
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Review Before Payment</title>
            <style>
              body{font-family:Arial,sans-serif;max-width:900px;margin:40px auto;padding:0 16px;background:#f6f7fb}
              .card{background:#fff;border:2px solid #111;border-radius:16px;padding:24px}
              .note{background:#f2f4f8;border-left:5px solid #111;padding:14px;border-radius:10px;margin:16px 0 22px;line-height:1.6}
              .mini{margin:10px 0;padding:12px;border:1px solid #ddd;border-radius:12px;background:#fafafa}
              .btn{display:inline-block;padding:14px 18px;border:2px solid #111;border-radius:12px;background:#111;color:#fff;text-decoration:none;font-weight:bold}
              .btn.alt{background:#fff;color:#111}
            </style>
          </head>
          <body>
            <div class="card">
              <h1>Review Before Payment</h1>

              <div class="note">
                <strong>Important:</strong> This system is sold as two required parts together:
                <br>1. One-time Property Access
                <br>2. Active Monthly Plan
                <br><br>
                Neither part is sold separately. Completing this payment is only the first part.
                The monthly plan is also required for full system access.
              </div>

              <div class="mini"><strong>Selected product:</strong> {{ product_label }}</div>
              <div class="mini"><strong>Price now:</strong> ${{ product_price }}</div>
              <div class="mini"><strong>Property:</strong> {{ street }}, {{ city }}, {{ state }} {{ zip }}</div>

              <div style="margin-top:20px;display:flex;gap:12px;flex-wrap:wrap;">
                <a class="btn" href="/buy?sku={{ sku }}&confirm=1">Continue to First Payment</a>
                <a class="btn alt" href="/">Go Back</a>
              </div>
            </div>
          </body>
        </html>
        """,
        sku=sku,
        product_label=product.get("label", sku),
        product_price=product.get("price", ""),
        street=loc.get("street", ""),
        city=loc.get("city", ""),
        state=loc.get("state", ""),
        zip=loc.get("zip", ""))
    access_token = get_paypal_access_token()

    from urllib.parse import urlencode
    loc = session.get("licensed_location", {}) or {}
    return_params = urlencode({
        "email": loc.get("email", ""),
        "business_name": loc.get("business_name", ""),
        "street": loc.get("street", ""),
        "city": loc.get("city", ""),
        "state": loc.get("state", ""),
        "zip": loc.get("zip", ""),
    })

    if product.get("kind") == "subscription":
        if sku == "PROPERTY_MONTHLY" and not session.get("pending_required_monthly_for"):
            return render_template_string("""
            <!doctype html>
            <html>
              <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>Monthly Subscription Required</title>
                <style>
                  body { font-family: Arial, sans-serif; background:#f7f7f7; padding:40px; }
                  .card { max-width:700px; margin:0 auto; background:#fff; border:1px solid #ddd; border-radius:16px; padding:28px; }
                  h1 { margin-top:0; }
                  .btn {
                    display:inline-block; margin-top:14px; padding:12px 18px; border-radius:12px;
                    background:#111; color:#fff; text-decoration:none; font-weight:700;
                  }
                  .note { color:#444; line-height:1.6; }
                </style>
              </head>
              <body>
                <div class="card">
                  <h1>Two required parts for activation</h1>
                  <p class="note">Activation includes two required parts: a one-time Property Access purchase and an active Monthly Plan. Neither is sold separately. Both are required for access.</p>
                  <a class="btn" href="/activate">Start Activation</a>
                </div>
              </body>
            </html>
            """)

        if not product.get("plan_id"):
            abort(500, f"Missing PayPal plan_id for {sku}.")

        return_url = request.host_url.rstrip("/") + "/subscribe-success"
        if return_params:
            return_url = return_url + "?" + return_params
        cancel_url = request.host_url.rstrip("/") + "/cancel"

        sub_data = {
            "plan_id": product["plan_id"],
            "custom_id": sku,
            "application_context": {
                "brand_name": "NILPF",
                "user_action": "SUBSCRIBE_NOW",
                "return_url": return_url,
                "cancel_url": cancel_url,
            }
        }

        resp = requests.post(
            f"{PAYPAL_BASE}/v1/billing/subscriptions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            json=sub_data,
            timeout=30,
        )
        resp.raise_for_status()
        sub = resp.json()

        for link in sub.get("links", []):
            if link.get("rel") == "approve":
                return redirect(link["href"])

        abort(500, "No PayPal subscription approval link found")

    return_url = request.host_url.rstrip("/") + "/success"
    if return_params:
        return_url = return_url + "?" + return_params
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

    location = session.get("licensed_location") or {
        "email": request.args.get("email", ""),
        "business_name": request.args.get("business_name", ""),
        "street": request.args.get("street", ""),
        "city": request.args.get("city", ""),
        "state": request.args.get("state", ""),
        "zip": request.args.get("zip", ""),
    }
    if not location.get("street") or not location.get("city") or not location.get("state"):
        abort(400, "Missing licensed location in session and return data.")

    full_address = f"{location['street']}, {location['city']}, {location['state']} {location.get('zip','')}".strip()

    purchase_unit = (capture_data.get("purchase_units") or [{}])[0]
    custom_id = purchase_unit.get("custom_id") or session.get("product_sku") or "FIRST_PROPERTY"
    payments = purchase_unit.get("payments") or {}
    captures = payments.get("captures") or []
    capture = captures[0] if captures else {}

    capture_id = capture.get("id") or order_id
    amount_info = capture.get("amount") or {}
    price_paid = amount_info.get("value") or ""

    upsert_license(
        order_id,
        location.get("email"),
        location.get("business_name"),
        full_address,
        location.get("state"),
        custom_id,
        capture_id,
        price_paid,
    )

    session["licensed_session_id"] = order_id
    session["licensed_location"] = {
        "email": location.get("email", ""),
        "business_name": location.get("business_name", ""),
        "street": location.get("street", ""),
        "city": location.get("city", ""),
        "state": location.get("state", ""),
        "zip": location.get("zip", ""),
    }
    session["product_sku"] = custom_id

    if custom_id in ("FIRST_PROPERTY", "ADDITIONAL_PROPERTY"):
        session["pending_required_monthly_for"] = custom_id
        return redirect("/buy?sku=PROPERTY_MONTHLY")

    return redirect(f"/documents?session_id={order_id}")

@app.route("/subscribe-success")
def subscribe_success():
    subscription_id = request.args.get("subscription_id") or request.args.get("token")
    if not subscription_id:
        abort(400, "Missing PayPal subscription id.")

    sku = session.get("product_sku") or "PROPERTY_MONTHLY"
    product = PRODUCTS.get(sku) or PRODUCTS.get("PROPERTY_MONTHLY") or {}

    location = session.get("licensed_location") or {
        "email": request.args.get("email", ""),
        "business_name": request.args.get("business_name", ""),
        "street": request.args.get("street", ""),
        "city": request.args.get("city", ""),
        "state": request.args.get("state", ""),
        "zip": request.args.get("zip", ""),
    }
    if not location.get("street") or not location.get("city") or not location.get("state"):
        abort(400, "Missing licensed location in session and return data.")

    full_address = f"{location['street']}, {location['city']}, {location['state']} {location.get('zip','')}".strip()

    upsert_license(
        subscription_id,
        location.get("email"),
        location.get("business_name"),
        full_address,
        location.get("state"),
        sku,
        subscription_id,
        product.get("price", ""),
    )

    parent_sku = session.pop("pending_required_monthly_for", None)

    session["licensed_session_id"] = subscription_id
    session["licensed_location"] = {
        "email": location.get("email", ""),
        "business_name": location.get("business_name", ""),
        "street": location.get("street", ""),
        "city": location.get("city", ""),
        "state": location.get("state", ""),
        "zip": location.get("zip", ""),
    }
    session["product_sku"] = parent_sku or sku
    return redirect(f"/documents?session_id={subscription_id}")

@app.route("/cancel")
def cancel():
    return redirect("/activate")

@app.route("/documents")
def documents():
    # SaaS access gate
    session_id = session.get("licensed_session_id") or request.args.get("session_id") or session.get("licensed_session_id") or request.args.get("session_id")
    if not session_id:
        return redirect("/")

    session["licensed_session_id"] = session_id

    lic = get_license_by_session(session_id)
    if not lic:
        abort(404, "License not found.")

    session["licensed_session_id"] = session_id

    payer_email, payer_name, prop_addr, prop_state, license_key, created_at, product_sku = lic
    active_tab = request.args.get("tab", "dashboard")

    return render_template_string("""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>NILPF Operational Framework</title>
        <style>
          * { box-sizing: border-box; }
          body {
            margin: 0;
            font-family: Arial, sans-serif;
            background: #f4f6f8;
            color: #111;
          }
          .topbar {
            background: #111;
            color: #fff;
            padding: 18px 20px;
          }
          .topbar h1 {
            margin: 0;
            font-size: 24px;
          }
          .topbar p {
            margin: 6px 0 0;
            font-size: 14px;
            color: #ddd;
          }
          .layout {
            display: grid;
            grid-template-columns: 1fr;
            min-height: calc(100vh - 82px);
          }
          .sidebar {
            background: #fff;
            border-right: 1px solid #ddd;
            padding: 18px 14px;
          }
          .tablink {
            display: block;
            width: 100%;
            text-decoration: none;
            color: #111;
            background: #fff;
            border: 2px solid #111;
            border-radius: 12px;
            padding: 12px 14px;
            margin: 0 0 12px 0;
            font-weight: 700;
          }
          .tablink.active {
            background: #111;
            color: #fff;
          }
          .main {
            padding: 22px;
          }
          .card {
            background: #fff;
            border: 2px solid #111;
            border-radius: 18px;
            padding: 18px;
            margin-bottom: 18px;
          }
          .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 14px;
          }
          .mini {
            background: #fafafa;
            border: 1px solid #ddd;
            border-radius: 14px;
            padding: 14px;
          }
          .mini h3 {
            margin: 0 0 8px 0;
            font-size: 16px;
          }
          .viewer {
            width: 100%;
            height: 78vh;
            border: 1px solid #ccc;
            border-radius: 14px;
            background: #fff;
          }
          .note {
            font-size: 14px;
            color: #444;
          }
          .btnrow a {
            display: inline-block;
            text-decoration: none;
            color: #111;
            border: 2px solid #111;
            border-radius: 12px;
            padding: 10px 14px;
            margin: 8px 10px 0 0;
            font-weight: 700;
            background: #fff;
          }
          @media (max-width: 900px) {
            .layout { grid-template-columns: 1fr; }
            .sidebar { border-right: 0; border-bottom: 1px solid #ddd; }
          }
        </style>
      </head>
      <body>

<style>
.home-icon{
    position:fixed;
    top:60px;
    right:22px;
    font-size:22px;
    text-decoration:none;
    color:#111;
    font-weight:600;
}
.home-icon:hover{
    text-decoration:underline;
}
</style>


<div style="background:#111;color:#fff;padding:10px;">

<a href="/documents?tab=dashboard" style="color:#fff;margin-right:20px;">Dashboard</a>
<a href="/participants" style="color:#fff;margin-right:20px;">Add / View Participants</a>
<a href="/notes" style="color:#fff;">Incident / Status Documentation</a>
</div>

      <div class="box">
        <div class="topbar">
          <h1>Operational Framework</h1>
          <p>{{ prop_addr }} · License Key {{ license_key }}</p>


        </div>

        <div style="padding:18px 20px 0 20px;">
  <a href="/form-builder" style="display:inline-block;padding:10px 16px;background:#111;color:#fff;text-decoration:none;border-radius:10px;font-weight:bold;">⬅ Return to Form Builder</a>
</div>
<div class="layout">
          <main class="main">
            {% if active_tab == 'dashboard' %}
              <div class="card">
                <h2>Dashboard</h2>
                <div class="grid">
                  <div class="mini">
                    <h3>Licensed Address</h3>
                    <div>{{ prop_addr }}</div>
                  </div>
                  <div class="mini">
                    <h3>License Key</h3>
                    <div>{{ license_key }}</div>
                  </div>
                  <div class="mini">
                    <h3>Buyer</h3>
                    <div>{{ payer_name or 'N/A' }}</div>
                    <div style="margin-top:10px;">
                      <a class="btn" href="/product?sku=ADDITIONAL_PROPERTY">Add Another Property</a>
                    </div>
                  </div>
                  <div class="mini">
                    <h3>Email</h3>
                    <div>{{ payer_email or 'N/A' }}</div>
                  </div>
                </div>
                <p class="note">This is now the NILPF web app workspace. Use the tabs on the left to open the correct section inside the app.</p>
              </div>
            {% elif active_tab == 'participants' %}
              <div class="card">
                <h2>Add / View Participants</h2>
                <p class="note">Create and manage participant ENTRY records here. This area controls ENTRY SCREENING, participant notes, and completion tracking.</p>
                <div class="btnrow" style="margin-bottom:12px;gap:8px;flex-wrap:wrap;">
                  
                </div>
              </div>

              <div class="card">
                <h3>Participant Forms</h3>
                <p class="note">Open each section below to work through participant ENTRY and ongoing documentation without leaving the workspace.</p>

                <details style="margin-top:12px;">
                  <summary style="cursor:pointer; font-weight:700;">ENTRY</summary>
                  <div style="padding:10px 0 0 14px;">
                    <p><a class="btn" href="/static/documents/18_Entry_Screening_v2.2.pdf" target="participant_viewer">ENTRY SCREENING</a></p>
                  </div>
                </details>

                <details style="margin-top:12px;">
                  <summary style="cursor:pointer; font-weight:700;">RIGHTS AND RESPONSIBILITIES</summary>
                  <div style="padding:10px 0 0 14px;">
                    <p><a class="btn" href="/static/documents/Member_Bill_of_Dignity_Independence_v2.1.pdf" target="participant_viewer">Member Bill of Rights</a></p>
                    <p><a class="btn" href="/static/documents/16_Participant_Financial_Responsibility_Agreement.pdf" target="participant_viewer">Participant Financial Responsibility Agreement</a></p>
                    <p><a class="btn" href="/static/documents/15_IMPORTANT_NOTICE_AND_DISCLAIMER_v2.1.pdf" target="participant_viewer">Important Notice & Disclaimer</a></p>
                    <p><a class="btn" href="/static/documents/13-Communication_and_Consent_Form.pdf" target="participant_viewer">Communication and Consent</a></p>
                  </div>
                </details>

                <details style="margin-top:12px;">
                  <summary style="cursor:pointer; font-weight:700;">MOVEMENT AND PROPERTY</summary>
                  <div style="padding:10px 0 0 14px;">
                    <p><a class="btn" href="/static/documents/12-Transfer_Form.pdf" target="participant_viewer">Transfer Form</a></p>
                    <p><a class="btn" href="/static/documents/11-Vehicle_Parking_Information_Form.pdf" target="participant_viewer">Vehicle Form</a></p>
                    <p><a class="btn" href="/static/documents/10-Pet_Animal_Information_Sheet.pdf" target="participant_viewer">Pet Form</a></p>
                  </div>
                </details>

                <details style="margin-top:12px;">
                  <summary style="cursor:pointer; font-weight:700;">NOTES</summary>
                  <div style="padding:10px 0 0 14px;">
                    <p><a class="btn" href="/participants">Notes and Irregularities</a></p>
                  </div>
                </details>

                <iframe class="viewer" name="participant_viewer" src="/static/documents/18_Entry_Screening_v2.2.pdf" style="margin-top:16px;"></iframe>
              </div>
                        {% elif active_tab == 'operational' %}
              <div class="card">
                <h2>Operational Framework</h2>
                <p class="note">Open a form directly in the browser. This layout is organized for first-time users.</p>

                {% for group_name, items in framework_groups.items() %}
                  <div style="margin-top:20px;">
                    <h3>{{ group_name }}</h3>
                    <ul style="line-height:1.9;">
                      {% for label, filename in items %}
                        <li><a href="/static/documents/{{ filename }}" >{{ label }}</a></li>
                      {% endfor %}
                    </ul>
                  </div>
                {% endfor %}
              </div>
            
              <div class="card">
                <h2>Master Lease</h2>
                <p class="note">Master Lease opens inside the workspace below.</p>
                <iframe class="viewer" src="/static/documents/Master_Lease_v2.1.pdf"></iframe>
              </div>
            {% endif %}
          </main>
        </div>
      





<!-- Dignity Idle Screen -->
<div id="dignityScreen" onclick="hideDignityScreen()" style="
display:none;
position:fixed;
inset:0;
background:rgba(17,17,17,.96);
color:#fff;
z-index:99999;
align-items:center;
justify-content:center;
text-align:center;
padding:30px;
font-family:Arial,sans-serif;
">
  <div>
    <div style="font-size:34px;font-weight:700;margin-bottom:14px;">Dignity & Privacy Protected</div>
    <div style="font-size:18px;max-width:700px;line-height:1.5;">
      Participant information has been hidden due to inactivity.
      Tap or click anywhere to continue.
    </div>
  </div>
</div>

<style>
.home-icon{
    position:fixed;
    top:60px;
    right:22px;
    font-size:22px;
    text-decoration:none;
    color:#111;
    font-weight:600;
}
.home-icon:hover{
    text-decoration:underline;
}
</style>

<a href="/home" class="home-icon">🏠</a>

<script>
let idleTimer;
let logoutTimer;
const idleTimeLimit = 60000;
const logoutTimeLimit = 300000;

function resetIdleTimer() {
    clearTimeout(idleTimer);
    clearTimeout(logoutTimer);
    idleTimer = setTimeout(showDignityScreen, idleTimeLimit);
    logoutTimer = setTimeout(() => { window.location = "/logout"; }, logoutTimeLimit);
}

function showDignityScreen() {
    const el = document.getElementById("dignityScreen");
    if (el) el.style.display = "flex";
    fetch('/log-dignity-screen', {method:'POST'});
}

function hideDignityScreen() {
    const el = document.getElementById("dignityScreen");
    if (el) el.style.display = "none";
    resetIdleTimer();
}

window.addEventListener("load", resetIdleTimer);
document.addEventListener("mousemove", resetIdleTimer);
document.addEventListener("keypress", resetIdleTimer);
document.addEventListener("click", resetIdleTimer);
document.addEventListener("touchstart", resetIdleTimer);
</script>

      </body>
    </html>
    """,
    active_tab=active_tab,
    prop_addr=prop_addr,
    license_key=license_key,
    payer_name=payer_name,
    payer_email=payer_email,
    framework_groups=globals().get("FRAMEWORK_GROUPS", globals().get("framework_groups", {}))
    )




@app.route("/participants", methods=["GET", "POST"])
def participants():
    ensure_participants_table()
    ensure_notes_table()
    ensure_participant_forms_table()

    import sqlite3
    from datetime import datetime

    message = ""

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        dob = (request.form.get("dob") or "").strip()
        move_in_date = (request.form.get("move_in_date") or datetime.utcnow().strftime("%Y-%m-%d") or "").strip()
        room_unit = (request.form.get("room_unit") or "").strip()
        gender = (request.form.get("gender") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        email = (request.form.get("email") or "").strip()
        address = (request.form.get("address") or "").strip()
        city = (request.form.get("city") or "").strip()
        state = (request.form.get("state") or "").strip()
        zip_code = (request.form.get("zip_code") or "").strip()
        emergency_contact_name = (request.form.get("emergency_contact_name") or "").strip()
        emergency_contact_phone = (request.form.get("emergency_contact_phone") or "").strip()

        if full_name:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()

            cols = [r[1] for r in cur.execute("PRAGMA table_info(participants)").fetchall()]

            data = {}
            if "full_name" in cols:
                data["full_name"] = full_name
            elif "participant_name" in cols:
                data["participant_name"] = full_name
            elif "legal_name" in cols:
                data["legal_name"] = full_name

            if "preferred_name" in cols:
                data["preferred_name"] = full_name

            if "dob" in cols:
                data["dob"] = dob
            if "move_in_date" in cols:
                data["move_in_date"] = move_in_date
            if "room_unit" in cols:
                data["room_unit"] = room_unit
            elif "room" in cols:
                data["room"] = room_unit
            elif "unit" in cols:
                data["unit"] = room_unit
            if "gender" in cols:
                data["gender"] = gender
            if "phone" in cols:
                data["phone"] = phone
            if "email" in cols:
                data["email"] = email
            if "address" in cols:
                data["address"] = address
            if "city" in cols:
                data["city"] = city
            if "state" in cols:
                data["state"] = state
            if "zip_code" in cols:
                data["zip_code"] = zip_code
            if "emergency_contact_name" in cols:
                data["emergency_contact_name"] = emergency_contact_name
            if "emergency_contact_phone" in cols:
                data["emergency_contact_phone"] = emergency_contact_phone
            if "created_at" in cols:
                data["created_at"] = datetime.utcnow().isoformat()

            if data:
                fields = ", ".join(data.keys())
                placeholders = ", ".join(["?"] * len(data))
                cur.execute(f"INSERT INTO participants ({fields}) VALUES ({placeholders})", tuple(data.values()))
                participant_id = cur.lastrowid
                conn.commit()
                seed_participant_forms(participant_id)
                message = "Participant added."
            else:
                message = "Participants table exists, but no matching columns were found."

            conn.close()
        else:
            message = "Full name is required."

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cols = [r[1] for r in cur.execute("PRAGMA table_info(participants)").fetchall()]
    select_cols = []
    for c in ["id", "legal_name", "preferred_name", "full_name", "participant_name", "dob", "gender", "phone", "email", "address", "city", "state", "zip_code", "emergency_contact_name", "emergency_contact_phone", "move_in_date", "room_unit", "room", "unit", "created_at"]:
        if c in cols and c not in select_cols:
            select_cols.append(c)

    rows = []
    if select_cols:
        q = "SELECT " + ", ".join(select_cols) + " FROM participants ORDER BY id DESC" if "id" in select_cols else "SELECT " + ", ".join(select_cols) + " FROM participants"
        rows = cur.execute(q).fetchall()

    alerts_by_pid = {}
    notes_by_pid = {}
    if "id" in select_cols and rows:
        pid_index = select_cols.index("id")
        for row in rows:
            try:
                pid = row[pid_index]
                total_forms = cur.execute(
                    "SELECT COUNT(*) FROM participant_forms WHERE participant_id = ?",
                    (str(pid),)
                ).fetchone()[0]
                incomplete_forms = cur.execute(
                    "SELECT COUNT(*) FROM participant_forms WHERE participant_id = ? AND COALESCE(is_complete, 0) = 0",
                    (str(pid),)
                ).fetchone()[0]
                note_count = cur.execute(
                    "SELECT COUNT(*) FROM participant_notes WHERE TRIM(COALESCE(participant_id, '')) = TRIM(?)",
                    (str(pid),)
                ).fetchone()[0]
                alerts_by_pid[str(pid)] = {
                    "total": total_forms,
                    "incomplete": incomplete_forms
                }
                notes_by_pid[str(pid)] = note_count
            except:
                alerts_by_pid[str(row[pid_index])] = {
                    "total": 0,
                    "incomplete": 0
                }
                notes_by_pid[str(row[pid_index])] = 0

    conn.close()

    return render_template_string("""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Add / View Participants</title>
      <style>
        body { font-family: Arial, sans-serif; max-width: 1100px; margin: 30px auto; padding: 0 16px; background: #f6f6f6; }
        .card { background: white; border: 2px solid #111; border-radius: 18px; padding: 18px; margin-bottom: 18px; }
        h1,h2 { margin-top: 0; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
        input { width: 100%; padding: 10px; border: 2px solid #111; border-radius: 10px; box-sizing: border-box; }
        button, .btn { display: inline-block; padding: 10px 14px; border: 2px solid #111; border-radius: 12px; background: #fff; text-decoration: none; color: #111; font-weight: 700; cursor: pointer; }
        table { width: 100%; border-collapse: collapse; }
        th, td { border-bottom: 1px solid #ddd; padding: 10px; text-align: left; vertical-align: top; }
        .note { color: #444; }
        .msg { font-weight: 700; margin-bottom: 12px; }
      </style>
    </head>
    <body>

<style>
.home-icon{
    position:fixed;
    top:60px;
    right:22px;
    font-size:22px;
    text-decoration:none;
    color:#111;
    font-weight:600;
}
.home-icon:hover{
    text-decoration:underline;
}
</style>


<div style="background:#111;color:#fff;padding:10px;">

<a href="/documents?tab=dashboard" style="color:#fff;margin-right:20px;">Dashboard</a>
<a href="/participants" style="color:#fff;margin-right:20px;">Add / View Participants</a>
<a href="/notes" style="color:#fff;">Incident / Status Documentation</a>
</div>

      <div class="card">
        <h1>Add / View Participants</h1>
        <p class="note">Create and view participant entry records here.</p>
        <p><a class="btn" href="javascript:history.back()">Go Back</a></p>
      </div>

      <div class="card">
        <h2>Add Participant</h2>
        {% if message %}<p class="msg">{{ message }}</p>{% endif %}
        <form method="post">
          <div class="grid">
            <div>
              <label>Full Name</label>
              <input type="text" name="full_name" required>
            </div>
            <div>
              <label>Date of Birth</label>
              <input type="date" name="dob" value="">
            </div>
            <div>
              <label>Gender</label>
              <input type="text" name="gender">
            </div>
            <div>
              <label>Phone</label>
              <input type="text" name="phone">
            </div>
            <div>
              <label>Email</label>
              <input type="text" name="email">
            </div>
            <div>
              <label>Address</label>
              <input type="text" name="address">
            </div>
            <div>
              <label>City</label>
              <input type="text" name="city">
            </div>
            <div>
              <label>State</label>
              <input type="text" name="state">
            </div>
            <div>
              <label>Zip Code</label>
              <input type="text" name="zip_code">
            </div>
            <div>
              <label>Emergency Contact Name</label>
              <input type="text" name="emergency_contact_name">
            </div>
            <div>
              <label>Emergency Contact Phone</label>
              <input type="text" name="emergency_contact_phone">
            </div>
            <div>
              <label>Move In Date</label>
              <input type="date" name="move_in_date" value="">
            </div>
            <div>
              <label>Room / Unit</label>
              <input type="text" name="room_unit">
            </div>
          </div>
          <p style="margin-top:14px;"><button type="submit">Save Participant</button></p>
        </form>
      </div>

      <div class="card">
        <h2>Current Participants</h2>
        {% if rows and select_cols %}
          <table>
            <thead>
              <tr>
                {% for c in select_cols %}
                  <th>{{ c }}</th>
                {% endfor %}
                <th>Workflow Alert</th>
              </tr>
            </thead>
            <tbody>
              {% for row in rows %}
                <tr>
                  <td>
                    <a href="/participant/{{ row[0] }}">
                      {{ row[1] if row|length > 1 else row[0] }}
                    </a>
                    {% set note_count = notes_by_pid.get(row[0]|string, 0) %}
                    {% if note_count > 0 %}
                      <a href="/notes?participant_id={{ row[0] }}" title="View notes" style="text-decoration:none;margin-left:8px;font-size:18px;">📝</a>
                    {% endif %}
                  </td>
                  {% for item in row[2:] %}
                  <td>{{ item }}</td>
                  {% endfor %}
                  <td>
                    {% set alert = alerts_by_pid.get(row[0]|string, {"total": 0, "incomplete": 0}) %}
                    {% if alert.total == 0 %}
                      <span class="note">No workflow forms</span>
                    {% elif alert.incomplete == 0 %}
                      <strong style="color:#2e8b57;">Complete</strong>
                    {% else %}
                      <strong style="color:#b22222;">{{ alert.incomplete }} incomplete</strong>
                      <div class="note">{{ alert.total - alert.incomplete }} of {{ alert.total }} done</div>
                    {% endif %}
                  </td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <p class="note">No participants yet.</p>
        {% endif %}
      </div>
    </body>
    </html>
    """, rows=rows, select_cols=select_cols, message=message, alerts_by_pid=alerts_by_pid, notes_by_pid=notes_by_pid)




@app.route("/participant/<int:pid>", methods=["GET", "POST"])
def participant_detail(pid):
    return redirect(f"/participant-workflow/{pid}")


@app.route("/documents/master-lease.pdf")
def stamped_master_lease():
    session_id="LOCAL_TEST"
    return redirect(f"/documents?session_id={session_id}&tab=ml")


# -------------------------
# Electronic Documents (ML / MLA)
# -------------------------



@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# -------------------------
# NOTES PAGE
# -------------------------
@app.route("/notes", methods=["GET", "POST"])
def notes():
    session_id = session.get("licensed_session_id") or request.args.get("session_id") or session.get("licensed_session_id") or request.args.get("session_id")
    if not session_id:
        return redirect("/")

    session["licensed_session_id"] = session_id

    lic = get_license_by_session(session_id)
    if not lic:
        session.clear()
        return redirect("/")

    import sqlite3
    from datetime import datetime

    incident_types = [
        "General Status",
        "Behavioral Concern",
        "Mental Status Observation",
        "Fighting / Aggression",
        "Verbal Conflict",
        "Intoxication / Suspected Alcohol",
        "Fall / Found Down",
        "Missing / Elopement",
        "Noncompliance with Program Rules",
        "Property Damage",
        "Visitor Issue",
        "Other"
    ]

    conn = sqlite3.connect("licenses.db")
    cur = conn.cursor()

    participants = cur.execute("""
        SELECT id, legal_name, preferred_name
        FROM participants
        ORDER BY COALESCE(NULLIF(TRIM(preferred_name), ''), TRIM(legal_name)) COLLATE NOCASE
    """).fetchall()

    selected_pid = (request.args.get("participant_id") or request.form.get("participant_id") or "").strip()

    if request.method == "POST":
        participant_id = (request.form.get("participant_id") or "").strip()
        staff_name = (request.form.get("staff_name") or "").strip()
        incident_type = (request.form.get("incident_type") or "").strip()
        note_text = (request.form.get("note_text") or "").strip()

        participant_name = ""
        if participant_id:
            row = cur.execute("""
                SELECT id, legal_name, preferred_name
                FROM participants
                WHERE id = ?
            """, (participant_id,)).fetchone()
            if row:
                pid, legal_name, preferred_name = row
                participant_name = (preferred_name or "").strip() or (legal_name or "").strip() or f"Participant {pid}"

        if participant_id and participant_name and staff_name and incident_type and note_text:
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("""
                INSERT INTO participant_notes
                (participant_name, staff_name, note_text, created_at, participant_id, incident_type)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (participant_name, staff_name, note_text, created_at, participant_id, incident_type))
            conn.commit()
            selected_pid = participant_id

    if selected_pid:
        rows = cur.execute("""
            SELECT participant_name, staff_name, incident_type, note_text, created_at, participant_id
            FROM participant_notes
            WHERE TRIM(COALESCE(participant_id, '')) = TRIM(?)
            ORDER BY id DESC
        """, (selected_pid,)).fetchall()
    else:
        rows = cur.execute("""
            SELECT participant_name, staff_name, incident_type, note_text, created_at, participant_id
            FROM participant_notes
            ORDER BY id DESC
        """).fetchall()

    conn.close()

    options_html = ""
    selected_name = ""
    for pid, legal_name, preferred_name in participants:
        display_name = (preferred_name or "").strip() or (legal_name or "").strip() or f"Participant {pid}"
        selected_attr = ' selected' if str(pid) == str(selected_pid) else ''
        if str(pid) == str(selected_pid):
            selected_name = display_name
        options_html += f'<option value="{pid}"{selected_attr}>{display_name} (ID {pid})</option>'

    incident_options_html = ""
    for item in incident_types:
        incident_options_html += f"<option value=\"{item}\">{item}</option>"

    rows_html = ""
    for participant_name, staff_name, incident_type, note_text, created_at, participant_id in rows:
        rows_html += f"""
        <div class="entry">
          <div class="entry-head">
            <strong>{participant_name}</strong>
            <span class="pill">{incident_type}</span>
            <span class="pill">ID {participant_id or "-"}</span>
          </div>
          <div class="meta">Staff: {staff_name} | {created_at}</div>
          <div class="body">{note_text}</div>
        </div>
        """

    participant_context = f"Participant Focus: {selected_name} (ID {selected_pid})" if selected_pid and selected_name else "All participants"

    back_link_html = f'<a href="/participant-workflow/{selected_pid}">Back to Participant Workflow</a>' if selected_pid else ""

    return f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Incident / Status Documentation</title>
        <style>
          body {{
            font-family: Arial, sans-serif;
            margin: 0;
            background: #f6f7fb;
            color: #111;
          }}
          .topbar {{
            background: #111;
            color: #fff;
            padding: 10px 16px;
          }}
          .topbar a {{
            color: #fff;
            margin-right: 20px;
            text-decoration: none;
            font-weight: 700;
          }}
          .wrap {{
            max-width: 1050px;
            margin: 0 auto;
            padding: 24px;
          }}
          .card {{
            background: #fff;
            border: 2px solid #111;
            border-radius: 18px;
            padding: 20px;
            margin-bottom: 18px;
          }}
          h1 {{
            margin: 0 0 8px 0;
            font-size: 30px;
          }}
          .sub {{
            color: #444;
            margin-bottom: 18px;
          }}
          .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 14px;
          }}
          label {{
            display: block;
            font-weight: 700;
            margin-bottom: 6px;
          }}
          select, input, textarea {{
            width: 100%;
            padding: 12px;
            border: 2px solid #111;
            border-radius: 12px;
            font-size: 15px;
            background: #fff;
          }}
          textarea {{
            min-height: 150px;
            resize: vertical;
          }}
          .btn {{
            display: inline-block;
            text-decoration: none;
            border: 2px solid #111;
            background: #111;
            color: #fff;
            padding: 12px 16px;
            border-radius: 999px;
            font-weight: 700;
            cursor: pointer;
          }}
          .entry {{
            border: 2px solid #111;
            border-radius: 16px;
            padding: 16px;
            margin-bottom: 14px;
            background: #fff;
          }}
          .entry-head {{
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
            margin-bottom: 6px;
          }}
          .pill {{
            display: inline-block;
            border: 2px solid #111;
            border-radius: 999px;
            padding: 4px 10px;
            font-size: 12px;
            font-weight: 700;
            background: #f3f3f3;
          }}
          .meta {{
            color: #444;
            font-size: 14px;
            margin-bottom: 10px;
          }}
          .body {{
            white-space: pre-wrap;
            line-height: 1.45;
          }}
        </style>
      </head>
      <body>
        <div class="topbar">
          <a href="/home">Home</a>
          <a href="/participants">Add / View Participants</a>
          <a href="/documents?tab=dashboard">Dashboard</a>
          {back_link_html}
        </div>

        <div class="wrap">
          <div class="card">
            <h1>Incident / Status Documentation</h1>
            <div class="sub">Document observations noticed during normal routine house checks and participant-related events.</div>
            <div class="sub"><strong>{participant_context}</strong></div>

            <form method="post">
              <div class="grid">
                <div>
                  <label>Participant</label>
                  <select name="participant_id" required>
                    <option value="">Select participant</option>
                    {options_html}
                  </select>
                </div>
                <div>
                  <label>Incident Type</label>
                  <select name="incident_type" required>
                    <option value="">Select incident type</option>
                    {incident_options_html}
                  </select>
                </div>
                <div>
                  <label>Staff / Composer</label>
                  <input name="staff_name" required>
                </div>
              </div>

              <div style="margin-top:14px;">
                <label>Incident / Status Details</label>
                <textarea name="note_text" required></textarea>
              </div>

              <div style="margin-top:14px;">
                <button class="btn" type="submit">Save Incident Note</button>
              </div>
            </form>
          </div>

          <div class="card">
            <h2 style="margin-top:0;">Timeline</h2>
            {rows_html if rows_html else "<div class='sub'>No incident or status notes saved yet.</div>"}
          </div>
        </div>
      </body>
    </html>
    """



# -------------------------
# Universal Form Loader
# -------------------------
@app.route("/form/<form_name>")
def open_form(form_name):
    pid = request.args.get("pid")

    return f"""
    <html>
    <body style="font-family:Arial;padding:40px">
    <h2>Form: {form_name}</h2>
    <p>Participant ID: {pid}</p>
    <p>This confirms the form route is working.</p>
    </body>
    </html>
    """



def seed_forms_for_participant(pid):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    forms = cur.execute("""
        SELECT form_name
        FROM forms_master
        ORDER BY display_order
    """).fetchall()

    for f in forms:
        name = f[0]

        exists = cur.execute("""
            SELECT 1
            FROM participant_forms
            WHERE participant_id=? AND form_name=?
        """, (pid, name)).fetchone()

        if not exists:
            cur.execute("""
                INSERT INTO participant_forms
                (participant_id, form_name, is_complete)
                VALUES (?, ?, 0)
            """, (pid, name))

    conn.commit()
    conn.close()


@app.route("/form-builder", methods=["GET", "POST"])
def form_builder():
    import json
    import re
    from pathlib import Path

    session_id = session.get("licensed_session_id") or request.args.get("session_id") or "public_builder"

    image_dir = Path("static/uploads/images")
    doc_dir = Path("static/uploads/documents")

    image_dir.mkdir(parents=True, exist_ok=True)
    doc_dir.mkdir(parents=True, exist_ok=True)

    layout_dir = Path("form_builder_layouts")
    layout_dir.mkdir(parents=True, exist_ok=True)

    if request.method == "POST":
        return redirect("/documents?tab=builder")

    current_doc_value = request.args.get("pdf", "").strip()
    current_source = ""
    current_image = ""
    if current_doc_value:
        if "|" in current_doc_value:
            current_source, current_image = current_doc_value.split("|", 1)
            current_source = Path(current_source).name.strip()
            current_image = Path(current_image).name.strip()
            current_doc_value = f"{current_source}|{current_image}"
        else:
            current_image = Path(current_doc_value).name
            current_doc_value = current_image

    source_pdf_url = get_source_pdf_url(current_doc_value) if current_doc_value else ""
    current_path = None
    current_image_url = source_pdf_url
    current_ext = Path(current_image).suffix.lower() if current_image else ""
    current_is_image = False
    current_is_pdf = current_ext == ".pdf"

    initial_fields = []
    if current_image:
        layout_path = resolve_layout_path(layout_dir, current_image)
        if layout_path.exists():
            try:
                initial_fields = json.loads(layout_path.read_text())
            except Exception:
                initial_fields = []

    form_options = []
    seen = set()

    source_groups = [
        ("Essential Forms (EF v2.2)", Path("EF_v2.2")),
        ("Core Docs (Core v2.1)", Path("Core-v2.1")),
    ]

    def pretty_label(filename: str) -> str:
        import re as _re
        name = Path(filename).stem
        name = _re.sub(r"[\ud800-\udfff]", "", name)
        name = "".join(ch for ch in name if ch.isprintable())
        name = name.replace("_", " ").replace("-", " ")
        name = _re.sub(r"^\d+[ ._-]*", "", name)
        name = _re.sub(r"\bv\d+(?:\.\d+)?\b", "", name, flags=_re.I)
        name = _re.sub(r"\s+", " ", name).strip()
        return name or "Unnamed Form"

    for group_label, folder in source_groups:
        items = []
        if folder.exists():
            for fp in sorted(folder.glob("*.pdf")):
                fname = fp.name
                if fname in seen:
                    continue
                seen.add(fname)
                items.append({
                    "value": f"{folder.name}|{fname}",
                    "label": pretty_label(fname),
                })
        if items:
            form_options.append({
                "group": group_label,
                "items": items,
            })

    return render_template_string("""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Custom Form App</title>
        <style>
          * { box-sizing: border-box; }
          body { margin: 0; font-family: Arial, sans-serif; background: #f4f6f8; color: #111; }
          .wrap { max-width: 1180px; margin: 0 auto; padding: 20px; }
          .card { background: #fff; border: 2px solid #111; border-radius: 18px; padding: 18px; margin-bottom: 16px; }
          .toolbar { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
          .toolbtn, .btn {
            display: inline-block;
            text-decoration: none;
            border: 2px solid #111;
            background: #fff;
            color: #111;
            padding: 10px 14px;
            border-radius: 12px;
            font-weight: 700;
            cursor: pointer;
          }
          .toolbtn.active, .btn.primary { background: #111; color: #fff; }
          .note { font-size: 14px; color: #444; margin: 8px 0 0 0; }
          .stage-wrap {
            background: #fff;
            border: 2px solid #111;
            border-radius: 18px;
            padding: 14px;
            overflow: auto;
          }
          .stage {
            position: relative;
            display: inline-block;
            max-width: 100%;
            border: 1px solid #ccc;
            background: #fafafa;
            min-height: 300px;
          }
          .stage img {
            display: block;
            max-width: 100%;
            height: auto;
          }
          .placeholder {
            width: 800px;
            max-width: 100%;
            min-height: 500px;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 30px;
            text-align: center;
            color: #666;
          }
          .pdf-pages {
            width: 100%;
            max-width: 1000px;
            margin: 0 auto;
          }
          .pdf-page-wrap {
            position: relative;
            width: fit-content;
            margin: 0 auto 22px auto;
            background: #fff;
            box-shadow: 0 2px 10px rgba(0,0,0,.08);
          }
          .pdf-page-wrap.active-tool {
            outline: 3px solid #111;
            outline-offset: 4px;
            cursor: crosshair;
          }
          .pdf-page-label {
            font-size: 13px;
            font-weight: 700;
            color: #333;
            margin: 0 0 6px 0;
          }
          .pdf-canvas {
            display: block;
            max-width: 100%;
            height: auto;
            background: #fff;
          }
          .marker {
            position: absolute;
            transform: translate(-50%, -50%);
            background: rgba(255,255,255,.92);
            border: 2px solid #111;
            border-radius: 10px;
            padding: 4px 8px;
            font-size: 13px;
            font-weight: 700;
            white-space: nowrap;
            cursor: pointer;
            z-index: 20;
          }
          .marker small {
            font-size: 11px;
            font-weight: 700;
            color: #444;
            margin-left: 6px;
          }
          .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
          input[type=file] { padding: 10px; border: 2px solid #111; border-radius: 12px; background: #fff; }
          .status { margin-top: 10px; font-size: 14px; font-weight: 700; }
        </style>
      </head>
      <body>
        <div class="wrap">
          <div class="card">
            <h1 style="margin:0 0 8px 0;">Custom Form App</h1>
            <p style="margin:0 0 10px 0;">Simple version: upload a page image, click a tool, then click the image to place the field.</p>

<div style="margin:12px 0;">
  <select id="doc-selector" style="width:100%;padding:10px;border:2px solid #111;border-radius:10px;">
    <option value="">Select Form</option>
    {% for section in form_options %}
      <optgroup label="{{ section.group }}">
        {% for item in section["items"] %}
          <option value="{{ item.value }}" {% if current_doc_value == item.value %}selected{% endif %}>{{ item.label }}</option>
        {% endfor %}
      </optgroup>
    {% endfor %}
  </select>
</div>

<script>
const d = document.getElementById("doc-selector");
if (d) {
  d.addEventListener("change", () => {
    if (d.value) {
      window.location = "/form-builder?pdf=" + encodeURIComponent(d.value);
    }
  });
}
</script>

            <form method="POST" enctype="multipart/form-data" class="row">
              <input type="file" name="form_image" accept=".png,.jpg,.jpeg,.webp,.pdf" disabled style="display:none;">
              <button class="btn primary" type="submit">Upload Page Image</button>
              <a class="btn" href="/documents?tab=dashboard">Back to Dashboard</a>
            </form>

            <div class="toolbar">
              <button type="button" class="toolbtn" data-tool="name">Text</button>
              <button type="button" class="toolbtn" data-tool="checkbox">Checkmark</button>
              <button type="button" class="toolbtn" data-tool="realcheckbox">Checkbox</button>
                            <button type="button" class="toolbtn" data-tool="date">Date</button>
              <button type="button" class="toolbtn" data-tool="signature">Signature</button>
              <button type="button" class="toolbtn" id="snapToggle">Snap: OFF</button>
              <button type="button" class="toolbtn" id="clearLast">Delete Last</button>
              <button type="button" class="btn primary" id="saveLayout">Save Layout</button>
<button type="button" class="btn" id="autoSuggest">Auto-Suggest Fields</button>
            </div>

            <p class="note">Images place directly on the builder. PDFs now render page-by-page inside the app so fields can save with the correct page number. Only images and PDFs are supported. PDFs render page-by-page for accurate field placement.</p>
            <div class="status" id="status"></div>
          </div>

          <div class="stage-wrap">
            <div class="stage" id="stage">
              {% if current_image_url and current_is_image %}
                <img id="docImage" src="{{ current_image_url }}" alt="Uploaded form page">
              {% elif current_image_url and current_is_pdf %}
                <div id="pdfPages" class="pdf-pages"></div>
              {% elif current_image_url %}
                <div class="placeholder" id="docImage">Unsupported file type. Please upload a PNG, JPG, WEBP, or PDF.</div>
              {% else %}
                <div class="placeholder" id="docImage">Upload a page image, PDF, or document first, then choose Checkbox, Date, or Signature.</div>
              {% endif %}
            </div>
          </div>
        </div>

        {% if current_is_pdf and current_image_url %}
        <script type="module">
          import * as pdfjsLib from "https://cdn.jsdelivr.net/npm/pdfjs-dist@5.4.530/build/pdf.min.mjs";

          pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdn.jsdelivr.net/npm/pdfjs-dist@5.4.530/build/pdf.worker.min.mjs";

          const stage = document.getElementById("stage");
          const pdfPages = document.getElementById("pdfPages");
          const statusEl = document.getElementById("status");
          const toolButtons = document.querySelectorAll(".toolbtn[data-tool]");
          const clearLastBtn = document.getElementById("clearLast");
          const saveBtn = document.getElementById("saveLayout");

const autoBtn = document.getElementById("autoSuggest");

function detectFieldType(text) {
  const t = text.toLowerCase();

  if (t.includes("signature")) return "signature";
  if (t.includes("date")) return "date";
  if (t.includes("name")) return "name";
  if (t.includes("phone")) return "phone";
  if (t.includes("email")) return "email";
  if (t.includes("address")) return "address";

  return null;
}

async function autoSuggestFields(pdf) {
  setStatus("Scanning PDF for fields...");

  for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
    const page = await pdf.getPage(pageNum);
    const textContent = await page.getTextContent();

    const viewport = page.getViewport({ scale: 1.35 });

    textContent.items.forEach(item => {
      const rawText = item.str.trim();
      if (rawText.length > 10) return;

      const fieldType = detectFieldType(rawText);
      if (!fieldType) return;

      const tx = item.transform[4];
      const ty = item.transform[5];

      const x = (tx + 35) / viewport.width;
      const y = 1 - (ty / viewport.height);

      fields.push({
        page: pageNum,
        type: fieldType,
        x: Number(x.toFixed(6)),
        y: Number(y.toFixed(6)),
        width: 0.2
      });
    });
  }

  renderFields();
  setStatus("Auto-suggest complete. Adjust fields if needed.");
}

if (autoBtn) {
  autoBtn.addEventListener("click", async () => {
    const loadingTask = pdfjsLib.getDocument(pdfUrl);
    const pdf = await loadingTask.promise;
    await autoSuggestFields(pdf);
  });
}


          let selectedTool = "";
          let snapEnabled = false;
          let fields = {{ initial_fields|tojson }};
          const pdfUrl = {{ current_image_url|tojson }};

          function markerText(type) {
            if (type === "name" || type === "text") return "Text";
            if (type === "email") return "Email";
            if (type === "phone") return "Phone";
            if (type === "address") return "Address";
            if (type === "checkbox") return "✓";
            if (type === "date") return "Date";
            if (type === "signature") return "Signature";
            return type;
          }

          function setStatus(msg) {
            statusEl.textContent = msg || "";
          }

          function renderFields() {
            document.querySelectorAll(".marker").forEach(el => el.remove());

            fields.forEach((field, index) => {
              const wrap = document.querySelector('.pdf-page-wrap[data-page="' + field.page + '"]');
              if (!wrap) return;

              const el = document.createElement("div");
              el.className = "marker";
              el.style.left = (field.x * 100) + "%";
              el.style.top = (field.y * 100) + "%";
              if (field.type === "checkbox") {
                el.style.width = "18px";
                el.style.minWidth = "18px";
                el.style.padding = "1px 3px";
                el.style.borderRadius = "999px";
                el.style.fontSize = "12px";
                el.style.lineHeight = "12px";
                el.innerHTML = "✓";
              } else {
                el.style.width = ((field.width || 0.18) * 100) + "%";
                el.innerHTML = markerText(field.type) + '<small>P' + field.page + '</small>';
              }
              el.title = "Double-click to delete";
              el.onclick = (e) => {
                e.preventDefault();
                e.stopPropagation();
              };
              el.ondblclick = (e) => {
                e.preventDefault();
                e.stopPropagation();
                fields.splice(index, 1);
                renderFields();
                setStatus("Field removed.");
              };
              wrap.appendChild(el);
            });
          }

          function syncActiveToolView() {
            document.querySelectorAll(".pdf-page-wrap").forEach(el => {
              if (selectedTool) el.classList.add("active-tool");
              else el.classList.remove("active-tool");
            });
          }

          async function renderPdf() {
            pdfPages.innerHTML = "";
            setStatus("Rendering PDF pages...");

            const loadingTask = pdfjsLib.getDocument(pdfUrl);
            const pdf = await loadingTask.promise;

            for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
              const page = await pdf.getPage(pageNum);
              const viewport = page.getViewport({ scale: 1.35 });

              const wrap = document.createElement("div");
              wrap.className = "pdf-page-wrap";
              wrap.dataset.page = String(pageNum);

              const label = document.createElement("div");
              label.className = "pdf-page-label";
              label.textContent = "Page " + pageNum + " of " + pdf.numPages;

              const canvas = document.createElement("canvas");
              canvas.className = "pdf-canvas";
              canvas.width = viewport.width;
              canvas.height = viewport.height;
              canvas.dataset.page = String(pageNum);

              wrap.appendChild(label);
              wrap.appendChild(canvas);
              pdfPages.appendChild(wrap);

              await page.render({
                canvasContext: canvas.getContext("2d"),
                viewport
              }).promise;

              let clickLocked = false;
let toolTimeout = null;

              wrap.addEventListener("click", (e) => {
                if (clickLocked) return;
                clickLocked = true;

                // 1 second click delay
                setTimeout(() => { clickLocked = false; }, 1000);

                // reset 5-second tool auto-off timer
                if (toolTimeout) clearTimeout(toolTimeout);
                toolTimeout = setTimeout(() => {
                  selectedTool = "";
                  toolButtons.forEach(b => b.classList.remove("active"));
                  syncActiveToolView();
                  setStatus("Tool auto-turned off.");
                }, 5000);
                if (!selectedTool) {
                  setStatus("Choose Checkbox, Date, or Signature first.");
                  return;
                }

                const rect = canvas.getBoundingClientRect();
                if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) {
                  return;
                }

                let x = (e.clientX - rect.left) / rect.width;
                let y = (e.clientY - rect.top) / rect.height;

                if (selectedTool === "checkbox") {
                  x = x - 0.005;
                  y = y + 0.010;

                  // snap to consistent rows
                  if (snapEnabled) {
                  y = Math.round(y * 28) / 28;
                }
                }

                const defaultFieldName =
                  selectedTool === "name" ? "legal_name" :
                  selectedTool === "date" ? "signature_date" :
                  selectedTool === "signature" ? "signature_data" :
                  selectedTool === "checkbox" ? "signature_ack" :
                  selectedTool;

                const defaultWidth =
                  selectedTool === "signature" ? 0.55 :
                  selectedTool === "name" ? 0.45 :
                  selectedTool === "date" ? 0.28 :
                  selectedTool === "checkbox" ? 0.08 :
                  0.30;

                fields.push({
                  page: pageNum,
                  type: selectedTool,
                  field_name: defaultFieldName + "_" + fields.length,
                  x: Number(x.toFixed(6)),
                  y: Number(y.toFixed(6)),
                  width: Number(defaultWidth.toFixed(6))
                });

                renderFields();
                selectedTool = "";
                toolButtons.forEach(b => b.classList.remove("active"));
                syncActiveToolView();
                setStatus("Field placed on page " + pageNum + ".");
              });
            }

            renderFields();
            syncActiveToolView();
            setStatus("PDF ready. Choose a tool, then click the correct page.");
          }

          
          const snapBtn = document.getElementById("snapToggle");
          if (snapBtn) {
            snapBtn.addEventListener("click", () => {
              snapEnabled = !snapEnabled;
              snapBtn.textContent = snapEnabled ? "Snap: ON" : "Snap: OFF";
              setStatus("Snap " + (snapEnabled ? "enabled." : "disabled."));
            });
          }
toolButtons.forEach(btn => {
            btn.addEventListener("click", () => {
              selectedTool = btn.dataset.tool;
              toolButtons.forEach(b => b.classList.remove("active"));
              btn.classList.add("active");
              syncActiveToolView();
              setStatus(selectedTool + " selected. Click the correct PDF page.");
            });
          });

          clearLastBtn.addEventListener("click", () => {
            if (!fields.length) {
              setStatus("No fields to remove.");
              return;
            }
            fields.pop();
            renderFields();
            setStatus("Last field removed.");
          });

          saveBtn.addEventListener("click", async () => {
            const img = {{ current_image|tojson }} || "";
            if (!img) {
              setStatus("Upload a page, PDF, or document first.");
              return;
            }

            const res = await fetch("/form-builder/save", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ img, fields })
            });

            const data = await res.json().catch(() => ({}));
            if (res.ok) {
              setStatus(data.message || "Layout saved.");

              // AUTO LOAD NEXT FORM
              const selector = document.getElementById("doc-selector");
              if (selector && selector.selectedIndex >= 0) {
                const nextIndex = selector.selectedIndex + 1;
                if (nextIndex < selector.options.length) {
                  const nextValue = selector.options[nextIndex].value;
                  if (nextValue) {
                    setTimeout(() => {
                      window.location = "/form-builder?pdf=" + encodeURIComponent(nextValue);
                    }, 600);
                  }
                } else {
                  setStatus("All forms completed.");
                }
              }

            } else {
              setStatus(data.error || "Save failed.");
            }
          });

          renderPdf().catch(err => {
            console.error(err);
            setStatus("PDF render failed: " + (err && err.message ? err.message : String(err)));
          });
        </script>
        {% else %}
        <script>
          const stage = document.getElementById("stage");
          const docImage = document.getElementById("docImage");
          const statusEl = document.getElementById("status");
          const toolButtons = document.querySelectorAll(".toolbtn[data-tool]");
          const clearLastBtn = document.getElementById("clearLast");
          const saveBtn = document.getElementById("saveLayout");

          let selectedTool = "";
          let snapEnabled = false;
          let fields = {{ initial_fields|tojson }};

          function markerText(type) {
            if (type === "name" || type === "text") return "Text";\n            if (type === "email") return "Email";\n            if (type === "phone") return "Phone";\n            if (type === "address") return "Address";\n            if (type === "checkbox") return "✓";
            if (type === "date") return "Date";
            if (type === "signature") return "Signature";
            return type;
          }

          function setStatus(msg) {
            statusEl.textContent = msg || "";
          }

          function renderFields() {
            document.querySelectorAll(".marker").forEach(el => el.remove());

            fields.forEach((field, index) => {
              const el = document.createElement("div");
              el.className = "marker";
              el.style.left = (field.x * 100) + "%";
              el.style.top = (field.y * 100) + "%";
              el.textContent = markerText(field.type);
              el.title = "Double-click to delete";
              el.ondblclick = () => {
                fields.splice(index, 1);
                renderFields();
                setStatus("Field removed.");
              };
              stage.appendChild(el);
            });
          }

          
          const snapBtn = document.getElementById("snapToggle");
          if (snapBtn) {
            snapBtn.addEventListener("click", () => {
              snapEnabled = !snapEnabled;
              snapBtn.textContent = snapEnabled ? "Snap: ON" : "Snap: OFF";
              setStatus("Snap " + (snapEnabled ? "enabled." : "disabled."));
            });
          }
toolButtons.forEach(btn => {
            btn.addEventListener("click", () => {
              selectedTool = btn.dataset.tool;
              toolButtons.forEach(b => b.classList.remove("active"));
              btn.classList.add("active");
              setStatus(selectedTool + " selected. Now click the page.");
            });
          });

          clearLastBtn.addEventListener("click", () => {
            if (!fields.length) {
              setStatus("No fields to remove.");
              return;
            }
            fields.pop();
            renderFields();
            setStatus("Last field removed.");
          });

          stage.addEventListener("click", (e) => {
            if (!selectedTool) {
              setStatus("Choose Checkbox, Date, or Signature first.");
              return;
            }

            if (!docImage || !docImage.getBoundingClientRect) {
              setStatus("Upload a page, PDF, or document first.");
              return;
            }

            const rect = docImage.getBoundingClientRect();
            if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) {
              return;
            }

            const x = (e.clientX - rect.left) / rect.width;
            const y = (e.clientY - rect.top) / rect.height;

            fields.push({
              page: 1,
              type: selectedTool,
              x: Number(x.toFixed(6)),
              y: Number(y.toFixed(6))
            });

            renderFields();
            toolButtons.forEach(b => b.classList.remove("active"));
            setStatus("Field placed.");
          });

          saveBtn.addEventListener("click", async () => {
            const img = {{ current_image|tojson }} || "";
            if (!img) {
              setStatus("Upload a page, PDF, or document first.");
              return;
            }

            const res = await fetch("/form-builder/save", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ img, fields })
            });

            const data = await res.json().catch(() => ({}));
            if (res.ok) {
              setStatus(data.message || "Layout saved.");
            } else {
              setStatus(data.error || "Save failed.");
            }
          });

          renderFields();
        </script>
        {% endif %}
      </body>
    </html>
    """, current_image=current_image, current_doc_value=current_doc_value, current_image_url=current_image_url, current_is_image=current_is_image, current_is_pdf=current_is_pdf, initial_fields=initial_fields, form_options=form_options)


@app.route("/form-builder/save", methods=["POST"])
def form_builder_save():
    import json
    from pathlib import Path

    session_id = session.get("licensed_session_id") or request.args.get("session_id")

    payload = request.get_json(silent=True) or {}
    img = (payload.get("img") or "").strip()
    fields = payload.get("fields") or []

    if not img:
        return {"error": "Missing image name."}, 400

    if not isinstance(fields, list):
        return {"error": "Fields must be a list."}, 400

    clean_fields = []
    for f in fields:
        try:
            clean_fields.append({
                "page": int(f.get("page", 1)),
                "type": str(f.get("type", "")).strip(),
                "field_name": str(f.get("field_name", "")).strip(),
                "x": float(f.get("x", 0)),
                "y": float(f.get("y", 0)),
                "width": float(f.get("width", 0.18)),
            })
        except Exception:
            continue

    layout_dir = Path("form_builder_layouts")
    layout_dir.mkdir(parents=True, exist_ok=True)

    clean_name = Path(img).name
    out = layout_dir / f"{clean_name}.json"
    out.write_text(json.dumps(clean_fields, indent=2))
    return {"ok": True, "message": f"Layout saved to {out.name}."}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)