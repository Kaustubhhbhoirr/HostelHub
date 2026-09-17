"""
routes/college.py — Maintenance / replacement requests from the warden to the college.

There is no separate "college" login yet, so the warden records the
college's response (status + college remarks) when it arrives.

Status flow:  Draft -> Sent to College -> Under Review -> Approved / Rejected -> Completed
"""

import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from config import COLLEGE_REQUEST_STATUSES, COLLEGE_REQUEST_TYPES, COMPLAINT_PRIORITIES
from database import execute, get_db, now_str, query_all, query_one
from helpers import create_notification
from routes.auth import warden_required

college_bp = Blueprint("college", __name__, url_prefix="/warden/college-requests")


def get_college_request_or_404(request_id):
    row = query_one(
        """SELECT cmr.*, users.name AS warden_name,
                  complaints.category AS complaint_category, complaints.student_id AS complaint_student_id
           FROM college_maintenance_requests AS cmr
           JOIN users ON users.id = cmr.warden_id
           LEFT JOIN complaints ON complaints.id = cmr.complaint_id
           WHERE cmr.id = ?""",
        (request_id,),
    )
    if row is None:
        abort(404)
    return row


def notify_student_about_escalation(complaint_id):
    """Tell the student that their complaint was forwarded to the college."""
    complaint = query_one("SELECT id, student_id, category FROM complaints WHERE id = ?", (complaint_id,))
    if complaint:
        create_notification(
            complaint["student_id"], "Complaint escalated to college",
            f"Your {complaint['category']} complaint (CMP-{complaint['id']:04d}) needs college approval "
            f"and has been forwarded to the college administration.",
            "complaint", url_for("complaints.student_detail", complaint_id=complaint["id"]),
        )


@college_bp.route("/")
@warden_required
def request_list():
    status = request.args.get("status", "")
    sql = """SELECT cmr.* FROM college_maintenance_requests AS cmr"""
    params = ()
    if status in COLLEGE_REQUEST_STATUSES:
        sql += " WHERE cmr.status = ?"
        params = (status,)
    college_requests = query_all(sql + " ORDER BY cmr.updated_at DESC", params)

    rows = query_all("SELECT status, COUNT(*) AS total FROM college_maintenance_requests GROUP BY status")
    counts = {row["status"]: row["total"] for row in rows}
    return render_template("college/list.html", college_requests=college_requests, status=status,
                           counts=counts, statuses=COLLEGE_REQUEST_STATUSES)


def read_college_form():
    data = {
        "request_type": request.form.get("request_type", ""),
        "asset": request.form.get("asset", "").strip(),
        "location": request.form.get("location", "").strip(),
        "quantity": request.form.get("quantity", "1").strip(),
        "priority": request.form.get("priority", "Medium"),
        "description": request.form.get("description", "").strip(),
        "complaint_id": request.form.get("complaint_id", "").strip(),
    }
    errors = []
    if data["request_type"] not in COLLEGE_REQUEST_TYPES:
        errors.append("Choose a request type.")
    if len(data["asset"]) < 2:
        errors.append("Enter the asset or item name, e.g. Ceiling Fan.")
    if len(data["location"]) < 3:
        errors.append("Enter the location, e.g. Block A, Room 204.")
    if not data["quantity"].isdigit() or not 1 <= int(data["quantity"]) <= 500:
        errors.append("Quantity must be a whole number between 1 and 500.")
    if data["priority"] not in COMPLAINT_PRIORITIES:
        errors.append("Choose a priority.")
    if len(data["description"]) < 15:
        errors.append("Describe the problem in at least 15 characters.")
    return data, errors


@college_bp.route("/new", methods=["GET", "POST"])
@college_bp.route("/<int:request_id>/edit", methods=["GET", "POST"], endpoint="request_edit")
@warden_required
def request_new(request_id=None):
    existing = get_college_request_or_404(request_id) if request_id else None
    if existing and existing["status"] != "Draft":
        flash("Only draft requests can be edited.", "warning")
        return redirect(url_for("college.request_detail", request_id=request_id))

    if existing:
        values = dict(existing)
    else:
        values = {"quantity": 1, "priority": "Medium", "request_type": "Repair"}
        # "Escalate" button on a complaint opens this form with ?complaint_id=...
        complaint_id = request.args.get("complaint_id", type=int)
        if complaint_id:
            complaint = query_one(
                """SELECT complaints.*, rooms.block, rooms.room_number FROM complaints
                   JOIN rooms ON rooms.id = complaints.room_id WHERE complaints.id = ?""",
                (complaint_id,),
            )
            if complaint:
                values.update({
                    "complaint_id": complaint["id"],
                    "asset": complaint["category"],
                    "location": f"Block {complaint['block']}, Room {complaint['room_number']}",
                    "priority": complaint["priority"],
                    "description": complaint["description"],
                })

    if request.method == "POST":
        data, errors = read_college_form()
        values.update(data)
        send_now = request.form.get("action") == "send"
        complaint_id = int(data["complaint_id"]) if data["complaint_id"].isdigit() else None

        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            db = get_db()
            status = "Sent to College" if send_now else "Draft"
            timestamp = now_str()
            try:
                if existing is None:
                    request_id = execute(
                        """INSERT INTO college_maintenance_requests
                           (warden_id, complaint_id, request_type, asset, location, quantity, description,
                            priority, status, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (g.user["id"], complaint_id, data["request_type"], data["asset"], data["location"],
                         int(data["quantity"]), data["description"], data["priority"], status,
                         timestamp, timestamp),
                    )
                else:
                    complaint_id = existing["complaint_id"]
                    execute(
                        """UPDATE college_maintenance_requests
                           SET request_type = ?, asset = ?, location = ?, quantity = ?, description = ?,
                               priority = ?, status = ?, updated_at = ?
                           WHERE id = ?""",
                        (data["request_type"], data["asset"], data["location"], int(data["quantity"]),
                         data["description"], data["priority"], status, timestamp, request_id),
                    )
                if send_now and complaint_id:
                    notify_student_about_escalation(complaint_id)
                db.commit()
                flash("Request sent to college." if send_now else "Request saved as draft.", "success")
                return redirect(url_for("college.request_detail", request_id=request_id))
            except sqlite3.Error:
                db.rollback()
                flash("The request could not be saved. Please try again.", "danger")

    return render_template("college/form.html", values=values, existing=existing,
                           request_types=COLLEGE_REQUEST_TYPES, priorities=COMPLAINT_PRIORITIES)


@college_bp.route("/<int:request_id>")
@warden_required
def request_detail(request_id):
    college_request = get_college_request_or_404(request_id)
    return render_template("college/detail.html", college_request=college_request,
                           statuses=COLLEGE_REQUEST_STATUSES)


@college_bp.route("/<int:request_id>/status", methods=["POST"])
@warden_required
def request_status(request_id):
    college_request = get_college_request_or_404(request_id)
    new_status = request.form.get("status", "")
    college_remarks = request.form.get("college_remarks", "").strip()

    if new_status not in COLLEGE_REQUEST_STATUSES:
        flash("Invalid status.", "danger")
        return redirect(url_for("college.request_detail", request_id=request_id))
    if new_status == "Draft" and college_request["status"] != "Draft":
        flash("A request that was already sent to the college cannot go back to Draft.", "warning")
        return redirect(url_for("college.request_detail", request_id=request_id))
    if len(college_remarks) > 1000:
        flash("College remarks must be under 1000 characters.", "danger")
        return redirect(url_for("college.request_detail", request_id=request_id))

    db = get_db()
    try:
        execute(
            "UPDATE college_maintenance_requests SET status = ?, college_remarks = ?, updated_at = ? WHERE id = ?",
            (new_status, college_remarks or None, now_str(), request_id),
        )
        # First time a draft is sent: let the student (if linked to a complaint) know.
        if college_request["status"] == "Draft" and new_status == "Sent to College" and college_request["complaint_id"]:
            notify_student_about_escalation(college_request["complaint_id"])
        db.commit()
        flash(f"Request status updated to {new_status}.", "success")
    except sqlite3.Error:
        db.rollback()
        flash("The status could not be updated. Please try again.", "danger")
    return redirect(url_for("college.request_detail", request_id=request_id))


@college_bp.route("/<int:request_id>/delete", methods=["POST"])
@warden_required
def request_delete(request_id):
    college_request = get_college_request_or_404(request_id)
    if college_request["status"] != "Draft":
        flash("Only drafts can be deleted. Sent requests are kept as an official record.", "warning")
        return redirect(url_for("college.request_detail", request_id=request_id))
    execute("DELETE FROM college_maintenance_requests WHERE id = ?", (request_id,))
    get_db().commit()
    flash("Draft request deleted.", "success")
    return redirect(url_for("college.request_list"))
