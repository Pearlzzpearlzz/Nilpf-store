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


def _current_property_id() -> str:
    provider = current_app.config.get("BED_PROPERTY_ID_PROVIDER")
    if callable(provider):
        value = str(provider() or "").strip()
        if value:
            return value
    return "site:primary"


def _participant_rows() -> list[dict]:
    provider = current_app.config.get("BED_PARTICIPANT_PROVIDER")
    if not callable(provider):
        return []

    rows = provider()
    if not isinstance(rows, list):
        return []

    normalized = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue

        pid = row.get("pid")
        if pid is None:
            pid = row.get("participant_id")
        if pid is None:
            pid = index

        name = (
            row.get("name")
            or row.get("participant_name")
            or row.get("legal_name")
            or row.get("full_name")
            or row.get("preferred_name")
            or ""
        )

        normalized.append({
            "pid": str(pid),
            "name": str(name or "").strip(),
        })

    return normalized


def _participant_map() -> dict[str, str]:
    return {
        row["pid"]: row["name"]
        for row in _participant_rows()
    }


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
    # --------------------------------------------------------
    # PROPERTY-SCOPE MIGRATION
    # Existing TH~OS bed data belongs to the currently licensed
    # property. Future properties remain isolated.
    # --------------------------------------------------------
    facility_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(bed_facilities)")
    }
    if "property_id" not in facility_columns:
        conn.execute("ALTER TABLE bed_facilities ADD COLUMN property_id TEXT")

    assignment_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(bed_assignments)")
    }
    if "property_id" not in assignment_columns:
        conn.execute("ALTER TABLE bed_assignments ADD COLUMN property_id TEXT")

    property_id = _current_property_id()

    conn.execute(
        """UPDATE bed_facilities
           SET property_id=?
           WHERE property_id IS NULL OR TRIM(property_id)=''""",
        (property_id,),
    )

    conn.execute(
        """UPDATE bed_assignments
           SET property_id=?
           WHERE property_id IS NULL OR TRIM(property_id)=''""",
        (property_id,),
    )

    # Old participant uniqueness was global across every property.
    # Replace it with property-scoped uniqueness so PID 1 at
    # Property A does not collide with PID 1 at Property B.
    conn.execute("DROP INDEX IF EXISTS uq_bed_active_participant")
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_bed_active_participant_property
           ON bed_assignments(property_id, participant_id)
           WHERE unassigned_at IS NULL"""
    )

    conn.execute(
        """CREATE INDEX IF NOT EXISTS ix_bed_facility_property
           ON bed_facilities(property_id)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS ix_bed_assignment_property
           ON bed_assignments(property_id, assigned_at)"""
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

    # ============================================================
    # TH~OS BED MANAGEMENT RBAC GATE
    # Restricts scalable Bed Management to roles with bed_manage.
    # Keeps standalone Bed Management tests independent of app.py.
    # ============================================================
    permission_checker = current_app.config.get("BED_PERMISSION_CHECKER")

    if callable(permission_checker) and not permission_checker("bed_manage"):
        flash("You do not have permission to manage bed count or occupancy.")
        return redirect(url_for("operations"))


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

    property_id = _current_property_id()
    participant_names = _participant_map()

    conn = _db()

    facilities = conn.execute(
        """SELECT id, name
           FROM bed_facilities
           WHERE property_id=?
           ORDER BY name""",
        (property_id,),
    ).fetchall()

    if facility_id is None and facilities:
        facility_id = facilities[0]["id"]

    if facility_id is not None:
        valid_facility = conn.execute(
            """SELECT 1 FROM bed_facilities
               WHERE id=? AND property_id=?""",
            (facility_id, property_id),
        ).fetchone()
        if not valid_facility:
            facility_id = facilities[0]["id"] if facilities else None
            area_id = None

    areas = []
    if facility_id:
        areas = conn.execute(
            """SELECT a.id, a.name, COUNT(s.id) AS capacity
               FROM bed_areas a
               JOIN bed_facilities f ON f.id=a.facility_id
               LEFT JOIN bed_spaces s ON s.area_id=a.id
               WHERE a.facility_id=? AND f.property_id=?
               GROUP BY a.id
               ORDER BY a.name""",
            (facility_id, property_id),
        ).fetchall()

    conditions = [
        "a.facility_id = ?",
        "f.property_id = ?",
    ] if facility_id else ["1 = 0"]

    params: list[object] = (
        [facility_id, property_id] if facility_id else []
    )

    if area_id:
        valid_area = conn.execute(
            """SELECT 1
               FROM bed_areas a
               JOIN bed_facilities f ON f.id=a.facility_id
               WHERE a.id=? AND f.property_id=?""",
            (area_id, property_id),
        ).fetchone()

        if valid_area:
            conditions.append("a.id = ?")
            params.append(area_id)
        else:
            area_id = None

    if status in STATUSES:
        conditions.append("s.status = ?")
        params.append(status)

    if space_type in SPACE_TYPES:
        conditions.append("s.space_type = ?")
        params.append(space_type)

    where = " AND ".join(conditions)

    summary = conn.execute(
        """SELECT COUNT(s.id) AS capacity,
                  SUM(CASE WHEN s.status='Occupied' THEN 1 ELSE 0 END) AS occupied,
                  SUM(CASE WHEN s.status='Available' THEN 1 ELSE 0 END) AS available,
                  SUM(CASE WHEN s.status='Out of Service' THEN 1 ELSE 0 END) AS out_of_service
           FROM bed_spaces s
           JOIN bed_areas a ON a.id=s.area_id
           JOIN bed_facilities f ON f.id=a.facility_id
           WHERE a.facility_id=? AND f.property_id=?"""
        if facility_id else
        "SELECT 0 AS capacity, 0 AS occupied, 0 AS available, 0 AS out_of_service",
        (facility_id, property_id) if facility_id else (),
    ).fetchone()

    stats = {
        key: int(summary[key] or 0)
        for key in ("capacity", "occupied", "available", "out_of_service")
    }
    stats["occupancy"] = (
        round(stats["occupied"] / stats["capacity"] * 100, 1)
        if stats["capacity"] else 0
    )

    raw_spaces = conn.execute(
        f"""SELECT s.id, a.name AS area_name, s.label,
                   s.space_type, s.status, ba.participant_id
            FROM bed_spaces s
            JOIN bed_areas a ON a.id=s.area_id
            JOIN bed_facilities f ON f.id=a.facility_id
            LEFT JOIN bed_assignments ba
              ON ba.space_id=s.id
             AND ba.unassigned_at IS NULL
             AND ba.property_id=?
            WHERE {where}
            ORDER BY a.name COLLATE NOCASE,
                     s.label COLLATE NOCASE""",
        [property_id] + params,
    ).fetchall()

    spaces = []
    needle = query.lower()

    for row in raw_spaces:
        item = dict(row)
        pid = str(item.get("participant_id") or "")
        item["participant_name"] = participant_names.get(pid, "")

        if needle:
            haystack = " ".join([
                str(item.get("label") or ""),
                str(item.get("area_name") or ""),
                pid,
                item["participant_name"],
            ]).lower()

            if needle not in haystack:
                continue

        spaces.append(item)

    total = len(spaces)
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)

    offset = (page - 1) * per_page
    spaces = spaces[offset:offset + per_page]

    assigned_pids = {
        str(row["participant_id"])
        for row in conn.execute(
            """SELECT participant_id
               FROM bed_assignments
               WHERE property_id=?
                 AND unassigned_at IS NULL""",
            (property_id,),
        ).fetchall()
    }

    participants = [
        row
        for row in _participant_rows()
        if row["pid"] not in assigned_pids
    ]
    participants.sort(key=lambda row: row["name"].lower())

    conn.close()

    selected_facility = next(
        (f for f in facilities if f["id"] == facility_id),
        None,
    )

    return render_template(
        "bed_management.html",
        facilities=facilities,
        selected_facility=selected_facility,
        facility_id=facility_id,
        areas=areas,
        area_id=area_id,
        spaces=spaces,
        participants=participants,
        stats=stats,
        q=query,
        status=status,
        space_type=space_type,
        space_types=SPACE_TYPES,
        statuses=STATUSES,
        page=page,
        pages=pages,
        per_page=per_page,
        total=total,
    )


@bed_management.post("/bed-management/facilities")
def add_facility():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Facility/property name is required.", "error")
        return _redirect_dashboard()
    conn = _db()
    cur = conn.execute(
        """INSERT INTO bed_facilities(name, created_at, property_id)
           VALUES (?, ?, ?)""",
        (name, _now(), _current_property_id()),
    )
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

        owns_facility = conn.execute(
            """SELECT 1 FROM bed_facilities
               WHERE id=? AND property_id=?""",
            (facility_id, _current_property_id()),
        ).fetchone()

        if not owns_facility:
            conn.close()
            abort(404)

        cur = conn.execute(
            """INSERT INTO bed_areas(facility_id, name, created_at)
               VALUES (?, ?, ?)""",
            (facility_id, name, _now()),
        )
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
    row = conn.execute(
        """SELECT a.facility_id
           FROM bed_areas a
           JOIN bed_facilities f ON f.id=a.facility_id
           WHERE a.id=? AND f.property_id=?""",
        (area_id, _current_property_id()),
    ).fetchone()
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
    participant_id = (
        request.form.get("participant_id") or ""
    ).strip()

    if not participant_id:
        flash("Select a participant PID.", "error")
        return _redirect_dashboard()

    property_id = _current_property_id()
    participant_names = _participant_map()

    if participant_id not in participant_names:
        flash("The participant PID was not found for this property.", "error")
        return _redirect_dashboard()

    conn = _db()

    try:
        conn.execute("BEGIN IMMEDIATE")

        space = conn.execute(
            """SELECT s.status
               FROM bed_spaces s
               JOIN bed_areas a ON a.id=s.area_id
               JOIN bed_facilities f ON f.id=a.facility_id
               WHERE s.id=? AND f.property_id=?""",
            (space_id, property_id),
        ).fetchone()

        if not space:
            raise ValueError(
                "The space was not found for this property."
            )

        if space["status"] != "Available":
            raise ValueError(
                "Only available spaces can be assigned."
            )

        conn.execute(
            """INSERT INTO bed_assignments(
                   space_id,
                   participant_id,
                   assigned_at,
                   property_id
               )
               VALUES (?,?,?,?)""",
            (
                space_id,
                participant_id,
                _now(),
                property_id,
            ),
        )

        conn.execute(
            """UPDATE bed_spaces
               SET status='Occupied', updated_at=?
               WHERE id=?""",
            (_now(), space_id),
        )

        conn.commit()
        flash(
            f"PID {participant_id} assigned.",
            "success",
        )

    except (sqlite3.IntegrityError, ValueError) as exc:
        conn.rollback()
        message = str(exc)

        if isinstance(exc, sqlite3.IntegrityError):
            message = (
                "Assignment blocked: the space or participant "
                "is already assigned."
            )

        flash(message, "error")

    finally:
        conn.close()

    return _redirect_dashboard()


@bed_management.post("/bed-management/spaces/<int:space_id>/unassign")
def unassign_space(space_id: int):
    reason = (
        request.form.get("reason")
        or "Participant unassigned"
    ).strip()[:250]

    property_id = _current_property_id()
    conn = _db()
    conn.execute("BEGIN IMMEDIATE")

    assignment = conn.execute(
        """SELECT ba.id
           FROM bed_assignments ba
           JOIN bed_spaces s ON s.id=ba.space_id
           JOIN bed_areas a ON a.id=s.area_id
           JOIN bed_facilities f ON f.id=a.facility_id
           WHERE ba.space_id=?
             AND ba.property_id=?
             AND f.property_id=?
             AND ba.unassigned_at IS NULL""",
        (space_id, property_id, property_id),
    ).fetchone()

    if assignment:
        conn.execute(
            """UPDATE bed_assignments
               SET unassigned_at=?, unassigned_reason=?
               WHERE id=? AND property_id=?""",
            (
                _now(),
                reason,
                assignment["id"],
                property_id,
            ),
        )

        conn.execute(
            """UPDATE bed_spaces
               SET status='Available', updated_at=?
               WHERE id=?""",
            (_now(), space_id),
        )

        conn.commit()

        flash(
            "Participant unassigned; assignment history was preserved.",
            "success",
        )
    else:
        conn.rollback()
        flash(
            "No active assignment was found for this property.",
            "error",
        )

    conn.close()
    return _redirect_dashboard()


@bed_management.post("/bed-management/spaces/<int:space_id>/status")
def change_status(space_id: int):
    status = request.form.get("new_status") or ""
    if status not in ("Available", "Out of Service"):
        abort(400)

    property_id = _current_property_id()
    conn = _db()

    owned_space = conn.execute(
        """SELECT 1
           FROM bed_spaces s
           JOIN bed_areas a ON a.id=s.area_id
           JOIN bed_facilities f ON f.id=a.facility_id
           WHERE s.id=? AND f.property_id=?""",
        (space_id, property_id),
    ).fetchone()

    if not owned_space:
        conn.close()
        abort(404)

    active = conn.execute(
        """SELECT 1
           FROM bed_assignments
           WHERE space_id=?
             AND property_id=?
             AND unassigned_at IS NULL""",
        (space_id, property_id),
    ).fetchone()

    if active:
        flash(
            "Unassign the participant before changing this space's status.",
            "error",
        )
    else:
        conn.execute(
            """UPDATE bed_spaces
               SET status=?, updated_at=?
               WHERE id=?""",
            (status, _now(), space_id),
        )
        conn.commit()
        flash(f"Space marked {status}.", "success")

    conn.close()
    return _redirect_dashboard()


@bed_management.get("/bed-management/history")
def assignment_history():
    property_id = _current_property_id()
    participant_names = _participant_map()

    conn = _db()

    raw_rows = conn.execute(
        """SELECT ba.participant_id,
                  f.name AS facility_name,
                  a.name AS area_name,
                  s.label,
                  ba.assigned_at,
                  ba.unassigned_at,
                  ba.unassigned_reason
           FROM bed_assignments ba
           JOIN bed_spaces s ON s.id=ba.space_id
           JOIN bed_areas a ON a.id=s.area_id
           JOIN bed_facilities f ON f.id=a.facility_id
           WHERE ba.property_id=?
             AND f.property_id=?
           ORDER BY ba.assigned_at DESC
           LIMIT 500""",
        (property_id, property_id),
    ).fetchall()

    rows = []

    for row in raw_rows:
        item = dict(row)
        pid = str(item.get("participant_id") or "")
        item["participant_name"] = participant_names.get(pid, "")
        rows.append(item)

    conn.close()

    return render_template(
        "bed_history.html",
        rows=rows,
    )


def register_bed_management(app) -> None:
    app.register_blueprint(bed_management)
