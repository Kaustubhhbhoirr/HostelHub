"""
routes/warden.py — Warden dashboard and Student management (CRUD).

CRUD on the users table:
    CREATE -> student_new()      INSERT INTO users
    READ   -> student_list(), student_detail()   SELECT
    UPDATE -> student_edit(), student_toggle_active()   UPDATE users
    DELETE -> student_delete()   DELETE FROM users
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from config import STUDENT_EMAIL_DOMAIN, DEPARTMENTS, OPEN_COMPLAINT_STATUSES
from database import DatabaseError, IntegrityError, execute, get_db, now_str, query_all, query_one
from helpers import (InvalidAllocationError, cancel_pending_room_requests,
                     get_active_allocation, vacate_bed)
from routes.auth import warden_required

warden_bp = Blueprint("warden", __name__, url_prefix="/warden")


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------

def count(sql, params=()):
    """Run a SELECT COUNT(*) AS total query and return just the number."""
    return query_one(sql, params)["total"]


def percent(part, whole):
    """Safe percentage (avoids dividing by zero)."""
    return round(part * 100 / whole) if whole else 0


@warden_bp.route("/dashboard")
@warden_required
def dashboard():
    # Bed counts per status -> dictionary like {"occupied": 67, "available": 24, ...}
    bed_rows = query_all("SELECT status, COUNT(*) AS total FROM beds GROUP BY status")
    beds_by_status = {row["status"]: row["total"] for row in bed_rows}

    total_beds = sum(beds_by_status.values())
    # Beds in closed rooms (unavailable) cannot be used, so they are not counted for occupancy.
    usable_beds = total_beds - beds_by_status.get("unavailable", 0)
    occupied_beds = beds_by_status.get("occupied", 0)

    placeholders = ", ".join("?" for _ in OPEN_COMPLAINT_STATUSES)
    stats = {
        "students": count("SELECT COUNT(*) AS total FROM users WHERE role = 'student' AND is_active = 1"),
        "rooms": count("SELECT COUNT(*) AS total FROM rooms WHERE status != 'inactive'"),
        "total_beds": total_beds,
        "occupied_beds": occupied_beds,
        "available_beds": beds_by_status.get("available", 0),
        "pending_complaints": count(
            f"SELECT COUNT(*) AS total FROM complaints WHERE status IN ({placeholders})", OPEN_COMPLAINT_STATUSES),
        "pending_requests": count("SELECT COUNT(*) AS total FROM room_change_requests WHERE status = 'Pending'"),
        "occupancy": percent(occupied_beds, usable_beds),
    }

    # Occupancy of each block for the progress bars.
    block_rows = query_all(
        """SELECT rooms.block,
                  COUNT(beds.id) AS total,
                  SUM(CASE WHEN beds.status = 'occupied' THEN 1 ELSE 0 END) AS occupied
           FROM beds JOIN rooms ON rooms.id = beds.room_id
           WHERE beds.status != 'unavailable'
           GROUP BY rooms.block ORDER BY rooms.block"""
    )
    blocks = [
        {"block": row["block"], "total": row["total"], "occupied": row["occupied"],
         "percent": percent(row["occupied"], row["total"])}
        for row in block_rows
    ]

    # Complaint counts per status, in the order of the workflow.
    status_rows = query_all("SELECT status, COUNT(*) AS total FROM complaints GROUP BY status")
    complaint_status_counts = {row["status"]: row["total"] for row in status_rows}
    top_categories = query_all(
        f"""SELECT category, COUNT(*) AS total FROM complaints
            WHERE status IN ({placeholders})
            GROUP BY category ORDER BY total DESC LIMIT 5""",
        OPEN_COMPLAINT_STATUSES,
    )

    recent_complaints = query_all(
        """SELECT complaints.*, users.name AS student_name, rooms.block, rooms.room_number
           FROM complaints
           JOIN users ON users.id = complaints.student_id
           JOIN rooms ON rooms.id = complaints.room_id
           ORDER BY complaints.created_at DESC LIMIT 5"""
    )
    recent_requests = query_all(
        """SELECT room_change_requests.*, users.name AS student_name
           FROM room_change_requests JOIN users ON users.id = room_change_requests.student_id
           ORDER BY room_change_requests.created_at DESC LIMIT 4"""
    )

    # "Needs attention" list: each item is (icon, tone, title, count, link).
    pending_actions = [
        ("bi-inbox", "tone-amber", "New complaints to acknowledge",
         complaint_status_counts.get("Submitted", 0), url_for("complaints.warden_list", status="Submitted")),
        ("bi-exclamation-octagon", "tone-red", "High priority complaints open",
         count(f"SELECT COUNT(*) AS total FROM complaints WHERE priority = 'High' AND status IN ({placeholders})",
               OPEN_COMPLAINT_STATUSES),
         url_for("complaints.warden_list", priority="High")),
        ("bi-arrow-left-right", "tone-indigo", "Room change requests to review",
         stats["pending_requests"], url_for("requests.warden_list")),
        ("bi-person-plus", "tone-green", "Students waiting for a bed",
         count("""SELECT COUNT(*) AS total FROM users
                  WHERE role = 'student' AND is_active = 1
                    AND id NOT IN (SELECT student_id FROM allocations WHERE status = 'active')"""),
         url_for("warden.student_list", allocation="unallocated")),
        ("bi-bank", "tone-blue", "College requests awaiting response",
         count("SELECT COUNT(*) AS total FROM college_maintenance_requests WHERE status IN ('Sent to College', 'Under Review')"),
         url_for("college.request_list")),
    ]

    return render_template(
        "warden/dashboard.html",
        stats=stats, beds_by_status=beds_by_status, blocks=blocks,
        complaint_status_counts=complaint_status_counts, top_categories=top_categories,
        recent_complaints=recent_complaints, recent_requests=recent_requests,
        pending_actions=pending_actions,
    )


# ------------------------------------------------------------------
# Students — READ
# ------------------------------------------------------------------

@warden_bp.route("/students")
@warden_required
def student_list():
    search = request.args.get("q", "").strip()
    block = request.args.get("block", "")
    allocation_filter = request.args.get("allocation", "")   # allocated / unallocated / inactive

    # Build the WHERE clause step by step. Only fixed SQL text is joined;
    # every user-typed value goes into `params`, never into the SQL string.
    conditions = ["users.role = 'student'"]
    params = []
    if search:
        # LOWER() on both sides makes the search ignore capital letters in SQLite AND PostgreSQL.
        conditions.append("(LOWER(users.name) LIKE ? OR LOWER(users.email) LIKE ? OR LOWER(users.student_id) LIKE ?)")
        like = f"%{search.lower()}%"
        params.extend([like, like, like])
    if block:
        conditions.append("rooms.block = ?")
        params.append(block)
    if allocation_filter == "allocated":
        conditions.append("allocations.id IS NOT NULL AND users.is_active = 1")
    elif allocation_filter == "unallocated":
        conditions.append("allocations.id IS NULL AND users.is_active = 1")
    elif allocation_filter == "inactive":
        conditions.append("users.is_active = 0")

    students = query_all(
        f"""SELECT users.*, rooms.block, rooms.room_number, beds.bed_number
            FROM users
            LEFT JOIN allocations ON allocations.student_id = users.id AND allocations.status = 'active'
            LEFT JOIN beds  ON beds.id  = allocations.bed_id
            LEFT JOIN rooms ON rooms.id = beds.room_id
            WHERE {' AND '.join(conditions)}
            ORDER BY users.is_active DESC, users.name""",
        tuple(params),
    )
    blocks = [row["block"] for row in query_all("SELECT DISTINCT block FROM rooms ORDER BY block")]
    return render_template("warden/students.html", students=students, blocks=blocks,
                           search=search, block=block, allocation_filter=allocation_filter)


@warden_bp.route("/students/<int:student_id>")
@warden_required
def student_detail(student_id):
    student = get_student_or_404(student_id)
    allocation = get_active_allocation(student_id)
    history = query_all(
        """SELECT allocations.*, beds.bed_number, rooms.block, rooms.room_number
           FROM allocations JOIN beds ON beds.id = allocations.bed_id JOIN rooms ON rooms.id = beds.room_id
           WHERE allocations.student_id = ? ORDER BY allocations.allocated_at DESC""",
        (student_id,),
    )
    complaints = query_all("SELECT * FROM complaints WHERE student_id = ? ORDER BY created_at DESC", (student_id,))
    room_requests = query_all("SELECT * FROM room_change_requests WHERE student_id = ? ORDER BY created_at DESC",
                              (student_id,))
    return render_template("warden/student_detail.html", student=student, allocation=allocation,
                           history=history, complaints=complaints, room_requests=room_requests)


def get_student_or_404(student_id):
    student = query_one("SELECT * FROM users WHERE id = ? AND role = 'student'", (student_id,))
    if student is None:
        abort(404)
    return student


# ------------------------------------------------------------------
# Students — CREATE and UPDATE (they share one form)
# ------------------------------------------------------------------

def read_student_form(is_new):
    """Read + validate the student form. Returns (data_dict, list_of_errors)."""
    form = request.form
    data = {
        "name": form.get("name", "").strip(),
        "email": form.get("email", "").strip().lower(),
        "student_id": form.get("student_id", "").strip().upper(),
        "phone": form.get("phone", "").strip(),
        "department": form.get("department", ""),
        "year_of_study": form.get("year_of_study", ""),
        "password": form.get("password", ""),
    }
    errors = []
    if len(data["name"]) < 3:
        errors.append("Please enter the student's full name.")
    # Students must use their MES student Google account, e.g. name@student.mes.ac.in.
    # The part before the domain must be a plain name ("a@b@..." or just "@student.mes.ac.in" are refused).
    local_part = data["email"].removesuffix(STUDENT_EMAIL_DOMAIN)
    if (not data["email"].endswith(STUDENT_EMAIL_DOMAIN) or not local_part
            or "@" in local_part or " " in data["email"]):
        errors.append(f"Student email must be an MES college address ending with {STUDENT_EMAIL_DOMAIN}.")
    if not data["student_id"]:
        errors.append("Student ID is required.")
    if data["phone"] and not (data["phone"].isdigit() and len(data["phone"]) == 10):
        errors.append("Phone number must be exactly 10 digits.")
    if data["department"] not in DEPARTMENTS:
        errors.append("Please choose a department.")
    if data["year_of_study"] not in ("1", "2", "3", "4"):
        errors.append("Please choose the year of study.")
    # Password is required for new students; when editing, blank means "keep the old one".
    if (is_new or data["password"]) and len(data["password"]) < 8:
        errors.append("Password must be at least 8 characters long.")
    return data, errors


def friendly_integrity_message(error):
    """Turn a UNIQUE constraint error into a message a user understands."""
    # SQLite says "users.student_id", PostgreSQL says "users_student_id_key".
    text = str(error)
    if "student_id" in text:
        return "Another student already has this student ID."
    if "email" in text:
        return "Another account already uses this email address."
    return "This change conflicts with existing records."


@warden_bp.route("/students/new", methods=["GET", "POST"])
@warden_required
def student_new():
    data = {}
    if request.method == "POST":
        data, errors = read_student_form(is_new=True)
        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            try:
                new_id = execute(
                    """INSERT INTO users (name, email, password_hash, role, student_id, phone,
                                          department, year_of_study, is_active, created_at)
                       VALUES (?, ?, ?, 'student', ?, ?, ?, ?, 1, ?)""",
                    (data["name"], data["email"], generate_password_hash(data["password"]),
                     data["student_id"], data["phone"] or None, data["department"],
                     int(data["year_of_study"]), now_str()),
                )
                get_db().commit()
                flash(f"Student {data['name']} added. You can now allocate a bed.", "success")
                return redirect(url_for("warden.student_detail", student_id=new_id))
            except IntegrityError as error:
                get_db().rollback()
                flash(friendly_integrity_message(error), "danger")

    return render_template("warden/student_form.html", student=data, is_new=True, departments=DEPARTMENTS)


@warden_bp.route("/students/<int:student_id>/edit", methods=["GET", "POST"])
@warden_required
def student_edit(student_id):
    student = get_student_or_404(student_id)
    form_values = dict(student)   # database row -> normal dictionary

    if request.method == "POST":
        data, errors = read_student_form(is_new=False)
        form_values.update(data)
        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            try:
                execute(
                    """UPDATE users SET name = ?, email = ?, student_id = ?, phone = ?,
                                        department = ?, year_of_study = ?
                       WHERE id = ?""",
                    (data["name"], data["email"], data["student_id"], data["phone"] or None,
                     data["department"], int(data["year_of_study"]), student_id),
                )
                if data["password"]:
                    execute("UPDATE users SET password_hash = ? WHERE id = ?",
                            (generate_password_hash(data["password"]), student_id))
                get_db().commit()
                flash("Student details updated.", "success")
                return redirect(url_for("warden.student_detail", student_id=student_id))
            except IntegrityError as error:
                get_db().rollback()
                flash(friendly_integrity_message(error), "danger")

    return render_template("warden/student_form.html", student=form_values, is_new=False,
                           departments=DEPARTMENTS)


# ------------------------------------------------------------------
# Students — deactivate / reactivate / DELETE
# ------------------------------------------------------------------

@warden_bp.route("/students/<int:student_id>/toggle-active", methods=["POST"])
@warden_required
def student_toggle_active(student_id):
    student = get_student_or_404(student_id)
    db = get_db()
    try:
        if student["is_active"]:
            # Deactivating frees the student's bed and cancels open requests.
            if get_active_allocation(student_id):
                vacate_bed(student_id)
            cancel_pending_room_requests(student_id, "Cancelled because the student account was deactivated.")
            execute("UPDATE users SET is_active = 0 WHERE id = ?", (student_id,))
            message = f"{student['name']} has been deactivated and their bed released."
        else:
            execute("UPDATE users SET is_active = 1 WHERE id = ?", (student_id,))
            message = f"{student['name']} has been reactivated."
        db.commit()
        flash(message, "success")
    except (InvalidAllocationError, *DatabaseError):   # * unpacks the tuple of database errors
        db.rollback()
        flash("Could not change the account status. Please try again.", "danger")
    return redirect(url_for("warden.student_detail", student_id=student_id))


@warden_bp.route("/students/<int:student_id>/delete", methods=["POST"])
@warden_required
def student_delete(student_id):
    student = get_student_or_404(student_id)
    db = get_db()
    try:
        if get_active_allocation(student_id):
            vacate_bed(student_id)
        # Allocation history and notifications are removed automatically (ON DELETE CASCADE).
        execute("DELETE FROM users WHERE id = ?", (student_id,))
        db.commit()
        flash(f"Student {student['name']} was deleted.", "success")
        return redirect(url_for("warden.student_list"))
    except IntegrityError:
        # Complaints and room requests point to this student (foreign keys),
        # so SQLite refuses the DELETE. Keeping history is safer anyway.
        db.rollback()
        flash("This student has complaint or request history and cannot be deleted. Deactivate the account instead.",
              "warning")
    except (InvalidAllocationError, *DatabaseError):   # * unpacks the tuple of database errors
        db.rollback()
        flash("Could not delete the student. Please try again.", "danger")
    return redirect(url_for("warden.student_detail", student_id=student_id))
