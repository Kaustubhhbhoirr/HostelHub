"""
data/queries.py — Everything that reads more than one collection.

A SQL database can join tables itself. Firestore cannot, so HostelHub does the
joining here, in ordinary Python, and both stores give exactly the same answers.
The hostel is small (about a hundred beds), so reading a collection and matching
the rows in Python is fast enough in both modes.

Every function returns plain dictionaries with the same keys the templates used
before, for example bed["student_name"] or complaint["block"].
"""

from config import COMPLAINT_PRIORITIES, COMPLAINT_STATUSES, OPEN_COMPLAINT_STATUSES
from data import get_store
from data.store import sort_rows


# ------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------

def by_id(rows):
    return {row["id"]: row for row in rows}


def active_allocations():
    return get_store().find("allocations", status="active")


def allocation_by_student():
    return {row["student_id"]: row for row in active_allocations()}


def allocation_by_bed():
    return {row["bed_id"]: row for row in active_allocations()}


def counts_by(rows, field):
    """{value: how many rows have it}, like SELECT field, COUNT(*) GROUP BY field."""
    totals = {}
    for row in rows:
        totals[row[field]] = totals.get(row[field], 0) + 1
    return totals


def room_label_fields(room):
    """The room columns that list pages show next to a bed."""
    return {"block": room["block"], "floor": room["floor"], "room_number": room["room_number"],
            "capacity": room["capacity"], "room_status": room["status"]}


# ------------------------------------------------------------------
# Beds, rooms and allocations
# ------------------------------------------------------------------

def get_bed(bed_id):
    """A bed together with its room details, or None."""
    store = get_store()
    bed = store.get("beds", bed_id)
    if bed is None:
        return None
    room = store.get("rooms", bed["room_id"])
    result = {"id": bed["id"], "bed_number": bed["bed_number"], "status": bed["status"],
              "room_id": bed["room_id"]}
    if room:
        result.update({"block": room["block"], "floor": room["floor"],
                       "room_number": room["room_number"], "room_status": room["status"]})
    return result


def get_active_allocation(student_id):
    """The student's current bed and room, or None if they have no bed."""
    store = get_store()
    allocation = store.first("allocations", student_id=student_id, status="active")
    if allocation is None:
        return None
    bed = store.get("beds", allocation["bed_id"])
    room = store.get("rooms", bed["room_id"]) if bed else None
    if bed is None or room is None:
        return None
    return {"allocation_id": allocation["id"], "allocated_at": allocation["allocated_at"],
            "bed_id": bed["id"], "bed_number": bed["bed_number"],
            "room_id": room["id"], **room_label_fields(room)}


def occupant_of_bed(bed_id):
    """The student who currently holds this bed, or None."""
    store = get_store()
    allocation = store.first("allocations", bed_id=bed_id, status="active")
    return store.get("users", allocation["student_id"]) if allocation else None


def room_beds_with_occupants(room_id):
    """All beds of one room with the occupant's name (None when the bed is free)."""
    store = get_store()
    occupants = allocation_by_bed()
    users = by_id(store.find("users", role="student"))
    beds = []
    for bed in sort_rows(store.find("beds", room_id=room_id), "bed_number"):
        allocation = occupants.get(bed["id"])
        student = users.get(allocation["student_id"]) if allocation else None
        beds.append({**bed,
                     "occupant_id": student["id"] if student else None,
                     "occupant_name": student["name"] if student else None})
    return beds


def roommates(room_id, student_id):
    """Other students in the same room. Phone numbers and emails are never included."""
    store = get_store()
    beds = by_id(store.find("beds", room_id=room_id))
    users = by_id(store.find("users", role="student"))
    mates = []
    for allocation in active_allocations():
        if allocation["bed_id"] in beds and allocation["student_id"] != student_id:
            student = users.get(allocation["student_id"])
            if student:
                mates.append({"name": student["name"], "department": student["department"],
                              "year_of_study": student["year_of_study"],
                              "bed_number": beds[allocation["bed_id"]]["bed_number"],
                              "allocated_at": allocation["allocated_at"]})
    return sort_rows(mates, "bed_number")


def blocks():
    return sorted({room["block"] for room in get_store().all("rooms")})


def floors(block):
    return sorted({room["floor"] for room in get_store().find("rooms", block=block)})


def rooms_on_floor(block, floor):
    return sort_rows(get_store().find("rooms", block=block, floor=floor), "room_number")


def bed_map_rows(block, floor):
    """Beds of one floor with their occupant and, for reserved beds, who is waiting."""
    store = get_store()
    rooms = by_id(rooms_on_floor(block, floor))
    if not rooms:
        return []
    users = by_id(store.all("users"))
    occupants = allocation_by_bed()
    reserved_for = {}
    for request_row in store.find("room_change_requests", status="Pending"):
        if request_row.get("requested_bed_id"):
            student = users.get(request_row["student_id"])
            reserved_for[request_row["requested_bed_id"]] = student["name"] if student else None

    rows = []
    for bed in store.all("beds"):
        if bed["room_id"] not in rooms:
            continue
        allocation = occupants.get(bed["id"])
        student = users.get(allocation["student_id"]) if allocation else None
        rows.append({**bed,
                     "student_id": student["id"] if student else None,
                     "student_name": student["name"] if student else None,
                     "roll_number": student["student_id"] if student else None,
                     "department": student["department"] if student else None,
                     "allocated_at": allocation["allocated_at"] if allocation else None,
                     "reserved_for": reserved_for.get(bed["id"])})
    return sort_rows(rows, "bed_number")


def unallocated_students():
    """Active students who have no bed (the map's allocate dropdown)."""
    store = get_store()
    allocated = set(allocation_by_student())
    students = [student for student in store.find("users", role="student", is_active=1)
                if student["id"] not in allocated]
    return sort_rows([{"id": s["id"], "name": s["name"], "student_id": s["student_id"],
                       "department": s["department"], "year_of_study": s["year_of_study"]}
                      for s in students], "name")


def free_beds():
    """Every available bed in an active room, with its room details."""
    store = get_store()
    rooms = by_id(store.find("rooms", status="active"))
    beds = []
    for bed in store.find("beds", status="available"):
        room = rooms.get(bed["room_id"])
        if room:
            beds.append({"id": bed["id"], "bed_number": bed["bed_number"], "block": room["block"],
                         "floor": room["floor"], "room_number": room["room_number"],
                         "capacity": room["capacity"]})
    return sort_rows(beds, "block", "floor", "room_number", "bed_number")


def room_list(search="", block="", status=""):
    """Rooms with their bed totals, filtered like the room list page."""
    store = get_store()
    beds_by_room = {}
    for bed in store.all("beds"):
        beds_by_room.setdefault(bed["room_id"], []).append(bed)

    needle = search.upper().replace("-", "").replace(" ", "")
    rooms = []
    for room in store.all("rooms"):
        if needle and needle not in f"{room['block']}{room['room_number']}".upper():
            continue
        if block and room["block"] != block:
            continue
        if status and room["status"] != status:
            continue
        beds = beds_by_room.get(room["id"], [])
        rooms.append({**room, "bed_count": len(beds),
                      "occupied": sum(1 for bed in beds if bed["status"] == "occupied"),
                      "available": sum(1 for bed in beds if bed["status"] == "available")})
    return sort_rows(rooms, "block", "floor", "room_number")


def allocation_history(student_id):
    """Every allocation of one student, newest first, with the bed and room."""
    store = get_store()
    beds = by_id(store.all("beds"))
    rooms = by_id(store.all("rooms"))
    rows = []
    for allocation in store.find("allocations", student_id=student_id):
        bed = beds.get(allocation["bed_id"])
        room = rooms.get(bed["room_id"]) if bed else None
        rows.append({**allocation,
                     "bed_number": bed["bed_number"] if bed else None,
                     "block": room["block"] if room else None,
                     "room_number": room["room_number"] if room else None})
    return sort_rows(rows, "allocated_at", reverse=True)


# ------------------------------------------------------------------
# Students
# ------------------------------------------------------------------

def student_list(search="", block="", allocation_filter=""):
    """The warden's student list with its search box and filters."""
    store = get_store()
    beds = by_id(store.all("beds"))
    rooms = by_id(store.all("rooms"))
    allocations = allocation_by_student()

    students = []
    needle = search.lower()
    for student in store.find("users", role="student"):
        if needle and not any(needle in str(student.get(field) or "").lower()
                              for field in ("name", "email", "student_id")):
            continue
        allocation = allocations.get(student["id"])
        bed = beds.get(allocation["bed_id"]) if allocation else None
        room = rooms.get(bed["room_id"]) if bed else None
        if block and (room is None or room["block"] != block):
            continue
        if allocation_filter == "allocated" and not (allocation and student["is_active"]):
            continue
        if allocation_filter == "unallocated" and (allocation or not student["is_active"]):
            continue
        if allocation_filter == "inactive" and student["is_active"]:
            continue
        students.append({**student,
                         "block": room["block"] if room else None,
                         "room_number": room["room_number"] if room else None,
                         "bed_number": bed["bed_number"] if bed else None})
    students.sort(key=lambda student: (-student["is_active"], student["name"].lower()))
    return students


def waiting_students_count():
    store = get_store()
    allocated = set(allocation_by_student())
    return sum(1 for student in store.find("users", role="student", is_active=1)
               if student["id"] not in allocated)


# ------------------------------------------------------------------
# Complaints
# ------------------------------------------------------------------

def complaint_with_details(complaint_id):
    """One complaint with the student's details and the room label, or None."""
    store = get_store()
    complaint = store.get("complaints", complaint_id)
    if complaint is None:
        return None
    student = store.get("users", complaint["student_id"]) or {}
    room = store.get("rooms", complaint["room_id"]) or {}
    return {**complaint,
            "student_name": student.get("name"), "roll_number": student.get("student_id"),
            "student_phone": student.get("phone"), "student_email": student.get("email"),
            "block": room.get("block"), "floor": room.get("floor"), "room_number": room.get("room_number")}


def student_complaints(student_id, status=""):
    store = get_store()
    complaints = store.find("complaints", student_id=student_id)
    if status in COMPLAINT_STATUSES:
        complaints = [row for row in complaints if row["status"] == status]
    return sort_rows(complaints, "created_at", "id", reverse=True)


def complaint_status_counts(student_id=None):
    store = get_store()
    rows = store.find("complaints", student_id=student_id) if student_id else store.all("complaints")
    return counts_by(rows, "status")


def warden_complaint_list(search="", status="", category="", priority="", block=""):
    """The warden's complaint list: open complaints first, then by priority and date."""
    store = get_store()
    users = by_id(store.all("users"))
    rooms = by_id(store.all("rooms"))
    needle = search.lower()

    rows = []
    for complaint in store.all("complaints"):
        student = users.get(complaint["student_id"]) or {}
        room = rooms.get(complaint["room_id"]) or {}
        if needle and not (needle in str(student.get("name", "")).lower()
                           or needle in complaint["description"].lower()
                           or needle in str(room.get("room_number", "")).lower()):
            continue
        if status == "open" and complaint["status"] not in OPEN_COMPLAINT_STATUSES:
            continue
        if status in COMPLAINT_STATUSES and complaint["status"] != status:
            continue
        if category and complaint["category"] != category:
            continue
        if priority in COMPLAINT_PRIORITIES and complaint["priority"] != priority:
            continue
        if block and room.get("block") != block:
            continue
        rows.append({**complaint, "student_name": student.get("name"),
                     "block": room.get("block"), "room_number": room.get("room_number")})

    # Newest first, then (stable sort) open complaints first and high priority first.
    rows = sort_rows(rows, "created_at", "id", reverse=True)
    status_order = {"Submitted": 1, "Acknowledged": 2, "In Progress": 3}
    priority_order = {"High": 1, "Medium": 2, "Low": 3}
    rows.sort(key=lambda row: (status_order.get(row["status"], 4), priority_order.get(row["priority"], 3)))
    return rows


def recent_complaints_with_names(limit=5):
    store = get_store()
    users = by_id(store.all("users"))
    rooms = by_id(store.all("rooms"))
    rows = [{**complaint,
             "student_name": (users.get(complaint["student_id"]) or {}).get("name"),
             "block": (rooms.get(complaint["room_id"]) or {}).get("block"),
             "room_number": (rooms.get(complaint["room_id"]) or {}).get("room_number")}
            for complaint in store.all("complaints")]
    return sort_rows(rows, "created_at", "id", reverse=True)[:limit]


# ------------------------------------------------------------------
# Room change requests
# ------------------------------------------------------------------

def request_with_details(request_id):
    """One room-change request with the student, the current bed and both other beds."""
    store = get_store()
    room_request = store.get("room_change_requests", request_id)
    if room_request is None:
        return None
    student = store.get("users", room_request["student_id"]) or {}
    result = {**room_request,
              "student_name": student.get("name"), "roll_number": student.get("student_id"),
              "department": student.get("department"), "year_of_study": student.get("year_of_study")}
    for prefix, bed_id in (("current", room_request["current_bed_id"]),
                           ("requested", room_request.get("requested_bed_id")),
                           ("assigned", room_request.get("assigned_bed_id"))):
        bed = get_bed(bed_id) if bed_id else None
        result[f"{prefix}_block"] = bed["block"] if bed else None
        result[f"{prefix}_floor"] = bed["floor"] if bed else None
        result[f"{prefix}_room_number"] = bed["room_number"] if bed else None
        result[f"{prefix}_bed_number"] = bed["bed_number"] if bed else None
        if prefix == "requested":
            result["requested_bed_status"] = bed["status"] if bed else None
    return result


def student_requests(student_id):
    rows = get_store().find("room_change_requests", student_id=student_id)
    return [request_with_details(row["id"])
            for row in sort_rows(rows, "created_at", "id", reverse=True)]


def warden_requests(status="Pending"):
    store = get_store()
    rows = store.all("room_change_requests") if status == "all" else store.find("room_change_requests", status=status)
    return [request_with_details(row["id"])
            for row in sort_rows(rows, "created_at", "id", reverse=True)]


def request_status_counts():
    return counts_by(get_store().all("room_change_requests"), "status")


def recent_requests_with_names(limit=4):
    store = get_store()
    users = by_id(store.all("users"))
    rows = [{**row, "student_name": (users.get(row["student_id"]) or {}).get("name")}
            for row in store.all("room_change_requests")]
    return sort_rows(rows, "created_at", "id", reverse=True)[:limit]


# ------------------------------------------------------------------
# College maintenance requests
# ------------------------------------------------------------------

def college_request_with_details(request_id):
    store = get_store()
    college_request = store.get("college_maintenance_requests", request_id)
    if college_request is None:
        return None
    warden = store.get("users", college_request["warden_id"]) or {}
    complaint = store.get("complaints", college_request.get("complaint_id")) or {}
    return {**college_request, "warden_name": warden.get("name"),
            "complaint_category": complaint.get("category") or None,
            "complaint_student_id": complaint.get("student_id") or None}


def college_request_list(status=""):
    store = get_store()
    rows = store.find("college_maintenance_requests", status=status) if status \
        else store.all("college_maintenance_requests")
    return [college_request_with_details(row["id"])
            for row in sort_rows(rows, "created_at", "id", reverse=True)]


def college_status_counts():
    return counts_by(get_store().all("college_maintenance_requests"), "status")


def latest_college_request_for_complaint(complaint_id):
    rows = get_store().find("college_maintenance_requests", complaint_id=complaint_id)
    return sort_rows(rows, "id", reverse=True)[0] if rows else None


# ------------------------------------------------------------------
# Notifications
# ------------------------------------------------------------------

def notifications_for(user_id, unread_only=False, limit=None):
    rows = get_store().find("notifications", user_id=user_id)
    if unread_only:
        rows = [row for row in rows if not row["is_read"]]
    rows = sort_rows(rows, "created_at", "id", reverse=True)
    return rows[:limit] if limit else rows


def unread_notification_count(user_id):
    return sum(1 for row in get_store().find("notifications", user_id=user_id) if not row["is_read"])


# ------------------------------------------------------------------
# Warden dashboard
# ------------------------------------------------------------------

def dashboard_data():
    """Every number on the warden dashboard, counted from the stored data."""
    store = get_store()
    beds = store.all("beds")
    rooms = store.all("rooms")
    complaints = store.all("complaints")
    requests = store.all("room_change_requests")

    beds_by_status = counts_by(beds, "status")
    total_beds = len(beds)
    usable_beds = total_beds - beds_by_status.get("unavailable", 0)
    occupied_beds = beds_by_status.get("occupied", 0)
    open_complaints = [row for row in complaints if row["status"] in OPEN_COMPLAINT_STATUSES]

    rooms_by_id = by_id(rooms)
    block_totals = {}
    for bed in beds:
        if bed["status"] == "unavailable":
            continue
        room = rooms_by_id.get(bed["room_id"])
        if not room:
            continue
        totals = block_totals.setdefault(room["block"], {"total": 0, "occupied": 0})
        totals["total"] += 1
        totals["occupied"] += 1 if bed["status"] == "occupied" else 0

    categories = counts_by(open_complaints, "category")
    top_categories = [{"category": name, "total": total}
                      for name, total in sorted(categories.items(), key=lambda item: (-item[1], item[0]))[:5]]

    return {
        "students": store.count("users", role="student", is_active=1),
        "rooms": sum(1 for room in rooms if room["status"] != "inactive"),
        "total_beds": total_beds,
        "occupied_beds": occupied_beds,
        "available_beds": beds_by_status.get("available", 0),
        "pending_complaints": len(open_complaints),
        "pending_requests": sum(1 for row in requests if row["status"] == "Pending"),
        "usable_beds": usable_beds,
        "beds_by_status": beds_by_status,
        "block_totals": block_totals,
        "complaint_status_counts": counts_by(complaints, "status"),
        "top_categories": top_categories,
        "high_priority_open": sum(1 for row in open_complaints if row["priority"] == "High"),
        "waiting_students": waiting_students_count(),
        "college_awaiting": sum(1 for row in store.all("college_maintenance_requests")
                                if row["status"] in ("Sent to College", "Under Review")),
    }
