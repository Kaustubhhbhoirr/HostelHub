"""
routes/student.py — Student dashboard and "My Room" page.

Every route here uses @student_required, and every query uses the
logged-in student's own id (g.user["id"]). A student never passes their
id in the URL, so they cannot look at another student's data.
"""

from datetime import datetime

from flask import Blueprint, g, render_template

from config import OPEN_COMPLAINT_STATUSES
from database import query_all, query_one
from helpers import get_active_allocation
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


def get_room_beds(room_id):
    """All beds of one room, with the occupant's name if the bed is taken.

    LEFT JOIN keeps beds that have no active allocation (their name is NULL).
    """
    return query_all(
        """SELECT beds.id, beds.bed_number, beds.status,
                  users.id AS occupant_id, users.name AS occupant_name
           FROM beds
           LEFT JOIN allocations ON allocations.bed_id = beds.id AND allocations.status = 'active'
           LEFT JOIN users ON users.id = allocations.student_id
           WHERE beds.room_id = ?
           ORDER BY beds.bed_number""",
        (room_id,),
    )


def get_roommates(room_id, student_id):
    """Other students in the same room.

    Only non-private details are selected (no phone number or email).
    """
    return query_all(
        """SELECT users.name, users.department, users.year_of_study, beds.bed_number,
                  allocations.allocated_at
           FROM allocations
           JOIN users ON users.id = allocations.student_id
           JOIN beds  ON beds.id  = allocations.bed_id
           WHERE beds.room_id = ? AND allocations.status = 'active' AND users.id != ?
           ORDER BY beds.bed_number""",
        (room_id, student_id),
    )


@student_bp.route("/dashboard")
@student_required
def dashboard():
    student_id = g.user["id"]
    allocation = get_active_allocation(student_id)

    placeholders = ", ".join("?" for _ in OPEN_COMPLAINT_STATUSES)   # "?, ?, ?"
    active_complaints = query_one(
        f"SELECT COUNT(*) AS total FROM complaints WHERE student_id = ? AND status IN ({placeholders})",
        (student_id, *OPEN_COMPLAINT_STATUSES),
    )["total"]
    pending_requests = query_one(
        "SELECT COUNT(*) AS total FROM room_change_requests WHERE student_id = ? AND status = 'Pending'",
        (student_id,),
    )["total"]
    resolved_complaints = query_one(
        "SELECT COUNT(*) AS total FROM complaints WHERE student_id = ? AND status = 'Resolved'",
        (student_id,),
    )["total"]

    recent_complaints = query_all(
        "SELECT * FROM complaints WHERE student_id = ? ORDER BY created_at DESC LIMIT 4",
        (student_id,),
    )
    recent_notifications = query_all(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 5",
        (student_id,),
    )
    roommate_count = len(get_roommates(allocation["room_id"], student_id)) if allocation else 0

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
    beds, roommates = [], []
    if allocation:
        beds = get_room_beds(allocation["room_id"])
        roommates = get_roommates(allocation["room_id"], g.user["id"])

    # Count beds per status with a dictionary, e.g. {"occupied": 3, "available": 1}.
    bed_counts = {}
    for bed in beds:
        bed_counts[bed["status"]] = bed_counts.get(bed["status"], 0) + 1

    return render_template("student/room.html", allocation=allocation, beds=beds,
                           roommates=roommates, bed_counts=bed_counts)
