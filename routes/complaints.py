"""
routes/complaints.py — Maintenance complaints for students and the warden.

Complaint workflow:

    Student submits  ->  Submitted
    Warden updates   ->  Acknowledged -> In Progress -> Resolved
                                     (or Rejected at any open step)

Each status change is saved in the complaints table and creates a
notification for the student.
"""

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from config import (COMPLAINT_CATEGORIES, COMPLAINT_NEXT_STATUSES, COMPLAINT_PRIORITIES,
                    COMPLAINT_STATUSES, OPEN_COMPLAINT_STATUSES)
from data import DatabaseError, get_store, now_str
from data.queries import (blocks as list_blocks, complaint_status_counts, complaint_with_details,
                          latest_college_request_for_complaint, student_complaints, warden_complaint_list)
from helpers import (PhotosDisabled, complaint_image_exists, create_notification, delete_complaint_image,
                     get_active_allocation, notify_wardens, save_complaint_image)
from routes.auth import login_required, student_required, warden_required
from storage import images_enabled, read_file

complaints_bp = Blueprint("complaints", __name__)

# Content type sent to the browser for each allowed photo extension.
IMAGE_MIME_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "gif": "image/gif", "webp": "image/webp"}


def complaint_code(complaint_id):
    """CMP-0007 style reference shown to users."""
    return f"CMP-{complaint_id:04d}"


def get_complaint(complaint_id):
    """One complaint with the student's name and room label, or None."""
    return complaint_with_details(complaint_id)


# ==================================================================
# STUDENT
# ==================================================================

@complaints_bp.route("/student/complaints")
@student_required
def student_list():
    status = request.args.get("status", "")
    complaints = student_complaints(g.user["id"], status)
    # Count per status for the filter tabs.
    counts = complaint_status_counts(g.user["id"])
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
        photo_skipped = False
        if not errors:
            try:
                image_path = save_complaint_image(request.files.get("image"))
            except PhotosDisabled:
                photo_skipped = True        # the photo is optional: save the complaint without it
            except ValueError as error:
                errors.append(str(error))

        if errors:
            for message in errors:
                flash(message, "danger")
        else:
            store = get_store()
            try:
                timestamp = now_str()
                # The room comes from the student's allocation on the server,
                # not from the form, so it cannot be faked.
                complaint_id = store.insert("complaints", {
                    "student_id": g.user["id"], "room_id": allocation["room_id"],
                    "category": form["category"], "description": form["description"],
                    "image_path": image_path, "priority": form["priority"], "status": "Submitted",
                    "warden_remarks": None, "created_at": timestamp, "updated_at": timestamp,
                    "resolved_at": None,
                })
                code = complaint_code(complaint_id)
                room_label = f"{allocation['block']}-{allocation['room_number']}"
                create_notification(g.user["id"], "Complaint submitted",
                                    f"Your {form['category']} complaint ({code}) has been sent to the warden.",
                                    "complaint", url_for("complaints.student_detail", complaint_id=complaint_id))
                notify_wardens(f"New {form['priority'].lower()} priority complaint",
                               f"{g.user['name']} reported a {form['category']} issue in {room_label} ({code}).",
                               "complaint", url_for("complaints.warden_detail", complaint_id=complaint_id))
                store.commit()
                flash("Maintenance complaint submitted successfully.", "success")
                if photo_skipped:
                    flash("Photo uploads are not available on this server, so the photo was not attached.",
                          "warning")
                return redirect(url_for("complaints.student_detail", complaint_id=complaint_id))
            except DatabaseError:
                store.rollback()
                delete_complaint_image(image_path)   # don't leave an unused file behind
                flash("Your complaint could not be saved. Please try again.", "danger")

    return render_template("complaints/new.html", allocation=allocation, form=form,
                           categories=COMPLAINT_CATEGORIES, priorities=COMPLAINT_PRIORITIES,
                           photos_enabled=images_enabled())


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

    Photos are never in a public folder (see storage.py), so this route is the ONLY
    way to open them:
      - the student who reported the complaint may see it
      - a warden may see it
      - anyone else gets 404 (knowing or guessing the URL is not enough)
    """
    complaint = get_store().get("complaints", complaint_id)
    if complaint is None or not complaint["image_path"]:
        abort(404)
    if g.user["role"] == "student" and complaint["student_id"] != g.user["id"]:
        abort(404)
    # read_file() refuses names like "../app.py" and returns None if the photo is missing.
    photo = read_file(complaint["image_path"])
    if photo is None:
        abort(404)
    extension = complaint["image_path"].rsplit(".", 1)[-1].lower()
    response = Response(photo, mimetype=IMAGE_MIME_TYPES.get(extension, "application/octet-stream"))
    # "private": browsers may cache it for this user, but shared caches (CDNs) must not.
    response.headers["Cache-Control"] = "private, max-age=300"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


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

    complaints = warden_complaint_list(search=filters["q"], status=filters["status"],
                                       category=filters["category"], priority=filters["priority"],
                                       block=filters["block"])
    counts = complaint_status_counts()
    counts["open"] = sum(counts.get(status, 0) for status in OPEN_COMPLAINT_STATUSES)
    blocks = list_blocks()

    return render_template("complaints/warden_list.html", complaints=complaints, filters=filters,
                           counts=counts, statuses=COMPLAINT_STATUSES, categories=COMPLAINT_CATEGORIES,
                           priorities=COMPLAINT_PRIORITIES, blocks=blocks)


@complaints_bp.route("/warden/complaints/<int:complaint_id>")
@warden_required
def warden_detail(complaint_id):
    complaint = get_complaint(complaint_id)
    if complaint is None:
        abort(404)
    escalation = latest_college_request_for_complaint(complaint_id)
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

    store = get_store()
    try:
        timestamp = now_str()
        resolved_at = timestamp if new_status == "Resolved" else complaint["resolved_at"]
        store.update("complaints", complaint_id, {
            "status": new_status, "warden_remarks": remarks or None,
            "updated_at": timestamp, "resolved_at": resolved_at,
        })

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
        store.commit()
        flash(f"Complaint {code} updated to {new_status}.", "success")
    except DatabaseError:
        store.rollback()
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
