from flask import session, redirect, url_for

DEFAULT_STATE = {
    "logged_in": False,
    "participant_id": None,
    "program_type": None,
    "stage": "login",
    "flow_locked": False
}

def get_state():
    if "flow_state" not in session:
        session["flow_state"] = DEFAULT_STATE.copy()
    return session["flow_state"]

def save_state(state):
    session["flow_state"] = state
    session.modified = True

def reset_state():
    session["flow_state"] = DEFAULT_STATE.copy()
    session.modified = True

FLOW_MAP = {
    "login": "login",
    "add_participant": "add_participant",
    "entry_screening": "entry_screening",
    "program_router": "program_router",
    "ilh_chain": "ilh_chain",
    "th_chain": "th_chain",
    "va_chain": "va_chain",
    "doc_chain": "doc_chain",
    "packet_builder": "packet_builder"
}

def get_next_route(state):
    stage = state.get("stage")

    if not state.get("logged_in"):
        return "login"

    if stage == "login":
        return "add_participant"

    if stage == "add_participant":
        return "entry_screening"

    if stage == "entry_screening":
        return "program_router"

    if stage in ["ilh_chain", "th_chain", "va_chain", "doc_chain"]:
        return stage

    if stage == "packet_builder":
        return "packet_builder"

    return "entry_screening"

def flow_redirect(state):
    return redirect(url_for(get_next_route(state)))

def advance_stage(state, new_stage):
    if new_stage in FLOW_MAP:
        state["stage"] = new_stage
        save_state(state)
    return state

def bind_participant(state, participant_id, program_type=None):
    state["participant_id"] = participant_id
    state["program_type"] = program_type
    state["stage"] = "entry_screening"
    save_state(state)
    return state
