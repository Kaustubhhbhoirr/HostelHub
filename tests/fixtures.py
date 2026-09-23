"""
tests/fixtures.py — A small hostel built in memory for the tests.

The application itself ships with no data: the real hostel is created in Firebase
by the hostel office. The tests still need something to work with, so this module
writes a miniature hostel (a few rooms, beds, students and complaints) into the
in-memory Firestore stand-in before each test.

It is test material only and is never used by the application.
"""

from data import now_str

WARDEN_EMAIL = "warden@mes.ac.in"
STUDENT_EMAIL = "aarav.sharma@student.mes.ac.in"

LAYOUT = [
    # (block, floor, room number, beds, room status)
    ("A", 1, "101", 4, "active"),
    ("A", 2, "201", 4, "active"),
    ("B", 1, "101", 3, "active"),
    ("B", 2, "201", 2, "maintenance"),
    ("C", 1, "101", 2, "inactive"),
]

STUDENTS = [
    # (name, email, roll number, department, year)
    ("Aarav Sharma", STUDENT_EMAIL, "25CE001", "Computer Engineering", 2),
    ("Rohan Patil", "rohan.patil@student.mes.ac.in", "25CE002", "Computer Engineering", 2),
    ("Tanmay Shinde", "tanmay.shinde@student.mes.ac.in", "24IT015", "Information Technology", 3),
    ("Mihir Joshi", "mihir.joshi@student.mes.ac.in", "23EX012", "Electronics & Telecommunication", 4),
    ("Kabir Nair", "kabir.nair@student.mes.ac.in", "25AD003", "AI & Data Science", 1),
    ("Varun More", "varun.more@student.mes.ac.in", "25ME004", "Mechanical Engineering", 1),
    ("Sahil Menon", "sahil.menon@student.mes.ac.in", "24CE020", "Computer Engineering", 3),
]

COMPLAINTS = [
    # (student index, category, description, priority, status, remarks)
    (0, "Fan", "The ceiling fan makes a loud grinding noise and runs very slowly.", "High", "In Progress",
     "Electrician has checked it; the motor needs replacement."),
    (0, "Water", "No hot water in the second-floor bathroom since yesterday evening.", "Medium", "Resolved",
     "Geyser thermostat replaced."),
    (0, "Furniture", "The lock on my study-table drawer is jammed.", "Low", "Submitted", None),
    (1, "Light", "Tube light in the room flickers continuously.", "Medium", "Acknowledged", "Electrician informed."),
    (2, "Other", "Wi-Fi is very slow in the room during the evening.", "Low", "Rejected",
     "Internet issues are handled by the IT department."),
]


def build(store):
    """Fill the store with the miniature hostel. Returns the ids the tests need."""
    warden_id = store.insert("users", {
        "name": "Dr. Meera Kulkarni", "email": WARDEN_EMAIL, "role": "warden", "student_id": None,
        "phone": "9820012345", "department": None, "year_of_study": None, "is_active": 1,
        "firebase_uid": "uid-warden", "created_at": now_str(),
    })

    student_ids = []
    for index, (name, email, roll, department, year) in enumerate(STUDENTS):
        student_ids.append(store.insert("users", {
            "name": name, "email": email, "role": "student", "student_id": roll,
            "phone": f"98765432{index:02d}", "department": department, "year_of_study": year,
            "is_active": 1, "firebase_uid": f"uid-student-{index}", "created_at": now_str(),
        }))

    beds = {}          # "A-101" -> {bed number: bed id}
    for block, floor, number, count, status in LAYOUT:
        room_id = store.insert("rooms", {"block": block, "floor": floor, "room_number": number,
                                         "capacity": count, "status": status})
        bed_status = {"active": "available", "maintenance": "maintenance"}.get(status, "unavailable")
        beds[f"{block}-{number}"] = {
            number_of_bed: store.insert("beds", {"room_id": room_id, "bed_number": number_of_bed,
                                                 "status": bed_status})
            for number_of_bed in range(1, count + 1)
        }

    def allocate(student_id, bed_id):
        store.insert("allocations", {"student_id": student_id, "bed_id": bed_id,
                                     "allocated_at": now_str(), "ended_at": None, "status": "active"})
        store.update("beds", bed_id, {"status": "occupied"})
        store.claim(f"bed-{bed_id}")
        store.claim(f"student-{student_id}")

    # Five students have a bed; the last two are waiting for one.
    allocate(student_ids[0], beds["A-201"][2])
    allocate(student_ids[1], beds["A-201"][1])
    allocate(student_ids[2], beds["A-201"][3])
    allocate(student_ids[3], beds["A-101"][1])
    allocate(student_ids[4], beds["B-101"][1])

    rooms_by_bed = {bed_id: store.get("beds", bed_id)["room_id"]
                    for room in beds.values() for bed_id in room.values()}

    complaint_ids = []
    for student_index, category, description, priority, status, remarks in COMPLAINTS:
        allocation = store.first("allocations", student_id=student_ids[student_index], status="active")
        complaint_ids.append(store.insert("complaints", {
            "student_id": student_ids[student_index], "room_id": rooms_by_bed[allocation["bed_id"]],
            "category": category, "description": description, "image_path": None, "priority": priority,
            "status": status, "warden_remarks": remarks, "created_at": now_str(), "updated_at": now_str(),
            "resolved_at": now_str() if status == "Resolved" else None,
        }))

    # One pending room-change request, holding (reserving) a free bed.
    reserved_bed = beds["A-101"][2]
    request_id = store.insert("room_change_requests", {
        "student_id": student_ids[2],
        "current_bed_id": beds["A-201"][3], "requested_bed_id": reserved_bed, "assigned_bed_id": None,
        "reason": "Need a quieter room for studies",
        "details": "My current room is next to the common room and it is noisy late at night.",
        "status": "Pending", "warden_remarks": None, "created_at": now_str(), "updated_at": now_str(),
    })
    store.update("beds", reserved_bed, {"status": "reserved"})

    # One finished request, so the history is not empty.
    store.insert("room_change_requests", {
        "student_id": student_ids[1], "current_bed_id": beds["A-201"][1], "requested_bed_id": None,
        "assigned_bed_id": None, "reason": "Want to stay closer to classmates",
        "details": "My project group stays in Block B and it would be easier to work together.",
        "status": "Rejected", "warden_remarks": "Block B has no free beds this month.",
        "created_at": now_str(), "updated_at": now_str(),
    })

    college_ids = []
    for request_type, asset, location, status, complaint_index in [
            ("Replacement", "Ceiling Fan", "Block A, Room 201", "Sent to College", 0),
            ("New Equipment", "Water Purifier (RO)", "Block B, Floor 1 corridor", "Draft", None),
            ("Plumbing Work", "Bathroom pipeline", "Block A, Floor 1 bathroom", "Completed", None)]:
        college_ids.append(store.insert("college_maintenance_requests", {
            "warden_id": warden_id,
            "complaint_id": complaint_ids[complaint_index] if complaint_index is not None else None,
            "request_type": request_type, "asset": asset, "location": location, "quantity": 1,
            "description": f"{asset} needs attention in {location}.", "priority": "High",
            "status": status, "college_remarks": None, "created_at": now_str(), "updated_at": now_str(),
        }))

    for user_id, title, message, is_read in [
            (student_ids[0], "Welcome to HostelHub", "Your hostel account is ready.", 1),
            (student_ids[0], "Complaint in progress", "Your Fan complaint is now In Progress.", 0),
            (warden_id, "New room change request", "Tanmay Shinde has requested a room change.", 0)]:
        store.insert("notifications", {"user_id": user_id, "title": title, "message": message,
                                       "type": "system", "link": "/notifications", "is_read": is_read,
                                       "created_at": now_str()})

    store.commit()
    return {"warden_id": warden_id, "student_ids": student_ids, "beds": beds,
            "complaint_ids": complaint_ids, "request_id": request_id, "college_ids": college_ids,
            "reserved_bed": reserved_bed}
