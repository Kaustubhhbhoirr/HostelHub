"""
routes/complaints.py — Maintenance complaints for students and the warden.

Complaint workflow:

    Student submits  ->  Submitted
    Warden updates   ->  Acknowledged -> In Progress -> Resolved
                                     (or Rejected at any open step)

Each status change is saved in the complaints table and creates a
notification for the student.
"""

import sqlite3

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   send_from_directory, url_for)

from config import (COMPLAINT_CATEGORIES, COMPLAINT_NEXT_STATUSES, COMPLAINT_PRIORITIES,
                    COMPLAINT_STATUSES, OPEN_COMPLAINT_STATUSES)
from database import execute, get_db, now_str, query_all, query_one
from helpers import (complaint_image_exists, create_notification, delete_complaint_image,
                     get_active_allocation, notify_wardens, save_complaint_image)
from routes.auth import login_required, student_required, warden_required

complaints_bp = Blueprint("complaints", __name__)


def complaint_code(complaint_id):
    """CMP-0007 style reference shown to users."""
    return f"CMP-{complaint_id:04d}"


def get_complaint(complaint_id):
    """One complaint with the student's name and room label, or None."""
    return query_one(
        """SELECT complaints.*, users.name AS student_name, users.student_id AS roll_number,
                  users.phone AS student_phone, users.email AS student_email,
                  rooms.block, rooms.floor, rooms.room_number
           FROM complaints
           JOIN users ON users.id = complaints.student_id
           JOIN rooms ON rooms.id = complaints.room_id
           WHERE complaints.id = ?""",
        (complaint_id,),
    )


# ==================================================================
# STUDENT
# ==================================================================

@complaints_bp.route("/student/complaints")
@student_required
def student_list():
    status = request.args.get("status", "")
    sql = "SELECT * FROM complaints WHERE student_id = ?"
    params = [g.user["id"]]
    if status in COMPLAINT_STATUSES:
        sql += " AND status = ?"
        params.append(status)
    complaints = query_all(sql + " ORDER BY created_at DESC", tuple(params))

    # Count per status for the filter tabs.
    rows = query_all("SELECT status, COUNT(*) AS total FROM complaints WHERE student_id = ? GROUP BY status",
                     (g.user["id"],))
    counts = {row["status"]: row["total"] for row in rows}
    return render_template("complaints/student_list.html", complaints=complaints, status=status,
                           counts=counts, statuses=COMPLAINT_STATUSES)


@complaints_bp.route("/student/complaints/new", methods=["GET", "POST"])
@student_required
def student_new():
    allocation = get_active_allocation(g.user["id"])
    form = {"category": "", "priority": "Medium", "description": ""}

    if request.method == "POST":
        form = {
            "category": request.form.get("category", ""),
            "priority": request.form.get("priority", "Medium"),
            "description": request.form.get("description", "").strip(),
        }

        errors = []
        if allocation is None:
            errors.append("You need an allocated room before reporting a room issue.")
        if form["category"] not in COMPLAINT_CATEGORIES:
            errors.append("Please choose a category.")
        if form["priority"] not in COMPLAINT_PRIORITIES:
            errors.append("Please choose a valid priority.")
        if len(form["description"]) < 15:
            errors.append("Please describe the problem in at least 15 characters.")
        if len(form["description"]) > 1000:
            errors.append("Description must be under 1000 characters.")

        image_path = None
        if not errors:
            try:
                image_path = save_complaint_image(request.files.get("image"))
            except ValueError as error:
                errors.append(str(error))

        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            db = get_db()
            try:
                timestamp = now_str()
                # The room comes from the student's allocation on the server,
                # not from the form, so it cannot be faked.
                complaint_id = execute(
                    """INSERT INTO complaints (student_id, room_id, category, description, image_path,
                                               priority, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'Submitted', ?, ?)""",
                    (g.user["id"], allocation["room_id"], form["category"], form["description"],
                     image_path, form["priority"], timestamp, timestamp),
                )
                code = complaint_code(complaint_id)
                room_label = f"{allocation['block']}-{allocation['room_number']}"
                create_notification(g.user["id"], "Complaint submitted",
                                    f"Your {form['category']} complaint ({code}) has been sent to the warden.",
                                    "complaint", url_for("complaints.student_detail", complaint_id=complaint_id))
                notify_wardens(f"New {form['priority'].lower()} priority complaint",
                               f"{g.user['name']} reported a {form['category']} issue in {room_label} ({code}).",
                               "complaint", url_for("complaints.warden_detail", complaint_id=complaint_id))
                db.commit()
                flash("Maintenance complaint submitted successfully.", "success")
                return redirect(url_for("complaints.student_detail", complaint_id=complaint_id))
            except sqlite3.Error:
                db.rollback()
                delete_complaint_image(image_path)   # don't leave an unused file behind
                flash("Your complaint could not be saved. Please try again.", "danger")

    return render_template("complaints/new.html", allocation=allocation, form=form,
                           categories=COMPLAINT_CATEGORIES, priorities=COMPLAINT_PRIORITIES)


@complaints_bp.route("/student/complaints/<int:complaint_id>")
@student_required
def student_detail(complaint_id):
    complaint = get_complaint(complaint_id)
    # 404 (not 403) so a student cannot even learn that another student's complaint exists.
    if complaint is None or complaint["student_id"] != g.user["id"]:
        abort(404)
    return render_template("complaints/detail.html", complaint=complaint, steps=complaint_steps(complaint),
                           image_available=complaint_image_exists(complaint["image_path"]))


@complaints_bp.route("/complaints/<int:complaint_id>/image")
@login_required
def complaint_image(complaint_id):
    """Send a complaint photo, but only to someone allowed to see that complaint.

    Photos are stored outside /static, so this route is the ONLY way to open them:
      - the student who reported the complaint may see it
      - a warden may see it
      - anyone else gets 404 (knowing or guessing the URL is not enough)
    """
    complaint = query_one("SELECT student_id, image_path FROM complaints WHERE id = ?", (complaint_id,))
    if complaint is None or not complaint["image_path"]:
        abort(404)
    if g.user["role"] == "student" and complaint["student_id"] != g.user["id"]:
        abort(404)
    # send_from_directory refuses paths like "../app.py" and returns 404 if the file is missing.
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], complaint["image_path"])


# ==================================================================
# WARDEN
# ==================================================================

@complaints_bp.route("/warden/complaints")
@warden_required
def warden_list():
    filters = {
        "q": request.args.get("q", "").strip(),
        "status": request.args.get("status", ""),
        "category": request.args.get("category", ""),
        "priority": request.args.get("priority", ""),
        "block": request.args.get("block", ""),
    }

    conditions, params = [], []
    if filters["q"]:
        conditions.append("(users.name LIKE ? OR complaints.description LIKE ? OR rooms.room_number LIKE ?)")
        like = f"%{filters['q']}%"
        params.extend([like, like, like])
    if filters["status"] == "open":
        conditions.append(f"complaints.status IN ({', '.join('?' for _ in OPEN_COMPLAINT_STATUSES)})")
        params.extend(OPEN_COMPLAINT_STATUSES)
    elif filters["status"] in COMPLAINT_STATUSES:
        conditions.append("complaints.status = ?")
        params.append(filters["status"])
    if filters["category"] in COMPLAINT_CATEGORIES:
        conditions.append("complaints.category = ?")
        params.append(filters["category"])
    if filters["priority"] in COMPLAINT_PRIORITIES:
        conditions.append("complaints.priority = ?")
        params.append(filters["priority"])
    if filters["block"]:
        conditions.append("rooms.block = ?")
        params.append(filters["block"])
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    complaints = query_all(
        f"""SELECT complaints.*, users.name AS student_name, rooms.block, rooms.room_number
            FROM complaints
            JOIN users ON users.id = complaints.student_id
            JOIN rooms ON rooms.id = complaints.room_id
            {where}
            ORDER BY CASE complaints.status
                         WHEN 'Submitted' THEN 1 WHEN 'Acknowledged' THEN 2 WHEN 'In Progress' THEN 3
                         ELSE 4 END,
                     CASE complaints.priority WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
                     complaints.created_at DESC""",
        tuple(params),
    )

    rows = query_all("SELECT status, COUNT(*) AS total FROM complaints GROUP BY status")
    counts = {row["status"]: row["total"] for row in rows}
    counts["open"] = sum(counts.get(s, 0) for s in OPEN_COMPLAINT_STATUSES)
    blocks = [row["block"] for row in query_all("SELECT DISTINCT block FROM rooms ORDER BY block")]

    return render_template("complaints/warden_list.html", complaints=complaints, filters=filters,
                           counts=counts, statuses=COMPLAINT_STATUSES, categories=COMPLAINT_CATEGORIES,
                           priorities=COMPLAINT_PRIORITIES, blocks=blocks)


@complaints_bp.route("/warden/complaints/<int:complaint_id>")
@warden_required
def warden_detail(complaint_id):
    complaint = get_complaint(complaint_id)
    if complaint is None:
        abort(404)
    escalation = query_one(
        "SELECT * FROM college_maintenance_requests WHERE complaint_id = ? ORDER BY id DESC LIMIT 1",
        (complaint_id,),
    )
    return render_template("complaints/detail.html", complaint=complaint, steps=complaint_steps(complaint),
                           next_statuses=COMPLAINT_NEXT_STATUSES[complaint["status"]], escalation=escalation,
                           image_available=complaint_image_exists(complaint["image_path"]))


@complaints_bp.route("/warden/complaints/<int:complaint_id>/update", methods=["POST"])
@warden_required
def warden_update(complaint_id):
    complaint = get_complaint(complaint_id)
    if complaint is None:
        abort(404)

    new_status = request.form.get("status", complaint["status"])
    remarks = request.form.get("remarks", "").strip()
    detail_url = url_for("complaints.warden_detail", complaint_id=complaint_id)

    # Validation using the allowed-transitions dictionary from config.py.
    allowed = COMPLAINT_NEXT_STATUSES[complaint["status"]]
    if new_status != complaint["status"] and new_status not in allowed:
        flash(f"A complaint that is '{complaint['status']}' cannot be changed to '{new_status}'.", "danger")
        return redirect(detail_url)
    if new_status == complaint["status"] and remarks == (complaint["warden_remarks"] or ""):
        flash("Nothing to update: choose a new status or change the remarks.", "warning")
        return redirect(detail_url)
    if new_status == "Rejected" and not remarks:
        flash("Please add a remark explaining why the complaint is rejected.", "danger")
        return redirect(detail_url)
    if len(remarks) > 500:
        flash("Remarks must be under 500 characters.", "danger")
        return redirect(detail_url)

    db = get_db()
    try:
        timestamp = now_str()
        resolved_at = timestamp if new_status == "Resolved" else complaint["resolved_at"]
        execute(
            "UPDATE complaints SET status = ?, warden_remarks = ?, updated_at = ?, resolved_at = ? WHERE id = ?",
            (new_status, remarks or None, timestamp, resolved_at, complaint_id),
        )

        code = complaint_code(complaint_id)
        if new_status != complaint["status"]:
            title = f"Complaint {new_status.lower()}"
            message = f"Your {complaint['category']} complaint ({code}) is now {new_status}."
        else:
            title = "Warden added remarks"
            message = f"The warden updated remarks on your {complaint['category']} complaint ({code})."
        if remarks:
            message += f" Remarks: {remarks}"
        create_notification(complaint["student_id"], title, message, "complaint",
                            url_for("complaints.student_detail", complaint_id=complaint_id))
        db.commit()
        flash(f"Complaint {code} updated to {new_status}.", "success")
    except sqlite3.Error:
        db.rollback()
        flash("The complaint could not be updated. Please try again.", "danger")
    return redirect(detail_url)


# ------------------------------------------------------------------
# Shared helper for the progress tracker on the detail page
# ------------------------------------------------------------------

def complaint_steps(complaint):
    """Build the progress steps as a list of dictionaries for the template."""
    workflow = ["Submitted", "Acknowledged", "In Progress", "Resolved"]
    status = complaint["status"]

    if status == "Rejected":
        return [
            {"label": "Submitted", "state": "done"},
            {"label": "Rejected", "state": "rejected"},
        ]

    current_index = workflow.index(status)
    steps = []
    for index, label in enumerate(workflow):
        if index < current_index or status == "Resolved":
            state = "done"
        elif index == current_index:
            state = "current"
        else:
            state = "upcoming"
        steps.append({"label": label, "state": state})
    return steps
