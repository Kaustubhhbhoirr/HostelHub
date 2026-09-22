"""
seed.py — Create a fresh database filled with realistic demo data.

Run it any time you want to reset the demo:

    python seed.py

WARNING: this deletes ALL existing data.
  - Normally it rebuilds the local SQLite file database/hostelhub.db.
  - If the DATABASE_URL environment variable is set, it rebuilds that
    PostgreSQL database instead and first asks you to type RESET.
    (The Flask app never runs this automatically in production.)

Why Python instead of a seed.sql file? The hostel has ~100 beds and ~70
students. Generating them with loops is much shorter and easier to read
than hundreds of hand-written INSERT statements.
"""

import random
import secrets
import sys
from datetime import timedelta

from werkzeug.security import generate_password_hash

from config import Config, DEPARTMENTS
from database import connect, create_tables, insert, local_now, run

STUDENT_PASSWORD = "Student@123"
WARDEN_PASSWORD = "Warden@123"

# Real team accounts that sign in with "Continue with Google" (Firebase).
# They get a random password that nobody knows, so Google is the only way in.
GOOGLE_STUDENTS = [
    # (name, college Google email, student ID, department, year)
    ("Kaustubh Bhoir", "kaustubhb25comp@student.mes.ac.in", "25CE101", "Computer Engineering", 2),
]

# Block layout: block letter -> (number of floors, rooms per floor, beds per room)
HOSTEL_LAYOUT = {
    "A": (3, 4, 4),
    "B": (3, 4, 3),
    "C": (2, 4, 2),
}

FIRST_NAMES = [
    "Vivaan", "Aditya", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan", "Rohan",
    "Kabir", "Yash", "Omkar", "Pranav", "Siddharth", "Tanmay", "Atharva", "Harsh",
    "Nikhil", "Varun", "Rahul", "Karan", "Aniket", "Soham", "Parth", "Shreyas",
    "Tejas", "Vedant", "Kunal", "Manas", "Rudra", "Aryan", "Dhruv", "Neel",
    "Mihir", "Aayush", "Sahil", "Chinmay", "Jay", "Vinayak", "Advait", "Ritesh",
]
LAST_NAMES = [
    "Patil", "Deshmukh", "Kulkarni", "Joshi", "Nair", "Iyer", "Gupta", "Shinde",
    "Pawar", "Jadhav", "More", "Mehta", "Rao", "Menon", "Kamble", "Chavan",
    "Desai", "Naik", "Thakur", "Pillai", "Reddy", "Bhosale", "Gaikwad", "Sawant",
]
DEPARTMENT_CODES = {
    "Computer Engineering": "CE",
    "Information Technology": "IT",
    "Electronics & Telecommunication": "EX",
    "Mechanical Engineering": "ME",
    "AI & Data Science": "AD",
}


def ago(days=0, hours=0):
    """Return a date-time text for some time in the past."""
    moment = local_now() - timedelta(days=days, hours=hours)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def insert_user(conn, name, email, password_hash, role, student_id=None,
                phone=None, department=None, year=None, created_days_ago=120):
    return insert(
        conn,
        """INSERT INTO users (name, email, password_hash, role, student_id, phone,
                              department, year_of_study, is_active, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
        (name, email, password_hash, role, student_id, phone, department, year,
         ago(days=created_days_ago)),
    )


def seed_rooms_and_beds(conn):
    """Create rooms and beds. Returns a dict like {"A-201": {1: bed_id, 2: bed_id, ...}}."""
    bed_ids = {}
    for block, (floors, rooms_per_floor, beds_per_room) in HOSTEL_LAYOUT.items():
        for floor in range(1, floors + 1):
            for room_index in range(1, rooms_per_floor + 1):
                room_number = f"{floor}{room_index:02d}"          # e.g. 201, 202
                room_id = insert(
                    conn,
                    "INSERT INTO rooms (block, floor, room_number, capacity, status) VALUES (?, ?, ?, ?, 'active')",
                    (block, floor, room_number, beds_per_room),
                )
                label = f"{block}-{room_number}"
                bed_ids[label] = {}
                for bed_number in range(1, beds_per_room + 1):
                    bed_ids[label][bed_number] = insert(
                        conn,
                        "INSERT INTO beds (room_id, bed_number, status) VALUES (?, ?, 'available')",
                        (room_id, bed_number),
                    )
    return bed_ids


def set_room_status(conn, label, room_status, bed_status):
    block, room_number = label.split("-")
    run(conn, "UPDATE rooms SET status = ? WHERE block = ? AND room_number = ?",
        (room_status, block, room_number))
    run(
        conn,
        """UPDATE beds SET status = ?
           WHERE room_id = (SELECT id FROM rooms WHERE block = ? AND room_number = ?)""",
        (bed_status, block, room_number),
    )


def allocate(conn, student_id, bed_id, days_ago):
    run(
        conn,
        "INSERT INTO allocations (student_id, bed_id, allocated_at, status) VALUES (?, ?, ?, 'active')",
        (student_id, bed_id, ago(days=days_ago)),
    )
    run(conn, "UPDATE beds SET status = 'occupied' WHERE id = ?", (bed_id,))


def room_id_of_bed(conn, bed_id):
    return run(conn, "SELECT room_id FROM beds WHERE id = ?", (bed_id,)).fetchone()["room_id"]


def notify(conn, user_id, title, message, notif_type, link, is_read, when):
    run(
        conn,
        """INSERT INTO notifications (user_id, title, message, type, link, is_read, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, title, message, notif_type, link, is_read, when),
    )


def add_complaint(conn, student_id, bed_id, category, description, priority,
                  status, remarks, days_ago, updated_days_ago=None):
    created = ago(days=days_ago, hours=random.randint(0, 8))
    updated = created if updated_days_ago is None else ago(days=updated_days_ago)
    resolved = updated if status == "Resolved" else None
    return insert(
        conn,
        """INSERT INTO complaints (student_id, room_id, category, description, priority, status,
                                   warden_remarks, created_at, updated_at, resolved_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (student_id, room_id_of_bed(conn, bed_id), category, description, priority,
         status, remarks, created, updated, resolved),
    )


def seed_database(db_path=Config.DATABASE, database_url=""):
    """Build the whole demo database.

    - seed_database("some/file.db")            -> SQLite file (app.py first run, tests)
    - seed_database(database_url="postgres://") -> PostgreSQL (explicit `python seed.py`)
    """
    random.seed(42)  # fixed seed -> the same demo data every time

    conn = connect(database_url, db_path)
    create_tables(conn, Config.SCHEMA_FILE)

    # Hash each password once; every demo student shares the same demo password.
    student_hash = generate_password_hash(STUDENT_PASSWORD)
    warden_hash = generate_password_hash(WARDEN_PASSWORD)

    # ---------------- Users ----------------
    warden_id = insert_user(conn, "Dr. Meera Kulkarni", "warden@mes.ac.in", warden_hash,
                            "warden", phone="9820012345", created_days_ago=400)

    demo_student = insert_user(conn, "Aarav Sharma", "student@student.mes.ac.in", student_hash, "student",
                               "25CE001", "9876543210", "Computer Engineering", 2)

    used_emails = {"student@student.mes.ac.in", "warden@mes.ac.in"}
    students = []  # list of (user_id, name)
    serial = 2
    while len(students) < 71:
        first, last = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
        email = f"{first.lower()}.{last.lower()}@student.mes.ac.in"
        if email in used_emails:
            continue
        used_emails.add(email)
        department = random.choice(DEPARTMENTS)
        year = random.randint(1, 4)
        admission_year = 26 - year + 1               # 1st year -> admitted in 2026
        roll = f"{admission_year}{DEPARTMENT_CODES[department]}{serial:03d}"
        phone = "9" + "".join(random.choice("0123456789") for _ in range(9))
        user_id = insert_user(conn, f"{first} {last}", email, student_hash, "student",
                              roll, phone, department, year, created_days_ago=random.randint(30, 200))
        students.append((user_id, f"{first} {last}"))
        serial += 1

    # ---------------- Rooms and beds ----------------
    beds = seed_rooms_and_beds(conn)
    set_room_status(conn, "C-204", "maintenance", "maintenance")   # under repair
    set_room_status(conn, "B-304", "inactive", "unavailable")      # closed room
    run(conn, "UPDATE beds SET status = 'maintenance' WHERE id IN (?, ?)",
        (beds["A-103"][4], beds["B-202"][3]))

    # ---------------- Allocations ----------------
    # Demo student lives in A-201 bed 2 with two roommates; bed 4 stays free.
    allocate(conn, demo_student, beds["A-201"][2], 90)
    allocate(conn, students[0][0], beds["A-201"][1], 90)
    allocate(conn, students[1][0], beds["A-201"][3], 60)

    # All other free beds, in random order.
    free_beds = [
        row["id"] for row in run(
            conn,
            """SELECT beds.id FROM beds JOIN rooms ON rooms.id = beds.room_id
               WHERE beds.status = 'available' AND rooms.status = 'active'
                 AND NOT (rooms.block = 'A' AND rooms.room_number = '201')
               ORDER BY beds.id"""
        ).fetchall()
    ]
    random.shuffle(free_beds)

    # students[2:66] get beds; the last 5 students stay unallocated for the warden to allocate.
    allocated_students = students[2:66]
    for (student_id, _name), bed_id in zip(allocated_students, free_beds):
        allocate(conn, student_id, bed_id, random.randint(10, 100))

    def current_bed(student_id):
        return run(
            conn, "SELECT bed_id FROM allocations WHERE student_id = ? AND status = 'active'", (student_id,)
        ).fetchone()["bed_id"]

    # ---------------- Complaints ----------------
    demo_bed = beds["A-201"][2]
    c_fan = add_complaint(conn, demo_student, demo_bed, "Fan",
                          "The ceiling fan makes a loud grinding noise and runs very slowly even at full speed. "
                          "It is difficult to sleep at night.",
                          "High", "In Progress", "Electrician has checked it; the motor needs replacement. "
                          "Replacement requested from college.", 4, 1)
    c_water = add_complaint(conn, demo_student, demo_bed, "Water",
                            "No hot water in the second-floor bathroom since yesterday evening.",
                            "Medium", "Resolved", "Geyser thermostat replaced. Please report again if the issue returns.",
                            14, 12)
    add_complaint(conn, demo_student, demo_bed, "Furniture",
                  "The lock on my study-table drawer is jammed and the drawer does not open.",
                  "Low", "Submitted", None, 0)

    other_complaints = [
        ("Light", "Tube light in the room flickers continuously and switches off after a few minutes.", "Medium", "Submitted", None, 1),
        ("Plumbing", "Wash-basin tap is leaking and water collects on the floor.", "High", "Acknowledged", "Plumber informed.", 2),
        ("Electrical", "Power socket near bed 3 sparks when a charger is plugged in.", "High", "Submitted", None, 0),
        ("Cleaning", "Corridor dustbin on floor 2 has not been emptied for three days.", "Low", "Resolved", "Cleaning staff schedule updated.", 9),
        ("Door/Lock", "Room door latch is broken and the door cannot be locked from inside.", "High", "In Progress", "Carpenter visit scheduled.", 3),
        ("Bathroom", "Shower head is blocked and water flow is very weak.", "Medium", "Resolved", "Shower head cleaned and replaced.", 20),
        ("Water", "Drinking water cooler on floor 1 is not cooling.", "Medium", "Acknowledged", "Technician will visit this week.", 5),
        ("Other", "Wi-Fi is very slow in the room during the evening.", "Low", "Rejected",
         "Internet issues are handled by the IT department. Please raise a ticket on the IT help desk portal.", 7),
        ("Furniture", "One leg of the bed is cracked and the bed wobbles.", "Medium", "Submitted", None, 2),
        ("Fan", "Fan regulator does not work; fan only runs at full speed.", "Low", "Resolved", "Regulator replaced.", 25),
    ]
    complainers = random.sample(allocated_students, len(other_complaints))
    for (student_id, _name), (category, text, priority, status, remarks, days) in zip(complainers, other_complaints):
        updated = None if status == "Submitted" else max(days - 1, 0)
        add_complaint(conn, student_id, current_bed(student_id), category, text,
                      priority, status, remarks, days, updated)

    # ---------------- Room change requests ----------------
    # Old rejected request for the demo student (so the history is not empty).
    run(
        conn,
        """INSERT INTO room_change_requests (student_id, current_bed_id, requested_bed_id, reason, details,
                                             status, warden_remarks, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'Rejected', ?, ?, ?)""",
        (demo_student, demo_bed, None, "Want to stay closer to classmates",
         "My project group stays in Block B and it would be easier to work together.",
         "Block B has no free beds this month. You may apply again next month.", ago(days=40), ago(days=38)),
    )

    # Two pending requests that each reserve a bed (these appear YELLOW on the map).
    pending_requesters = [students[10], students[25]]
    pending_targets = [beds["A-201"][4], beds["C-101"][2]]
    for (student_id, _name), target_bed in zip(pending_requesters, pending_targets):
        if run(conn, "SELECT status FROM beds WHERE id = ?", (target_bed,)).fetchone()["status"] != "available":
            # Seat already taken by random allocation: pick another free bed instead.
            target_bed = run(
                conn,
                """SELECT beds.id FROM beds JOIN rooms ON rooms.id = beds.room_id
                   WHERE beds.status = 'available' AND rooms.status = 'active' ORDER BY beds.id LIMIT 1"""
            ).fetchone()["id"]
        run(
        conn,
            """INSERT INTO room_change_requests (student_id, current_bed_id, requested_bed_id, reason, details,
                                                 status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'Pending', ?, ?)""",
            (student_id, current_bed(student_id), target_bed, "Need a quieter room for studies",
             "My current room is next to the common room and it is noisy late at night. "
             "I have semester exams coming up.", ago(days=1), ago(days=1)),
        )
        run(conn, "UPDATE beds SET status = 'reserved' WHERE id = ?", (target_bed,))

    # One approved request from the past, with matching allocation history.
    moved_student = students[30][0]
    new_bed = current_bed(moved_student)
    old_bed = run(
        conn,
        """SELECT beds.id FROM beds JOIN rooms ON rooms.id = beds.room_id
           WHERE beds.status = 'available' AND rooms.status = 'active' ORDER BY beds.id DESC LIMIT 1"""
    ).fetchone()["id"]
    run(
        conn,
        "INSERT INTO allocations (student_id, bed_id, allocated_at, ended_at, status) VALUES (?, ?, ?, ?, 'ended')",
        (moved_student, old_bed, ago(days=150), ago(days=15)),
    )
    run(
        conn,
        """INSERT INTO room_change_requests (student_id, current_bed_id, requested_bed_id, assigned_bed_id,
                                             reason, details, status, warden_remarks, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'Approved', ?, ?, ?)""",
        (moved_student, old_bed, new_bed, new_bed, "Health or medical reason",
         "Doctor advised a ground-floor room after a knee injury.",
         "Approved on medical grounds.", ago(days=17), ago(days=15)),
    )
    run(conn, "UPDATE allocations SET allocated_at = ? WHERE student_id = ? AND status = 'active'",
        (ago(days=15), moved_student))

    # ---------------- Google sign-in team accounts ----------------
    # Added last so every other demo row above stays exactly the same.
    for name, email, roll, department, year in GOOGLE_STUDENTS:
        google_only_hash = generate_password_hash(secrets.token_hex(32))
        user_id = insert_user(conn, name, email, google_only_hash, "student", roll, None, department, year,
                              created_days_ago=30)
        free_bed = run(
            conn,
            """SELECT beds.id FROM beds JOIN rooms ON rooms.id = beds.room_id
               WHERE beds.status = 'available' AND rooms.status = 'active' ORDER BY beds.id LIMIT 1"""
        ).fetchone()["id"]
        allocate(conn, user_id, free_bed, 30)
        notify(conn, user_id, "Welcome to HostelHub",
               "Your hostel account is ready. Sign in any time with your MES Google account.",
               "system", "/student/dashboard", 0, ago(days=30))

    # ---------------- College maintenance requests ----------------
    college_requests = [
        (c_fan, "Replacement", "Ceiling Fan", "Block A, Room 201", 1,
         "Fan motor is damaged and makes a grinding noise. The electrician confirmed the motor cannot be repaired.",
         "High", "Sent to College", None, 1),
        (None, "New Equipment", "Water Purifier (RO)", "Block B, Floor 1 corridor", 1,
         "The existing purifier cannot serve 36 students. Requesting an additional RO purifier.",
         "Medium", "Approved", "Approved by the administrative office. Installation next week.", 18),
        (None, "Furniture Replacement", "Mattress", "Block C, Room 204", 2,
         "Mattresses are torn and must be replaced before the room is reopened.",
         "Medium", "Draft", None, 2),
        (None, "Plumbing Work", "Bathroom pipeline", "Block A, Floor 3 bathroom", 1,
         "Main pipeline leakage in the floor 3 bathroom wall.",
         "High", "Completed", "Pipeline replaced by the college maintenance team.", 35),
    ]
    for complaint_id, rtype, asset, location, qty, text, priority, status, remarks, days in college_requests:
        run(
        conn,
            """INSERT INTO college_maintenance_requests
               (warden_id, complaint_id, request_type, asset, location, quantity, description,
                priority, status, college_remarks, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (warden_id, complaint_id, rtype, asset, location, qty, text, priority, status,
             remarks, ago(days=days), ago(days=max(days - 2, 0))),
        )

    # ---------------- Notifications ----------------
    notify(conn, demo_student, "Welcome to HostelHub",
           "Your hostel account is ready. You can view your room and report maintenance issues here.",
           "system", "/student/dashboard", 1, ago(days=90))
    notify(conn, demo_student, "Room change request rejected",
           "Block B has no free beds this month. You may apply again next month.",
           "room_request", "/student/room-requests", 1, ago(days=38))
    notify(conn, demo_student, "Complaint resolved",
           f"Your Water complaint (CMP-{c_water:04d}) has been marked as Resolved.",
           "complaint", f"/student/complaints/{c_water}", 1, ago(days=12))
    notify(conn, demo_student, "Complaint in progress",
           f"Your Fan complaint (CMP-{c_fan:04d}) is now In Progress. Remarks: motor needs replacement.",
           "complaint", f"/student/complaints/{c_fan}", 0, ago(days=1))

    # Warden: one notification for each submitted complaint and pending request.
    for row in run(
        conn,
        """SELECT complaints.id, complaints.category, complaints.priority, complaints.created_at, users.name
           FROM complaints JOIN users ON users.id = complaints.student_id
           WHERE complaints.status = 'Submitted' ORDER BY complaints.id"""
    ).fetchall():
        notify(conn, warden_id, f"New {row['priority'].lower()} priority complaint",
               f"{row['name']} reported a {row['category']} issue (CMP-{row['id']:04d}).",
               "complaint", f"/warden/complaints/{row['id']}", 0, row["created_at"])
    for row in run(
        conn,
        """SELECT room_change_requests.id, users.name, room_change_requests.created_at
           FROM room_change_requests JOIN users ON users.id = room_change_requests.student_id
           WHERE room_change_requests.status = 'Pending' ORDER BY room_change_requests.id"""
    ).fetchall():
        notify(conn, warden_id, "New room change request",
               f"{row['name']} has requested a room change.",
               "room_request", f"/warden/room-requests/{row['id']}", 0, row["created_at"])
    notify(conn, warden_id, "College request approved",
           "Water Purifier (RO) request for Block B was approved by the college.",
           "college", "/warden/college-requests", 1, ago(days=16))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    if Config.DATABASE_URL:
        # Resetting a hosted database is destructive, so ask first
        # (or pass --yes when you are completely sure).
        print("DATABASE_URL is set: this will DELETE ALL DATA in that PostgreSQL database.")
        if "--yes" not in sys.argv and input("Type RESET to continue: ").strip() != "RESET":
            print("Cancelled. Nothing was changed.")
            sys.exit(1)
        seed_database(database_url=Config.DATABASE_URL)
        print("PostgreSQL database created with demo data.")
    else:
        seed_database(Config.DATABASE)
        print("Database created with demo data:", Config.DATABASE)
    print(f"  Student login: student@student.mes.ac.in / {STUDENT_PASSWORD}")
    print(f"  Warden login : warden@mes.ac.in / {WARDEN_PASSWORD}")
    for _name, google_email, *_rest in GOOGLE_STUDENTS:
        print(f"  Google login : {google_email} (Continue with Google)")
