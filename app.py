from reportlab.pdfgen import canvas
import fitz
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory
import json
import os
from datetime import date, datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)

PARTICIPANTS_FILE = "data/participants.json"

def load_participants():
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(PARTICIPANTS_FILE):
        return []
    try:
        with open(PARTICIPANTS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_participants(participants):
    os.makedirs("data", exist_ok=True)
    with open(PARTICIPANTS_FILE, "w") as f:
        json.dump(participants, f, indent=2)



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
    True-to-sight PDF engine.
    Renders the print template directly, then converts it to PDF using Playwright.
    """
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    from flask import render_template
    import os

    current_participants = load_participants()

    if pid < 0 or pid >= len(current_participants):
        raise ValueError(f"Participant not found for PDF export: {pid}")

    participant = current_participants[pid]

    form_key = filename
    if form_key.endswith("_true_to_sight.pdf"):
        form_key = form_key.replace("_true_to_sight.pdf", "")
    elif form_key.endswith(".pdf"):
        form_key = form_key.replace(".pdf", "")

    form_state = participant.get("forms", {}).get(form_key, {})
    d = form_state.get("data", {})
    locked = form_state.get("locked", False)

    PDF_TEMPLATE_OVERRIDES = {
        "mla": "mla_transitional_print.html",
    }

    template_name = PDF_TEMPLATE_OVERRIDES.get(form_key, f"{form_key}_print.html")

    output_dir = Path("static/filled") / f"participant_{pid}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename

    html = render_template(
        template_name,
        id=pid,
        participant=participant,
        person=participant,
        d=d,
        data=d,
        locked=locked,
        form=form_state,
        activation=load_activation()
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=str(output_path),
            format="Letter",
            print_background=True,
            margin={
                "top": "0.35in",
                "right": "0.35in",
                "bottom": "0.35in",
                "left": "0.35in"
            }
        )
        browser.close()

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"PDF was not created or was empty: {output_path}")

    return str(output_path)


def download_packet(id):
    participants = load_participants()
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
        if is_property_paper_route(route):
            save_locked_property_paper(route, state.get("data", {}))
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
    participants = load_participants()
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
    participants = load_participants()
    if id < 0 or id >= len(participants):
        return redirect(url_for("add_participant"))

    participant = participants[id]
    state = participant.setdefault("forms", {}).setdefault("mla", {"data": {}, "locked": False, "completed": False})

    return render_template("mla_transitional_print.html", id=id, participant=participant, d=state.get("data", {}), locked=state.get("locked", False))


@app.route("/mla-final/<int:id>", methods=["POST"])
def mla_final(id):
    participants = load_participants()
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
    participants = load_participants()
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
        participants = load_participants()
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
        participants = load_participants()
        if id < 0 or id >= len(participants):
            return redirect(url_for("add_participant"))
        state = th_form_state(id, key, participants)
        return render_template(print_template, id=id, participant=participants[id], d=state.get("data", {}), locked=state.get("locked", False))

    @app.route(f"/{route}-final/<int:id>", methods=["POST"], endpoint=f"{route}_final")
    def th_final(id, route=route, key=key):
        participants = load_participants()
        if id < 0 or id >= len(participants):
            return redirect(url_for("add_participant"))
        state = th_form_state(id, key, participants)
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

        if is_property_paper_route(route):
            save_locked_property_paper(route, state.get("data", {}))
            return redirect("/property-papers")

        return redirect(f"/packet-builder/{id}")

    @app.route(f"/{route}-unlock/<int:id>", methods=["GET", "POST"], endpoint=f"{route}_unlock")
    def th_unlock(id, route=route, key=key):
        participants = load_participants()
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
    participants = load_participants()
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
    participants = load_participants()
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    form = p.get("forms", {}).get("referral_source_record", {})
    data = form.get("data", {})
    locked = form.get("locked", False)

    return render_template("referral_source_record_print.html", id=id, participant=p, data=data, locked=locked)


@app.route("/referral-source-record-final/<int:id>", methods=["POST"])
def referral_source_record_final(id):
    participants = load_participants()
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
    participants = load_participants()
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
    participants = load_participants()
    if id >= len(participants):
        return "Participant not found"

    p = participants[id]
    form = p.get("forms", {}).get("agency_sponsorship_record", {})
    data = form.get("data", {})
    locked = form.get("locked", False)

    return render_template("agency_sponsorship_record_print.html", id=id, participant=p, data=data, locked=locked)


@app.route("/agency-sponsorship-record-final/<int:id>", methods=["POST"])
def agency_sponsorship_record_final(id):
    participants = load_participants()
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


@app.route("/mr-ir/scan")
def mr_ir_scan():
    scan_targets = [
        {"name": "Mr.IR Dashboard", "path": "/mr-ir", "purpose": "Internal repair dashboard"},
        {"name": "Operations", "path": "/operations", "purpose": "Main operations/control area"},
        {"name": "Rolodex", "path": "/rolodex", "purpose": "Organization contact list"},
        {"name": "PDF Diagnostic", "path": "/render-pdf-diagnostic", "purpose": "Render/Docker PDF generation check"},
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

if __name__ == "__main__":
    app.run(debug=True)
