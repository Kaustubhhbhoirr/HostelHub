"""
routes/requests.py — Room change requests.

Students can never move themselves. They create a request:

    Student request  ->  Pending   (the preferred bed, if any, becomes "reserved")
    Warden approves  ->  Approved  (student is moved: old bed free, new bed occupied)
    Warden rejects   ->  Rejected  (reserved bed becomes available again)
    Student cancels  ->  Cancelled (reserved bed becomes available again)
"""

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from config import ROOM_CHANGE_REASONS, ROOM_CHANGE_STATUSES
from database import DatabaseError, execute, get_db, now_str, query_all, query_one
from helpers import (InvalidAllocationError, bed_label, create_notification, get_active_allocation,
                     get_bed, move_student, notify_wardens, release_reserved_bed)
from routes.auth import student_required, warden_required

requests_bp = Blueprint("requests", __name__)


def get_available_beds():
    """Every free bed in an active room, for the bed dropdowns."""
    return query_all(
        """SELECT beds.id, beds.bed_number, rooms.block, rooms.floor, rooms.room_number, rooms.capacity
           FROM beds JOIN rooms ON rooms.id = beds.room_id
           WHERE beds.status = 'available' AND rooms.status = 'active'
           ORDER BY rooms.block, rooms.floor, rooms.room_number, beds.bed_number"""
    )


def group_beds_by_block(beds):
    """{'A': [bed, bed], 'B': [...]} so the dropdown can use <optgroup> per block."""
    groups = {}
    for bed in beds:
        groups.setdefault(bed["block"], []).append(bed)
    return groups


def get_request(request_id):
    """One request with the student and both bed labels (LEFT JOIN because beds may be NULL)."""
    return query_one(
        """SELECT rcr.*, users.name AS student_name, users.student_id AS roll_number,
                  users.department, users.year_of_study,
                  cur_bed.bed_number AS current_bed_number, cur_room.block AS current_block,
                  cur_room.room_number AS current_room_number, cur_room.floor AS current_floor,
                  req_bed.bed_number AS requested_bed_number, req_bed.status AS requested_bed_status,
                  req_room.block AS requested_block, req_room.room_number AS requested_room_number,
                  req_room.floor AS requested_floor,
                  new_bed.bed_number AS assigned_bed_number, new_room.block AS assigned_block,
                  new_room.room_number AS assigned_room_number
           FROM room_change_requests AS rcr
           JOIN users ON users.id = rcr.student_id
           JOIN beds  AS cur_bed  ON cur_bed.id  = rcr.current_bed_id
           JOIN rooms AS cur_room ON cur_room.id = cur_bed.room_id
           LEFT JOIN beds  AS req_bed  ON req_bed.id  = rcr.requested_bed_id
           LEFT JOIN rooms AS req_room ON req_room.id = req_bed.room_id
           LEFT JOIN beds  AS new_bed  ON new_bed.id  = rcr.assigned_bed_id
           LEFT JOIN rooms AS new_room ON new_room.id = new_bed.room_id
           WHERE rcr.id = ?""",
        (request_id,),
    )


def request_code(request_id):
    return f"RCR-{request_id:04d}"


# ==================================================================
# STUDENT
# ==================================================================

@requests_bp.route("/student/room-requests")
@student_required
def student_list():
    request_ids = query_all(
        "SELECT id FROM room_change_requests WHERE student_id = ? ORDER BY created_at DESC", (g.user["id"],))
    # Reuse get_request() so every card has the full bed/room labels.
    room_requests = [get_request(row["id"]) for row in request_ids]
    has_pending = any(r["status"] == "Pending" for r in room_requests)
    return render_template("requests/student_list.html", room_requests=room_requests, has_pending=has_pending,
                           allocation=get_active_allocation(g.user["id"]))


@requests_bp.route("/student/room-requests/new", methods=["GET", "POST"])
@student_required
def student_new():
    allocation = get_active_allocation(g.user["id"])
    if allocation is None:
        flash("You need a room allocation before you can request a room change.", "warning")
        return redirect(url_for("requests.student_list"))

    pending = query_one("SELECT id FROM room_change_requests WHERE student_id = ? AND status = 'Pending'",
                        (g.user["id"],))
    if pending:
        flash("You already have a pending room change request. Wait for the warden's decision or cancel it.",
              "warning")
        return redirect(url_for("requests.student_list"))

    form = {"reason": "", "requested_bed_id": "", "details": ""}
    if request.method == "POST":
        form = {
            "reason": request.form.get("reason", ""),
            "requested_bed_id": request.form.get("requested_bed_id", ""),
            "details": request.form.get("details", "").strip(),
        }
        requested_bed_id = int(form["requested_bed_id"]) if form["requested_bed_id"].isdigit() else None

        errors = []
        if form["reason"] not in ROOM_CHANGE_REASONS:
            errors.append("Please choose a reason for the room change.")
        if len(form["details"]) < 20:
            errors.append("Please explain your request in at least 20 characters.")
        if requested_bed_id is not None:
            bed = get_bed(requested_bed_id)
            if bed is None or bed["status"] != "available" or bed["room_status"] != "active":
                errors.append("The bed you selected is no longer available. Please pick another one.")

        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            db = get_db()
            try:
                timestamp = now_str()
                request_id = execute(
                    """INSERT INTO room_change_requests (student_id, current_bed_id, requested_bed_id, reason,
                                                         details, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 'Pending', ?, ?)""",
                    (g.user["id"], allocation["bed_id"], requested_bed_id, form["reason"], form["details"],
                     timestamp, timestamp),
                )
                if requested_bed_id is not None:
                    # Hold the bed so nobody else is allocated to it while the warden decides.
                    execute("UPDATE beds SET status = 'reserved' WHERE id = ? AND status = 'available'",
                            (requested_bed_id,))
                notify_wardens("New room change request",
                               f"{g.user['name']} ({allocation['block']}-{allocation['room_number']}) "
                               f"requested a room change: {form['reason']}.",
                               "room_request", url_for("requests.warden_detail", request_id=request_id))
                db.commit()
                flash("Room change request submitted.", "success")
                return redirect(url_for("requests.student_list"))
            except DatabaseError:
                db.rollback()
                flash("Your request could not be saved. Please try again.", "danger")

    available_beds = [bed for bed in get_available_beds() if bed["id"] != allocation["bed_id"]]
    return render_template("requests/new.html", allocation=allocation, form=form, reasons=ROOM_CHANGE_REASONS,
                           beds_by_block=group_beds_by_block(available_beds))


@requests_bp.route("/student/room-requests/<int:request_id>/cancel", methods=["POST"])
@student_required
def student_cancel(request_id):
    room_request = query_one("SELECT * FROM room_change_requests WHERE id = ? AND student_id = ?",
                             (request_id, g.user["id"]))
    if room_request is None:
        abort(404)
    if room_request["status"] != "Pending":
        flash("Only pending requests can be cancelled.", "warning")
        return redirect(url_for("requests.student_list"))

    db = get_db()
    release_reserved_bed(room_request["requested_bed_id"])
    execute("UPDATE room_change_requests SET status = 'Cancelled', updated_at = ? WHERE id = ?",
            (now_str(), request_id))
    db.commit()
    flash("Room change request cancelled.", "success")
    return redirect(url_for("requests.student_list"))


# ==================================================================
# WARDEN
# ==================================================================

@requests_bp.route("/warden/room-requests")
@warden_required
def warden_list():
    status = request.args.get("status", "Pending")
    if status not in ROOM_CHANGE_STATUSES and status != "all":
        status = "Pending"

    sql = "SELECT id FROM room_change_requests"
    params = ()
    if status != "all":
        sql += " WHERE status = ?"
        params = (status,)
    rows = query_all(sql + " ORDER BY created_at DESC", params)
    room_requests = [get_request(row["id"]) for row in rows]

    count_rows = query_all("SELECT status, COUNT(*) AS total FROM room_change_requests GROUP BY status")
    counts = {row["status"]: row["total"] for row in count_rows}
    counts["all"] = sum(counts.values())
    return render_template("requests/warden_list.html", room_requests=room_requests, status=status,
                           counts=counts, statuses=ROOM_CHANGE_STATUSES)


@requests_bp.route("/warden/room-requests/<int:request_id>")
@warden_required
def warden_detail(request_id):
    room_request = get_request(request_id)
    if room_request is None:
        abort(404)

    # Beds the warden may assign: all free beds, plus the bed reserved for THIS request.
    beds = get_available_beds()
    if room_request["status"] == "Pending" and room_request["requested_bed_id"] \
            and room_request["requested_bed_status"] == "reserved":
        beds = [get_bed(room_request["requested_bed_id"])] + list(beds)

    return render_template("requests/warden_detail.html", room_request=room_request,
                           beds_by_block=group_beds_by_block(beds))


@requests_bp.route("/warden/room-requests/<int:request_id>/decide", methods=["POST"])
@warden_required
def warden_decide(request_id):
    room_request = get_request(request_id)
    if room_request is None:
        abort(404)
    detail_url = url_for("requests.warden_detail", request_id=request_id)

    if room_request["status"] != "Pending":
        flash("This request has already been processed.", "warning")
        return redirect(detail_url)

    action = request.form.get("action")
    remarks = request.form.get("remarks", "").strip()
    code = request_code(request_id)
    db = get_db()

    if action == "reject":
        if not remarks:
            flash("Please add a remark explaining why the request is rejected.", "danger")
            return redirect(detail_url)
        try:
            release_reserved_bed(room_request["requested_bed_id"])
            execute("UPDATE room_change_requests SET status = 'Rejected', warden_remarks = ?, updated_at = ? WHERE id = ?",
                    (remarks, now_str(), request_id))
            create_notification(room_request["student_id"], "Room change request rejected",
                                f"Your request {code} was rejected. Remarks: {remarks}",
                                "room_request", url_for("requests.student_list"))
            db.commit()
            flash("Room change request rejected.", "success")
        except DatabaseError:
            db.rollback()
            flash("The request could not be updated. Please try again.", "danger")
        return redirect(detail_url)

    if action != "approve":
        flash("Unknown action.", "danger")
        return redirect(detail_url)

    new_bed_id = request.form.get("bed_id", type=int)
    if not new_bed_id:
        flash("Choose the bed the student should move to.", "danger")
        return redirect(detail_url)

    try:
        # The student's own reserved bed may be used; any other bed must be available.
        uses_reserved_bed = new_bed_id == room_request["requested_bed_id"]
        if room_request["requested_bed_id"] and not uses_reserved_bed:
            release_reserved_bed(room_request["requested_bed_id"])

        # move_student(): old allocation ended, old bed available, new bed occupied.
        new_bed = move_student(room_request["student_id"], new_bed_id, allow_reserved=uses_reserved_bed)

        execute(
            """UPDATE room_change_requests
               SET status = 'Approved', assigned_bed_id = ?, warden_remarks = ?, updated_at = ?
               WHERE id = ?""",
            (new_bed_id, remarks or None, now_str(), request_id),
        )
        message = f"You have been moved to {bed_label(new_bed)}."
        if remarks:
            message += f" Remarks: {remarks}"
        create_notification(room_request["student_id"], "Room change approved", message,
                            "room_request", url_for("student.my_room"))
        db.commit()   # all changes above are saved together
        flash(f"Room change approved. {room_request['student_name']} moved to {bed_label(new_bed)}.", "success")
    except InvalidAllocationError as error:
        db.rollback()  # undo everything, including the released reservation
        flash(str(error), "danger")
    except DatabaseError:
        db.rollback()
        flash("The room change could not be completed. No changes were saved.", "danger")
    return redirect(detail_url)
