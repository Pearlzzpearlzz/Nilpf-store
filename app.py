

framework_groups = {
    "Program Foundations": [
        ("Member Bill of Rights", "00_Member_Bill_of_Rights_v2.1.pdf"),
        ("NILPF Charter", "01_NILPF_Charter_of_Human_Dignity_and_Independent_Living_v2.1.pdf"),
    ],
    "Getting Started": [
        ("Pre-Opening Waitlist Form", "14-Pre_Opening_Waitlist_Form.pdf"),
        ("Entry Screening", "18_Entry_Screening_v2.1.pdf"),
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
    conn.commit()
    conn.close()

# Product catalog (edit prices + file paths as you wish)

PRODUCTS = {
    "STANDARD_SET": {
        "label": "NILPF Standard Docs System",
        "price": "1.00",
        "file": "downloads/ALL_BUNDLE.zip",
    },
    "COMPLETE_SET": {
        "label": "NILPF Complete Docs System",
        "price": "1.00",
        "file": "downloads/ALL_BUNDLE.zip",
    },
    "MASTER_LEASE": {
        "label": "Master Lease v2.1",
        "price": "1.00",
        "file": "static/documents/Master_Lease_v2.1.pdf",
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

def upsert_license(session_id: str, email: str, name: str, address: str, state_abbr: str, product_sku: str = None, transaction_id: str = None) -> str:
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
    # Store PayPal transaction id (replay protection)
    if transaction_id:
        try:
            cur.execute("UPDATE licenses SET transaction_id=? WHERE session_id=?", (transaction_id, session_id))
        except Exception:
            try:
                cur.execute("UPDATE licenses SET transaction_id=? WHERE license_key=?", (transaction_id, license_key))
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
FORM_DEFINITIONS = {
    "18_Entry_Screening_v2.1.pdf": {
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
# Participant Workflow UI
# -------------------------
FORM_LABELS = {
    "00_Member_Bill_of_Rights_v2.1.pdf": "Member Bill of Rights",
    "01_NILPF_Charter_of_Human_Dignity_and_Independent_Living_v2.1.pdf": "Charter of Human Dignity and Independent Living",
    "18_Entry_Screening_v2.1.pdf": "Entry Screening",
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

    "18_Entry_Screening_v2.1.pdf": "Entry",

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

    "18_Entry_Screening_v2.1.pdf": "required",

    "1_Independent_Living_Disclosure_v2.1.pdf": "required",
    "2_No_Services_No_Supervision_Acknowledgement.pdf": "required",
    "3_Voluntary_Participation_Acknowledgement.pdf": "required",

    "4_House_Rules_and_Community_Standards.pdf": "required",
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
    "10-Pet_Animal_Information_Sheet.pdf": "optional",
    "11-Vehicle_Parking_Information_Form.pdf": "optional",
    "9_Guest_Addendum.pdf": "optional",

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
                "is_complete": is_complete,
                "completed_at": completed_at,
                "is_required": is_required,
                "is_conditional": is_conditional,
                "requirement_label": requirement_label
            })
        grouped[group_name] = items

    percent = int((completed_required / total_required) * 100) if total_required else 0
    progress = {
        "completed": completed_required,
        "total_required": total_required,
        "percent": percent
    }

    return participant, grouped, progress


def get_grouped_participant_forms():
    return {
        "Foundation": [
            {"form_name": "00_Member_Bill_of_Rights_v2.1.pdf", "label": "Member Bill of Rights", "conditional": False},
            {"form_name": "01_NILPF_Charter_of_Human_Dignity_and_Independent_Living_v2.1.pdf", "label": "NILPF Charter of Human Dignity and Independent Living", "conditional": False}
        ],
        "Entry": [
            {"form_name": "18_Entry_Screening_v2.1.pdf", "label": "Entry Screening", "conditional": False},
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

    if request.method == "POST":
        payload = {}
        for field in form_def["fields"]:
            payload[field["name"]] = request.form.get(field["name"], "").strip()
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
        .wrap { max-width: 920px; margin: 0 auto; padding: 24px; }
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
<a href="/" style="color:#fff;margin-right:20px;">Home</a>
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
          <form method="POST">

            {% for field in form_def.fields %}
              <div class="field">
                <label for="{{ field.name }}">{{ field.label }}</label>
                {% if field.type == "textarea" %}
                  <textarea id="{{ field.name }}" name="{{ field.name }}">{{ values.get(field.name, "") }}</textarea>
                {% else %}
                  <input id="{{ field.name }}" name="{{ field.name }}" type="{{ field.type if field.type in ['text','date'] else 'text' }}" value="{{ values.get(field.name, "") }}">
                {% endif %}
              </div>
            {% endfor %}

            <div class="btnrow">
              <button class="btn" type="submit">Save Form</button>
              <a class="btn alt" href="/participant-form-print/{{ pid }}/{{ quoted_form_name }}" target="_blank">Print</a>
              <a class="btn alt" href="/participant-workflow/{{ pid }}">Back to Workflow</a>
            </div>
          </form>
        </div>
      </div>
    </body>
    </html>
    """,
    pid=pid,
    legal_name=legal_name,
    display_name=display_name,
    form_def=form_def,
    values=values,
    quoted_form_name=quote(form_name))

@app.route("/participant-form-print/<int:participant_id>/<path:form_name>")
def participant_form_print(participant_id, form_name):
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
    values = get_participant_form_values(participant_id, form_name)

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
      <title>{{ form_def.title }} - Print</title>
      <style>
        body { font-family: Arial, sans-serif; margin: 24px; color: #111; background:#fff; }
        .sheet { max-width: 900px; margin: 0 auto; }
        .head { border-bottom: 3px solid #111; padding-bottom: 12px; margin-bottom: 18px; }
        h1 { margin: 0 0 6px 0; font-size: 28px; }
        .sub { margin-bottom: 6px; color: #444; }
        .meta { margin-top: 10px; font-size: 14px; color: #333; }
        .line { margin-bottom: 16px; padding: 10px 0 12px 0; border-bottom: 1px solid #ccc; }
        .label { font-weight: 700; margin-bottom: 6px; font-size: 15px; }
        .value { white-space: pre-wrap; min-height: 22px; font-size: 15px; line-height: 1.4; }
        .printbtn {
          display: inline-block; border: 2px solid #111; background: #111; color: #fff;
          padding: 10px 14px; border-radius: 999px; font-weight: 700; cursor: pointer;
        }
        .footnote { margin-top: 24px; font-size: 12px; color: #555; }
        @media print {
          .noprint { display: none; }
          body { margin: 0.5in; }
        }
      </style>
    </head>
    <body>
<div style="background:#111;color:#fff;padding:10px;">
<a href="/" style="color:#fff;margin-right:20px;">Home</a>
<a href="/participants" style="color:#fff;margin-right:20px;">Add / View Participants</a>
<a href="/notes" style="color:#fff;">Incident / Status Documentation</a>
</div>

      <div class="sheet">
        <div class="noprint" style="margin-bottom:16px;">
          <button class="printbtn" onclick="window.print()">Print</button>
        </div>

        <div class="head">
          <h1>{{ form_def.title }}</h1>
          <div class="sub">Participant: {{ display_name }}{% if legal_name != display_name %} | Legal Name: {{ legal_name }}{% endif %}</div>
          <div class="meta">Printable participant copy generated from the NILPF workflow.</div>
        </div>

        {% for field in form_def.fields %}
          <div class="line">
            <div class="label">{{ field.label }}</div>
            <div class="value">{{ values.get(field.name, "") }}</div>
          </div>
        {% endfor %}

        <div class="footnote">
          This copy reflects the information currently saved in the participant workflow for this form.
        </div>
      </div>
    </body>
    </html>
    """,
    form_def=form_def,
    values=values,
    display_name=display_name,
    legal_name=legal_name)




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
        </style>
      </head>
      <body>
        <div class="topbar">
          <a href="/home">Home</a>
          <a href="/documents">Dashboard</a>
          <a href="/participants">Add / View Participants</a>
          <a href="/notes">Incident / Status Documentation</a>
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
              <a class="btn" href="/documents">Open Framework</a>
            </div>

            <div class="card">
              <h2>Incident / Status Documentation</h2>
              <p>Record participant decline, incidents, concerns, and follow-up notes.</p>
              <a class="btn" href="/notes">Open Notes</a>
            </div>
          </div>

          <div class="footer-note">
            Use this page as the central exit point for staff when they are finished or stepping away.
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
          .row { border:1px solid #d7dbe7; border-radius:14px; padding:12px; margin-bottom:10px; background:#fafafa; }
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
        <div class="wrap">
          <div class="top">
            <h1>Participant Workflow</h1>
            <div class="note"><strong>{{ display_name }}</strong> · Participant ID {{ pid }}</div>
            <div class="note">Created: {{ created_at }}</div>

            <div class="progressbox">
              <div class="progressmeta">
                <span>{{ progress.completed }} / {{ progress.total_required }} required complete</span>
                <span>{{ progress.percent }}%</span>
              </div>
              <div class="bar">
                <div class="fill" style="width: {{ progress.percent }}%;"></div>
              </div>
            </div>

            <div class="btnrow">
              <a class="btn alt" href="/participant/{{ pid }}">Back to Participant</a>
              <a class="btn alt" href="/participants">Participant Manager</a>
              <a class="btn alt" href="/home">Home</a>
            </div>
          </div>

          <div class="grid">
            {% for group_name, items in grouped.items() %}
              <div class="card">
                <h2>{{ group_name }}</h2>
                {% for item in items %}
                  <div class="row {% if item.is_complete %}done{% endif %}">
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
                    session["session_id"] = session_id
                    session["license_key"] = license_key
                    session["payer_email"] = payer_email or email
                    session["payer_name"] = payer_name or ""
                    session["licensed_location"] = prop_addr or address
                    session["property_state"] = prop_state or ""
                    session["product_sku"] = product_sku or ""
                    return redirect("/documents")
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
<div style="background:#111;color:#fff;padding:10px;">
<a href="/" style="color:#fff;margin-right:20px;">Home</a>
<a href="/documents" style="color:#fff;margin-right:20px;">Dashboard</a>
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
    c.save()

    buf.seek(0)
    filename = f"certificate_{session_id}.pdf"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")



@app.route("/")
def home():
    # Home always logs the user out
    session.pop("licensed_session_id", None)
    session.pop("licensed_location", None)
    session.pop("product_sku", None)

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
            padding: 32px 18px 60px;
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
            font-size: 18px;
            margin-bottom: 18px;
          }
          .note {
            background: #f2f4f8;
            border-left: 5px solid #111;
            padding: 14px;
            border-radius: 10px;
            margin: 16px 0 22px;
          }
          ul {
            line-height: 1.7;
            padding-left: 22px;
          }
          .actions {
            margin-top: 24px;
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
          }
          .btn {
            display: inline-block;
            padding: 14px 18px;
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
<div style="background:#111;color:#fff;padding:10px;">
<a href="/" style="color:#fff;margin-right:20px;">Home</a>
<a href="/documents" style="color:#fff;margin-right:20px;">Dashboard</a>
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
              <li>Certificate access</li>
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

            <div style="margin-top:24px;padding:18px;border:2px solid #111;border-radius:14px;">
              <h3 style="margin-top:0;">Business Address Login</h3>
              <p class="note">Enter your licensed business address to restore access.</p>
              <form method="POST" action="/restore-access">
                <input
                  name="business_address"
                  placeholder="Licensed Business Address"
                  required
                  style="width:100%;padding:12px;border:2px solid #111;border-radius:10px;box-sizing:border-box;margin-bottom:12px;"
                >
                <button class="btn primary" type="submit">Restore Access</button>
              </form>
            </div>
          </div>
        </div>
      </body>
    </html>
    """)




def get_license_by_business_address(address: str):
    import sqlite3
    addr = (address or "").strip()
    if not addr:
        return None

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    row = cur.execute(
        """
        SELECT session_id, payer_email, payer_name, property_address, property_state, license_key, created_at, product_sku
        FROM licenses
        WHERE TRIM(LOWER(property_address)) = TRIM(LOWER(?))
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (addr,)
    ).fetchone()
    conn.close()
    return row


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

    session["license_session_id"] = db_session_id
    session["product_sku"] = product_sku or "COMPLETE_SET"
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
        session["product_sku"] = "COMPLETE_SET"
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
          .field{display:flex;flex-direction:column;min-width:140px;flex:1}
          .field.street{min-width:260px;flex:2}
          label{display:block;margin:0 0 6px;font-weight:bold}
          input{width:100%;padding:12px;border:2px solid #111;border-radius:10px;box-sizing:border-box}
          .btn{display:inline-block;padding:14px 18px;border:2px solid #111;border-radius:12px;background:#111;color:#fff;text-decoration:none;font-weight:bold;white-space:nowrap}
        </style>
      </head>
      <body>
<div style="background:#111;color:#fff;padding:10px;">
<a href="/" style="color:#fff;margin-right:20px;">Home</a>
<a href="/documents" style="color:#fff;margin-right:20px;">Dashboard</a>
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

@app.route("/buy")
def buy():
    session["product_sku"] = "COMPLETE_SET"
    product = PRODUCTS.get("COMPLETE_SET")
    if not product:
        abort(500, "COMPLETE_SET product is missing.")

    if not session.get("licensed_location"):
        return redirect("/activate")

    access_token = get_paypal_access_token()
    return_url = request.host_url.rstrip("/") + "/success"
    cancel_url = request.host_url.rstrip("/") + "/cancel"

    order_data = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {"currency_code": "USD", "value": product["price"]},
                "custom_id": "COMPLETE_SET",
                "description": product.get("label", "COMPLETE_SET"),
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
    if not location:
        abort(400, "Missing licensed location in session.")

    full_address = f"{location['street']}, {location['city']}, {location['state']} {location['zip']}"

    upsert_license(
        order_id,
        location.get("email"),
        location.get("business_name"),
        full_address,
        location.get("state"),
        "COMPLETE_SET",
    )

    session["licensed_session_id"] = order_id
    session.pop("licensed_location", None)
    session["product_sku"] = "COMPLETE_SET"
    return redirect(f"/documents?session_id={order_id}")

@app.route("/cancel")
def cancel():
    return redirect("/activate")

@app.route("/product", methods=["GET", "POST"])
def product():
    return redirect("/activate")

@app.route("/documents")
def documents():
    session_id = request.args.get("session_id") or session.get("licensed_session_id")
    if not session_id:
        return redirect("/activate")

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
            grid-template-columns: 240px 1fr;
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
<div style="background:#111;color:#fff;padding:10px;">
<a href="/" style="color:#fff;margin-right:20px;">Home</a>
<a href="/documents" style="color:#fff;margin-right:20px;">Dashboard</a>
<a href="/participants" style="color:#fff;margin-right:20px;">Add / View Participants</a>
<a href="/notes" style="color:#fff;">Incident / Status Documentation</a>
</div>

      <div class="box">
        <div class="topbar">
          <h1>Operational Framework</h1>
          <p>{{ prop_addr }} · License Key {{ license_key }}</p>


        </div>

        <div class="layout">
          <aside class="sidebar">
            <a class="tablink {% if active_tab == 'dashboard' %}active{% endif %}" href="/documents?session_id={{ session_id }}&tab=dashboard">Dashboard</a>
            <a class="tablink {% if active_tab == 'participants' %}active{% endif %}" href="/documents?session_id={{ session_id }}&tab=participants">Add / View Participants</a>
            <a class="tablink {% if active_tab == 'operational' %}active{% endif %}" href="/documents?session_id={{ session_id }}&tab=operational">Program Framework</a>
            <a class="tablink {% if active_tab == 'master' %}active{% endif %}" href="/documents?session_id={{ session_id }}&tab=master">Master Lease</a>
            <a class="tablink {% if active_tab == 'client' %}active{% endif %}" href="/documents?session_id={{ session_id }}&tab=client">Operations</a>
            <a class="tablink {% if active_tab == 'notes' %}active{% endif %}" href="/documents?session_id={{ session_id }}&tab=notes">Incident / Status Documentation</a>
          </aside>

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
                      <a class="btn" href="/product">Add Another Property</a>
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
                    <p><a class="btn" href="/static/documents/18_Entry_Screening_v2.1.pdf" target="participant_viewer">ENTRY SCREENING</a></p>
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

                <iframe class="viewer" name="participant_viewer" src="/static/documents/18_Entry_Screening_v2.1.pdf" style="margin-top:16px;"></iframe>
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
            {% elif active_tab == 'client' %}
              <div class="card">
                <h2>Operations</h2>
                <p class="note">Operational forms open inside the workspace below.</p>
                <div class="btnrow" style="margin-bottom:12px;gap:8px;flex-wrap:wrap;">
                  <a class="btn" href="/static/documents/README_ESSENTIAL_FORMS_v2.1.pdf" target="operations_viewer">Essential Forms Guide</a>
                  
                  <a class="btn" href="/static/documents/15_IMPORTANT_NOTICE_AND_DISCLAIMER_v2.1.pdf" target="operations_viewer">Important Notice</a>
                  
                  <a class="btn" href="/static/documents/064_Owner_Acknowledgment_and_Program_Boundary_Handbook_v2.1.pdf" target="operations_viewer">Owner Handbook</a>
                </div>
                <iframe class="viewer" name="operations_viewer" src="/static/documents/README_ESSENTIAL_FORMS_v2.1.pdf"></iframe>
              </div>
            {% elif active_tab == 'master' %}
              <div class="card">
                <h2>Master Lease</h2>
                <p class="note">Master Lease opens inside the workspace below.</p>
                <iframe class="viewer" src="/static/documents/Master_Lease_v2.1.pdf"></iframe>
              </div>
            {% elif active_tab == 'notes' %}
              <div class="card">
                <h2>Incident / Status Documentation</h2>
                <div style="background:#fff8e1;border:2px solid #111;padding:12px;border-radius:10px;margin-bottom:15px;">
                <b>Adverse Occurrence Log Notice</b><br><br>
                This log records events that may affect <b>program participation, housing safety, or property operations</b>.<br><br>
                Entries should document only occurrences relevant to:<br>
                • Program participation<br>
                • Property safety<br>
                • Rule compliance<br>
                • Housing environment concerns<br><br>
                This log is <b>not a medical or clinical record</b> and must not include healthcare information, diagnoses, treatment notes, or personal health data.
                </div>
                <p class="note">Narrative charting belongs here. Use the participant notes area from the participant workspace.</p>
                <div style="background:#fff;border:2px solid #111;padding:12px;border-radius:10px;margin-bottom:15px;">
                <b>Quick Entry</b><br><br>
                <form>
                Date:<br>
                <input type="date" value="2026-03-10" style="width:200px;"><br><br>
                Reporter:<br>
                <input type="text" placeholder="Your name" style="width:250px;"><br><br>
                Participant:<br>
                <input type="text" placeholder="Participant name (if applicable)" style="width:250px;"><br><br>
                Note:<br>
                <textarea rows="4" style="width:100%;" placeholder="Describe the occurrence affecting program participation..."></textarea><br><br>
                <button type="submit">Save Note</button>
                </form>
                </div>
                <div class="btnrow">
                  <a href="/participants">Go to Incident / Status Documentation</a>
                </div>
              </div>
            {% endif %}
          </main>
        </div>
      





<!-- Dignity Idle Screen -->
<div id="dignityScreen" onclick="hideDignityScreen()" style="
display:none;
position:fixed;
top:0;
left:0;
width:100%;
height:100%;
background:black;
color:white;
justify-content:center;
align-items:center;
flex-direction:column;
text-align:center;
z-index:9999;
padding:40px;
">
<h1>Member Bill of Dignity</h1>
<p style="max-width:700px;font-size:18px;line-height:1.6;">
Every participant is entitled to dignity, independence, and respect.
This program exists to protect safe housing, responsible participation,
and the preservation of human dignity.
</p>
<p style="margin-top:30px;font-size:14px;color:#ccc;">
Click anywhere to return to workspace
</p>
</div>

<script>
let idleTimer;
const idleTimeLimit = 60000;

function resetIdleTimer() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(showDignityScreen, idleTimeLimit);
}

function showDignityScreen() {
    const el = document.getElementById("dignityScreen");
    if (el) el.style.display = "flex";
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
</script>

</body>
    </html>
    """, session_id=session_id, prop_addr=prop_addr, license_key=license_key,
        framework_groups=framework_groups,
       payer_email=payer_email, payer_name=payer_name, active_tab=active_tab)




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
                alerts_by_pid[str(pid)] = {
                    "total": total_forms,
                    "incomplete": incomplete_forms
                }
            except:
                alerts_by_pid[str(row[pid_index])] = {
                    "total": 0,
                    "incomplete": 0
                }

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
<div style="background:#111;color:#fff;padding:10px;">
<a href="/" style="color:#fff;margin-right:20px;">Home</a>
<a href="/documents" style="color:#fff;margin-right:20px;">Dashboard</a>
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
              <input type="date" value="2026-03-10" name="dob">
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
              <input type="date" value="2026-03-10" name="move_in_date">
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
    """, rows=rows, select_cols=select_cols, message=message, alerts_by_pid=alerts_by_pid)




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


# -------------------------
# NOTES PAGE
# -------------------------
@app.route("/notes")
def notes():
    session_id = session.get("session_id")
    license_key = session.get("license_key")
    if not session_id or not license_key:
        return redirect("/product")

    lic = get_license_by_session(session_id)
    if not lic:
        session.clear()
        return redirect("/product")

    payer_email, payer_name, prop_addr, prop_state, db_license_key, created_at, product_sku = lic
    if product_sku != "COMPLETE_SET":
        session.clear()
        return redirect("/product")
    import sqlite3
    conn=sqlite3.connect("licenses.db")
    cur=conn.cursor()
    cur.execute("SELECT participant_name,staff_name,note_text,created_at FROM participant_notes ORDER BY id DESC")
    rows=cur.fetchall()
    conn.close()

    html="<h1>Incident / Status Documentation</h1><a href='/home'>Home</a><hr>"
    for r in rows:
        html+=f"<p><b>{r[0]}</b> | {r[1]} | {r[3]}<br>{r[2]}</p><hr>"
    return html



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)


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
