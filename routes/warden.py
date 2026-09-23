"""
routes/warden.py — Warden dashboard and Student management (CRUD).

CRUD on the users table:
    CREATE -> student_new()      INSERT INTO users
    READ   -> student_list(), student_detail()   SELECT
    UPDATE -> student_edit(), student_toggle_active()   UPDATE users
    DELETE -> student_delete()   DELETE FROM users
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import firebase_accounts
from config import STUDENT_EMAIL_DOMAIN, DEPARTMENTS
from data import DatabaseError, IntegrityError, get_store, now_str
from data.queries import (allocation_history, blocks as list_blocks, dashboard_data,
                          get_active_allocation, recent_complaints_with_names,
                          recent_requests_with_names, sort_rows, student_list as list_students)
from helpers import InvalidAllocationError, cancel_pending_room_requests, vacate_bed
from routes.auth import warden_required

warden_bp = Blueprint("warden", __name__, url_prefix="/warden")


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------

def percent(part, whole):
    """Safe percentage (avoids dividing by zero)."""
    return round(part * 100 / whole) if whole else 0


@warden_bp.route("/dashboard")
@warden_required
def dashboard():
    data = dashboard_data()

    stats = {
        "students": data["students"],
        "rooms": data["rooms"],
        "total_beds": data["total_beds"],
        "occupied_beds": data["occupied_beds"],
        "available_beds": data["available_beds"],
        "pending_complaints": data["pending_complaints"],
        "pending_requests": data["pending_requests"],
        "occupancy": percent(data["occupied_beds"], data["usable_beds"]),
    }

    # Occupancy of each block for the progress bars.
    blocks = [
        {"block": name, "total": totals["total"], "occupied": totals["occupied"],
         "percent": percent(totals["occupied"], totals["total"])}
        for name, totals in sorted(data["block_totals"].items())
    ]

    complaint_status_counts = data["complaint_status_counts"]
    recent_complaints = recent_complaints_with_names(limit=5)
    recent_requests = recent_requests_with_names(limit=4)

    # "Needs attention" list: each item is (icon, tone, title, count, link).
    pending_actions = [
        ("bi-inbox", "tone-amber", "New complaints to acknowledge",
         complaint_status_counts.get("Submitted", 0), url_for("complaints.warden_list", status="Submitted")),
        ("bi-exclamation-octagon", "tone-red", "High priority complaints open",
         data["high_priority_open"], url_for("complaints.warden_list", priority="High")),
        ("bi-arrow-left-right", "tone-indigo", "Room change requests to review",
         stats["pending_requests"], url_for("requests.warden_list")),
        ("bi-person-plus", "tone-green", "Students waiting for a bed",
         data["waiting_students"], url_for("warden.student_list", allocation="unallocated")),
        ("bi-bank", "tone-blue", "College requests awaiting response",
         data["college_awaiting"], url_for("college.request_list")),
    ]

    return render_template(
        "warden/dashboard.html",
        stats=stats, beds_by_status=data["beds_by_status"], blocks=blocks,
        complaint_status_counts=complaint_status_counts, top_categories=data["top_categories"],
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

    students = list_students(search=search, block=block, allocation_filter=allocation_filter)
    blocks = list_blocks()
    return render_template("warden/students.html", students=students, blocks=blocks,
                           search=search, block=block, allocation_filter=allocation_filter)


@warden_bp.route("/students/<int:student_id>")
@warden_required
def student_detail(student_id):
    student = get_student_or_404(student_id)
    allocation = get_active_allocation(student_id)
    store = get_store()
    history = allocation_history(student_id)
    complaints = sort_rows(store.find("complaints", student_id=student_id), "created_at", "id", reverse=True)
    room_requests = sort_rows(store.find("room_change_requests", student_id=student_id),
                              "created_at", "id", reverse=True)
    return render_template("warden/student_detail.html", student=student, allocation=allocation,
                           history=history, complaints=complaints, room_requests=room_requests)


def get_student_or_404(student_id):
    student = get_store().get("users", student_id)
    if student is None or student["role"] != "student":
        abort(404)
    return student


# ------------------------------------------------------------------
# Students — CREATE and UPDATE (they share one form)
# ------------------------------------------------------------------

def check_unique(store, data, student_id=None):
    """Email and student ID may be used by only one account (UNIQUE in the schema)."""
    for field, value in (("email", data["email"]), ("student_id", data["student_id"])):
        clash = store.first("users", **{field: value})
        if clash and clash["id"] != student_id:
            raise IntegrityError[0](f"users.{field} already used")


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
    """Turn a "value already used" error into a message a user understands."""
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
            store = get_store()
            created = False
            try:
                check_unique(store, data)
                # The sign-in account lives in Firebase Authentication; Firestore
                # keeps who the student is. Both are needed before they can log in.
                uid, created = firebase_accounts.create_account(data["email"], data["password"], data["name"])
                new_id = store.insert("users", {
                    "name": data["name"], "email": data["email"], "role": "student",
                    "student_id": data["student_id"], "phone": data["phone"] or None,
                    "department": data["department"], "year_of_study": int(data["year_of_study"]),
                    "is_active": 1, "firebase_uid": uid, "created_at": now_str(),
                })
                store.commit()
                flash(f"Student {data['name']} added. They can sign in with that email, "
                      f"with the password you set or with Google.", "success")
                return redirect(url_for("warden.student_detail", student_id=new_id))
            except IntegrityError as error:
                store.rollback()
                flash(friendly_integrity_message(error), "danger")
            except firebase_accounts.AccountError as error:
                store.rollback()
                flash(f"The sign-in account could not be created: {error}", "danger")
            except DatabaseError:
                store.rollback()
                if created:      # do not leave a sign-in account without a HostelHub record
                    firebase_accounts.delete_account(data["email"])
                flash("The student could not be saved. Please try again.", "danger")

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
            store = get_store()
            try:
                check_unique(store, data, student_id)
                changes = {"name": data["name"], "email": data["email"],
                           "student_id": data["student_id"], "phone": data["phone"] or None,
                           "department": data["department"], "year_of_study": int(data["year_of_study"])}
                if data["password"] or data["email"] != student["email"] or data["name"] != student["name"]:
                    # The sign-in account (Firebase Authentication) must follow the new
                    # email address; passwords live there, never in Firestore.
                    changes["firebase_uid"] = firebase_accounts.update_account(
                        student.get("firebase_uid"), email=data["email"],
                        password=data["password"] or None, name=data["name"])
                store.update("users", student_id, changes)
                store.commit()
                flash("Student details updated.", "success")
                return redirect(url_for("warden.student_detail", student_id=student_id))
            except IntegrityError as error:
                store.rollback()
                flash(friendly_integrity_message(error), "danger")
            except firebase_accounts.AccountError as error:
                store.rollback()
                flash(f"The sign-in account could not be updated: {error}", "danger")
            except DatabaseError:
                store.rollback()
                flash("The changes could not be saved. Please try again.", "danger")

    return render_template("warden/student_form.html", student=form_values, is_new=False,
                           departments=DEPARTMENTS)


# ------------------------------------------------------------------
# Students — deactivate / reactivate / DELETE
# ------------------------------------------------------------------

@warden_bp.route("/students/<int:student_id>/toggle-active", methods=["POST"])
@warden_required
def student_toggle_active(student_id):
    student = get_student_or_404(student_id)
    store = get_store()
    try:
        if student["is_active"]:
            # Deactivating frees the student's bed and cancels open requests.
            if get_active_allocation(student_id):
                vacate_bed(student_id)
            cancel_pending_room_requests(student_id, "Cancelled because the student account was deactivated.")
            store.update("users", student_id, {"is_active": 0})
            firebase_accounts.set_enabled(student["email"], False)   # also stop the Firebase sign-in
            message = f"{student['name']} has been deactivated and their bed released."
        else:
            store.update("users", student_id, {"is_active": 1})
            firebase_accounts.set_enabled(student["email"], True)
            message = f"{student['name']} has been reactivated."
        store.commit()
        flash(message, "success")
    except (InvalidAllocationError, *DatabaseError):   # * unpacks the tuple of database errors
        store.rollback()
        flash("Could not change the account status. Please try again.", "danger")
    return redirect(url_for("warden.student_detail", student_id=student_id))


@warden_bp.route("/students/<int:student_id>/delete", methods=["POST"])
@warden_required
def student_delete(student_id):
    student = get_student_or_404(student_id)
    store = get_store()
    try:
        # Complaints and room-change requests are the student's history: an account
        # with history is kept (and deactivated instead), exactly as before.
        if store.find("complaints", student_id=student_id) or \
                store.find("room_change_requests", student_id=student_id):
            raise IntegrityError[0]("student has history")
        if get_active_allocation(student_id):
            vacate_bed(student_id)
        for allocation in store.find("allocations", student_id=student_id):
            store.delete("allocations", allocation["id"])
        for notification in store.find("notifications", user_id=student_id):
            store.delete("notifications", notification["id"])
        store.delete("users", student_id)
        store.commit()
        firebase_accounts.delete_account(student["email"])   # remove the sign-in account too
        flash(f"Student {student['name']} was deleted.", "success")
        return redirect(url_for("warden.student_list"))
    except IntegrityError:
        store.rollback()
        flash("This student has complaint or request history and cannot be deleted. Deactivate the account instead.",
              "warning")
    except (InvalidAllocationError, *DatabaseError):   # * unpacks the tuple of database errors
        store.rollback()
        flash("Could not delete the student. Please try again.", "danger")
    return redirect(url_for("warden.student_detail", student_id=student_id))
