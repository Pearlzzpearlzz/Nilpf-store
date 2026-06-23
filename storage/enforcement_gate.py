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

ILH_ONLY_PREFIXES = (
    "/entry-screening/",
    "/entry-screening-print/",
    "/entry-screening-final/",
    "/entry-screening-unlock/",
    "/medical-attestation/",
    "/independent-living-disclosure/",
    "/no-services-supervision/",
    "/vehicle-parking/",
    "/bill-of-dignity/",
    "/ilh-",
)

TH_ONLY_PREFIXES = (
    "/intake-assessment/",
    "/mla/",
    "/mla-print/",
    "/mla-final/",
    "/mla-unlock/",
    "/program-compliance-addendum/",
    "/program-participation-agreement/",
)

def expected_program_family(program_type):
    if program_type == "ILH":
        return "ILH"
    if program_type in TH_PROGRAMS:
        return "TH"
    return "UNKNOWN"


def classify_route_family(path):
    if any(path.startswith(prefix) for prefix in ILH_ONLY_PREFIXES):
        return "ILH"
    if any(path.startswith(prefix) for prefix in TH_ONLY_PREFIXES):
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

    view_args = request.view_args or {}
    raw_pid = view_args.get("id", view_args.get("pid"))

    pid = None
    participant_program = None

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
    route_family = classify_route_family(path)
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
