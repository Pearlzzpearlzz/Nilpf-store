import json
import os
from datetime import datetime, timezone

try:
    import fcntl
except ImportError:
    fcntl = None

AUDIT_FILE = "data/enforcement_audit.jsonl"

TH_PROGRAMS = {
    "Transitional",
    "VA_GPD_Aligned",
    "DOC_Reentry_Aligned",
    "Reentry",
}

ILH_ONLY_ROUTES = {
    "entry-screening",
    "medical-attestation",
    "independent-living-disclosure",
    "fire-safety",
    "emergency-contact",
    "emergency-evacuation",
    "guest-addendum",
    "no-services-supervision",
    "common-area-security",
    "property-belongings",
    "privacy-acknowledgment",
    "privacy-noncommercial",
    "security-camera",
    "voluntary-participation",
    "bill-of-dignity",
    "ilh-mla",
}

TH_ONLY_ROUTES = {
    "intake-assessment",
    "mla",
    "program-compliance-addendum",
    "program-participation-agreement",
}

ADMIN_RECORD_ROUTES = {
    "property-paper",
    "property-paper-edit",
    "property-paper-print",
    "property-paper-final",
    "property-paper-unlock",
    "property-papers",
    "ilh-master-lease",
    "master-lease-transitional",
    "board-resolution",
    "mou-partner-agreement",
    "waiver-financial-justification",
    "triple-net-lease",
}

TEST_ONLY_ROUTES = {
    "ach-test",
}

SHARED_PID_ROUTES = {
    "house-rules",
    "vehicle-parking",
    "incident-report",
    "transfer",
    "pet-animal",
    "packet-builder",
    "download-packet",
    "participant-complete",
    "continue-flow",
    "skip-participant-form",
    "sensitive-identity-record",
    "release-of-information",
    "personal-belongings",
    "individual-service-plan",
    "service-activity-record",
    "participant-program-adherence-review",
    "exit-discharge-summary",
    "payor-funding-record",
    "billing-invoice-setup",
    "referral-source-record",
    "agency-sponsorship-record",
}


def normalize_route_name(path):
    parts = [part for part in (path or "").strip("/").split("/") if part]

    if not parts:
        return ""

    route_name = parts[0]

    if route_name == "form" and len(parts) >= 3:
        if parts[2] == "ach_authorization":
            return "ach-authorization"

    if route_name == "skip-participant-form" and len(parts) >= 2:
        return "skip-participant-form"

    for suffix in ("-print", "-final", "-unlock"):
        if route_name.endswith(suffix):
            route_name = route_name[:-len(suffix)]
            break

    return route_name



def expected_program_family(program_type):
    if program_type == "ILH":
        return "ILH"
    if program_type in TH_PROGRAMS:
        return "TH"
    return "UNKNOWN"


def classify_route_family(path):
    parts = [
        part
        for part in (path or "").strip("/").split("/")
        if part
    ]

    route_name = normalize_route_name(path)

    if route_name == "skip-participant-form":
        if len(parts) < 2:
            return "SHARED"

        target_route = parts[1]

        if target_route in ILH_ONLY_ROUTES:
            return "ILH"

        if target_route in TH_ONLY_ROUTES:
            return "TH"

        return "SHARED"

    if route_name in ADMIN_RECORD_ROUTES:
        return "ADMIN_RECORD"

    if route_name in TEST_ONLY_ROUTES:
        return "TEST_ONLY"

    if route_name in ILH_ONLY_ROUTES:
        return "ILH"

    if route_name in TH_ONLY_ROUTES:
        return "TH"

    return "SHARED"


def _append_jsonl(record):
    os.makedirs(os.path.dirname(AUDIT_FILE), exist_ok=True)

    with open(AUDIT_FILE, "a", encoding="utf-8") as audit_file:
        if fcntl is not None:
            fcntl.flock(audit_file.fileno(), fcntl.LOCK_EX)

        audit_file.write(json.dumps(record, sort_keys=True) + "\n")
        audit_file.flush()
        os.fsync(audit_file.fileno())

        if fcntl is not None:
            fcntl.flock(audit_file.fileno(), fcntl.LOCK_UN)

def audit_enforcement_request(request, session, participants):
    endpoint = request.endpoint or ""
    path = request.path or ""

    if endpoint == "static" or path.startswith("/static/"):
        return None

    if not session.get("logged_in"):
        return None

    route_family = classify_route_family(path)

    view_args = request.view_args or {}
    raw_pid = view_args.get("id", view_args.get("pid"))

    pid = None
    participant_program = None

    # Property Paper and test-route IDs are not participant PIDs.
    if route_family not in {"ADMIN_RECORD", "TEST_ONLY"}:
        if raw_pid is not None:
            try:
                pid = int(raw_pid)
            except (TypeError, ValueError):
                pid = None

        if pid is not None and isinstance(participants, list):
            if 0 <= pid < len(participants):
                participant = participants[pid] or {}
                participant_program = (
                    participant.get("program_type")
                    or participant.get("housing_type")
                    or "ILH"
                )

    expected_family = expected_program_family(participant_program)
    outcome = "allowed_audit_only"

    if (
        pid is not None
        and expected_family in {"ILH", "TH"}
        and route_family in {"ILH", "TH"}
        and expected_family != route_family
    ):
        outcome = "program_route_mismatch_audit_only"

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "audit_only",
        "endpoint": endpoint,
        "method": request.method,
        "path": path,
        "pid": pid,
        "participant_program": participant_program,
        "expected_family": expected_family,
        "route_family": route_family,
        "outcome": outcome,
        "authenticated": True,
    }

    _append_jsonl(record)
    return record
