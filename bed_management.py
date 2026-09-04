"""Scalable bed/space inventory and occupancy workflow for TH~OS."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for


bed_management = Blueprint("bed_management", __name__)

SPACE_TYPES = (
    "Private Room",
    "Shared Room Bed",
    "Dorm Bed",
    "Common Area Space",
    "Temporary Space",
)
STATUSES = ("Available", "Occupied", "Out of Service")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(current_app.config.get("BED_DB_PATH", "licenses.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_bed_management_db() -> None:
    conn = _db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bed_facilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bed_areas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            facility_id INTEGER NOT NULL REFERENCES bed_facilities(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(facility_id, name)
        );

        CREATE TABLE IF NOT EXISTS bed_spaces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_id INTEGER NOT NULL REFERENCES bed_areas(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            space_type TEXT NOT NULL CHECK(space_type IN (
                'Private Room','Shared Room Bed','Dorm Bed','Common Area Space','Temporary Space'
            )),
            status TEXT NOT NULL DEFAULT 'Available' CHECK(status IN (
                'Available','Occupied','Out of Service'
            )),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(area_id, label)
        );

        CREATE TABLE IF NOT EXISTS bed_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            space_id INTEGER NOT NULL REFERENCES bed_spaces(id) ON DELETE RESTRICT,
            participant_id TEXT NOT NULL,
            assigned_at TEXT NOT NULL,
            unassigned_at TEXT,
            unassigned_reason TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_bed_active_space
            ON bed_assignments(space_id) WHERE unassigned_at IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_bed_active_participant
            ON bed_assignments(participant_id) WHERE unassigned_at IS NULL;
        CREATE INDEX IF NOT EXISTS ix_bed_spaces_area_status
            ON bed_spaces(area_id, status);
        CREATE INDEX IF NOT EXISTS ix_bed_assignment_history
            ON bed_assignments(participant_id, assigned_at);
        """
    )
    conn.commit()
    conn.close()


def _participant_expression(conn: sqlite3.Connection) -> str:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(participants)")}
    for name in ("legal_name", "full_name", "participant_name", "preferred_name"):
        if name in columns:
            return f"p.{name}"
    return "''"


def _redirect_dashboard(**overrides):
    args = {key: request.form.get(key) for key in ("facility_id", "area_id", "q", "status", "space_type", "page", "per_page")}
    args.update(overrides)
    return redirect(url_for("bed_management.dashboard", **{k: v for k, v in args.items() if v not in (None, "")}))


@bed_management.before_request
def _ensure_schema():
    init_bed_management_db()


@bed_management.get("/bed-management")
def dashboard():
    facility_id = request.args.get("facility_id", type=int)
    area_id = request.args.get("area_id", type=int)
    query = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    space_type = (request.args.get("space_type") or "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in (20, 50, 100):
        per_page = 20

    conn = _db()
    facilities = conn.execute("SELECT id, name FROM bed_facilities ORDER BY name").fetchall()
    if facility_id is None and facilities:
        facility_id = facilities[0]["id"]

    areas = []
    if facility_id:
        areas = conn.execute(
            """SELECT a.id, a.name, COUNT(s.id) AS capacity
               FROM bed_areas a LEFT JOIN bed_spaces s ON s.area_id=a.id
               WHERE a.facility_id=? GROUP BY a.id ORDER BY a.name""",
            (facility_id,),
        ).fetchall()

    conditions = ["a.facility_id = ?"] if facility_id else ["1 = 0"]
    params: list[object] = [facility_id] if facility_id else []
    if area_id:
        conditions.append("a.id = ?")
        params.append(area_id)
    if status in STATUSES:
        conditions.append("s.status = ?")
        params.append(status)
    if space_type in SPACE_TYPES:
        conditions.append("s.space_type = ?")
        params.append(space_type)
    has_participants = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='participants'"
    ).fetchone() is not None
    participant_name = _participant_expression(conn) if has_participants else "''"
    participant_join = "LEFT JOIN participants p ON CAST(p.id AS TEXT)=ba.participant_id" if has_participants else ""
    if query:
        conditions.append(f"(s.label LIKE ? OR a.name LIKE ? OR ba.participant_id LIKE ? OR {participant_name} LIKE ?)")
        needle = f"%{query}%"
        params.extend([needle, needle, needle, needle])
    where = " AND ".join(conditions)

    summary = conn.execute(
        """SELECT COUNT(s.id) AS capacity,
                  SUM(CASE WHEN s.status='Occupied' THEN 1 ELSE 0 END) AS occupied,
                  SUM(CASE WHEN s.status='Available' THEN 1 ELSE 0 END) AS available,
                  SUM(CASE WHEN s.status='Out of Service' THEN 1 ELSE 0 END) AS out_of_service
           FROM bed_spaces s JOIN bed_areas a ON a.id=s.area_id
           WHERE a.facility_id=?""" if facility_id else
        "SELECT 0 AS capacity, 0 AS occupied, 0 AS available, 0 AS out_of_service",
        (facility_id,) if facility_id else (),
    ).fetchone()
    stats = {key: int(summary[key] or 0) for key in ("capacity", "occupied", "available", "out_of_service")}
    stats["occupancy"] = round((stats["occupied"] / stats["capacity"] * 100), 1) if stats["capacity"] else 0

    total = conn.execute(
        f"""SELECT COUNT(*) FROM bed_spaces s
             JOIN bed_areas a ON a.id=s.area_id
             LEFT JOIN bed_assignments ba ON ba.space_id=s.id AND ba.unassigned_at IS NULL
             {participant_join}
             WHERE {where}""", params,
    ).fetchone()[0]
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    spaces = conn.execute(
        f"""SELECT s.id, a.name AS area_name, s.label, s.space_type, s.status,
                    ba.participant_id, {participant_name} AS participant_name
             FROM bed_spaces s
             JOIN bed_areas a ON a.id=s.area_id
             LEFT JOIN bed_assignments ba ON ba.space_id=s.id AND ba.unassigned_at IS NULL
             {participant_join}
             WHERE {where}
             ORDER BY a.name COLLATE NOCASE, s.label COLLATE NOCASE
             LIMIT ? OFFSET ?""", params + [per_page, (page - 1) * per_page],
    ).fetchall()

    participants = []
    if has_participants:
        participants = conn.execute(
            f"""SELECT CAST(p.id AS TEXT) AS pid, {participant_name} AS name
                 FROM participants p
                 WHERE NOT EXISTS (
                    SELECT 1 FROM bed_assignments ba
                    WHERE ba.participant_id=CAST(p.id AS TEXT) AND ba.unassigned_at IS NULL
                 ) ORDER BY name COLLATE NOCASE"""
        ).fetchall()
    conn.close()

    selected_facility = next((f for f in facilities if f["id"] == facility_id), None)
    return render_template(
        "bed_management.html", facilities=facilities, selected_facility=selected_facility,
        facility_id=facility_id, areas=areas, area_id=area_id, spaces=spaces,
        participants=participants, stats=stats, q=query, status=status,
        space_type=space_type, space_types=SPACE_TYPES, statuses=STATUSES,
        page=page, pages=pages, per_page=per_page, total=total,
    )


@bed_management.post("/bed-management/facilities")
def add_facility():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Facility/property name is required.", "error")
        return _redirect_dashboard()
    conn = _db()
    cur = conn.execute("INSERT INTO bed_facilities(name, created_at) VALUES (?, ?)", (name, _now()))
    conn.commit()
    facility_id = cur.lastrowid
    conn.close()
    flash("Facility/property added.", "success")
    return _redirect_dashboard(facility_id=facility_id)


@bed_management.post("/bed-management/areas")
def add_area():
    facility_id = request.form.get("facility_id", type=int)
    name = (request.form.get("name") or "").strip()
    if not facility_id or not name:
        flash("Select a facility and enter an area or room name.", "error")
        return _redirect_dashboard()
    try:
        conn = _db()
        cur = conn.execute("INSERT INTO bed_areas(facility_id, name, created_at) VALUES (?, ?, ?)", (facility_id, name, _now()))
        conn.commit()
        area_id = cur.lastrowid
        conn.close()
        flash("Area/room added.", "success")
        return _redirect_dashboard(facility_id=facility_id, area_id=area_id)
    except sqlite3.IntegrityError:
        flash("That area/room already exists for this facility.", "error")
        return _redirect_dashboard()


@bed_management.post("/bed-management/spaces/generate")
def generate_spaces():
    area_id = request.form.get("area_id", type=int)
    count = request.form.get("count", type=int)
    prefix = (request.form.get("prefix") or "Bed").strip()[:40]
    start = request.form.get("start", 1, type=int)
    space_type = request.form.get("space_type") or "Dorm Bed"
    if not area_id or not count or count < 1 or count > 400 or start < 1 or space_type not in SPACE_TYPES:
        flash("Choose an area and generate between 1 and 400 valid spaces at a time.", "error")
        return _redirect_dashboard()
    conn = _db()
    row = conn.execute("SELECT facility_id FROM bed_areas WHERE id=?", (area_id,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    now = _now()
    created = 0
    for number in range(start, start + count):
        label = f"{prefix} {number:03d}" if count >= 100 or start >= 100 else f"{prefix} {number}"
        try:
            conn.execute(
                "INSERT INTO bed_spaces(area_id,label,space_type,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (area_id, label, space_type, "Available", now, now),
            )
            created += 1
        except sqlite3.IntegrityError:
            continue
    conn.commit()
    conn.close()
    flash(f"Generated {created} sleeping space{'s' if created != 1 else ''}.", "success")
    return _redirect_dashboard(facility_id=row["facility_id"], area_id=area_id, page=1)


@bed_management.post("/bed-management/spaces/<int:space_id>/assign")
def assign_space(space_id: int):
    participant_id = (request.form.get("participant_id") or "").strip()
    if not participant_id:
        flash("Select a participant PID.", "error")
        return _redirect_dashboard()
    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        space = conn.execute("SELECT status FROM bed_spaces WHERE id=?", (space_id,)).fetchone()
        has_participants = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='participants'"
        ).fetchone()
        participant = conn.execute(
            "SELECT 1 FROM participants WHERE CAST(id AS TEXT)=?", (participant_id,)
        ).fetchone() if has_participants else None
        if not space or not participant:
            raise ValueError("The space or participant PID was not found.")
        if space["status"] != "Available":
            raise ValueError("Only available spaces can be assigned.")
        conn.execute(
            "INSERT INTO bed_assignments(space_id,participant_id,assigned_at) VALUES (?,?,?)",
            (space_id, participant_id, _now()),
        )
        conn.execute("UPDATE bed_spaces SET status='Occupied', updated_at=? WHERE id=?", (_now(), space_id))
        conn.commit()
        flash(f"PID {participant_id} assigned.", "success")
    except (sqlite3.IntegrityError, ValueError) as exc:
        conn.rollback()
        message = str(exc)
        if isinstance(exc, sqlite3.IntegrityError):
            message = "Assignment blocked: the space or participant is already assigned."
        flash(message, "error")
    finally:
        conn.close()
    return _redirect_dashboard()


@bed_management.post("/bed-management/spaces/<int:space_id>/unassign")
def unassign_space(space_id: int):
    reason = (request.form.get("reason") or "Participant unassigned").strip()[:250]
    conn = _db()
    conn.execute("BEGIN IMMEDIATE")
    assignment = conn.execute(
        "SELECT id FROM bed_assignments WHERE space_id=? AND unassigned_at IS NULL", (space_id,)
    ).fetchone()
    if assignment:
        conn.execute(
            "UPDATE bed_assignments SET unassigned_at=?, unassigned_reason=? WHERE id=?",
            (_now(), reason, assignment["id"]),
        )
        conn.execute("UPDATE bed_spaces SET status='Available', updated_at=? WHERE id=?", (_now(), space_id))
        conn.commit()
        flash("Participant unassigned; assignment history was preserved.", "success")
    else:
        conn.rollback()
        flash("No active assignment was found.", "error")
    conn.close()
    return _redirect_dashboard()


@bed_management.post("/bed-management/spaces/<int:space_id>/status")
def change_status(space_id: int):
    status = request.form.get("new_status") or ""
    if status not in ("Available", "Out of Service"):
        abort(400)
    conn = _db()
    active = conn.execute(
        "SELECT 1 FROM bed_assignments WHERE space_id=? AND unassigned_at IS NULL", (space_id,)
    ).fetchone()
    if active:
        flash("Unassign the participant before changing this space's status.", "error")
    else:
        conn.execute("UPDATE bed_spaces SET status=?, updated_at=? WHERE id=?", (status, _now(), space_id))
        conn.commit()
        flash(f"Space marked {status}.", "success")
    conn.close()
    return _redirect_dashboard()


@bed_management.get("/bed-management/history")
def assignment_history():
    conn = _db()
    has_participants = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='participants'"
    ).fetchone() is not None
    participant_name = _participant_expression(conn) if has_participants else "''"
    participant_join = "LEFT JOIN participants p ON CAST(p.id AS TEXT)=ba.participant_id" if has_participants else ""
    rows = conn.execute(
        f"""SELECT ba.participant_id, {participant_name} AS participant_name,
                    f.name AS facility_name, a.name AS area_name, s.label,
                    ba.assigned_at, ba.unassigned_at, ba.unassigned_reason
             FROM bed_assignments ba JOIN bed_spaces s ON s.id=ba.space_id
             JOIN bed_areas a ON a.id=s.area_id JOIN bed_facilities f ON f.id=a.facility_id
             {participant_join}
             ORDER BY ba.assigned_at DESC LIMIT 500"""
    ).fetchall()
    conn.close()
    return render_template("bed_history.html", rows=rows)


def register_bed_management(app) -> None:
    app.register_blueprint(bed_management)
