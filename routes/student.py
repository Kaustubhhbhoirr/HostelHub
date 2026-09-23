"""
routes/student.py — Student dashboard and "My Room" page.

Every route here uses @student_required, and every query uses the
logged-in student's own id (g.user["id"]). A student never passes their
id in the URL, so they cannot look at another student's data.
"""

from datetime import datetime

from flask import Blueprint, g, render_template

from config import OPEN_COMPLAINT_STATUSES
from data import get_store
from data.queries import (get_active_allocation, notifications_for, room_beds_with_occupants,
                          roommates, student_complaints)
from routes.auth import student_required

student_bp = Blueprint("student", __name__, url_prefix="/student")


def greeting_for_now():
    """Pick a greeting based on the current hour."""
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    elif hour < 17:
        return "Good afternoon"
    return "Good evening"


@student_bp.route("/dashboard")
@student_required
def dashboard():
    student_id = g.user["id"]
    allocation = get_active_allocation(student_id)

    store = get_store()
    complaints = store.find("complaints", student_id=student_id)
    active_complaints = sum(1 for row in complaints if row["status"] in OPEN_COMPLAINT_STATUSES)
    resolved_complaints = sum(1 for row in complaints if row["status"] == "Resolved")
    pending_requests = store.count("room_change_requests", student_id=student_id, status="Pending")

    recent_complaints = student_complaints(student_id)[:4]
    recent_notifications = notifications_for(student_id, limit=5)
    roommate_count = len(roommates(allocation["room_id"], student_id)) if allocation else 0

    # A dictionary groups the numbers shown in the stat cards.
    stats = {
        "active_complaints": active_complaints,
        "pending_requests": pending_requests,
        "resolved_complaints": resolved_complaints,
        "roommates": roommate_count,
    }
    return render_template(
        "student/dashboard.html",
        greeting=greeting_for_now(),
        allocation=allocation,
        stats=stats,
        recent_complaints=recent_complaints,
        recent_notifications=recent_notifications,
    )


@student_bp.route("/room")
@student_required
def my_room():
    allocation = get_active_allocation(g.user["id"])
    beds, room_mates = [], []
    if allocation:
        beds = room_beds_with_occupants(allocation["room_id"])
        room_mates = roommates(allocation["room_id"], g.user["id"])

    # Count beds per status with a dictionary, e.g. {"occupied": 3, "available": 1}.
    bed_counts = {}
    for bed in beds:
        bed_counts[bed["status"]] = bed_counts.get(bed["status"], 0) + 1

    return render_template("student/room.html", allocation=allocation, beds=beds,
                           roommates=room_mates, bed_counts=bed_counts)
