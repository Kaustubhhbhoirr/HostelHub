"""
check_database.py — Look for inconsistent data in the HostelHub database.

Run it any time to prove the live Firestore data is still correct:

    python check_database.py

Each check looks for records that break a hostel rule. A correct database
produces no problems at all. The automated tests call find_problems() after
every test.
"""

import sys

from dotenv import load_dotenv

# Read .env BEFORE config.py, which takes the Firebase settings from the environment.
load_dotenv()

from config import COMPLAINT_STATUSES, ROOM_CHANGE_STATUSES, ROOM_STATUSES  # noqa: E402
from data import create_store  # noqa: E402

BED_STATUSES = ("available", "occupied", "reserved", "maintenance", "unavailable")


def find_problems(store):
    """Return a list of human-readable problems (an empty list means all good)."""
    users = {row["id"]: row for row in store.all("users")}
    rooms = {row["id"]: row for row in store.all("rooms")}
    beds = {row["id"]: row for row in store.all("beds")}
    allocations = store.all("allocations")
    requests = store.all("room_change_requests")
    complaints = store.all("complaints")

    active = [row for row in allocations if row["status"] == "active"]
    active_by_bed, active_by_student = {}, {}
    problems = []

    def report(description, broken_ids):
        if broken_ids:
            listed = ", ".join(str(value) for value in sorted(set(broken_ids))[:5])
            problems.append(f"{description}: {len(set(broken_ids))} found (ids: {listed})")

    # 1 and 2: the two "only one active allocation" rules.
    duplicate_students, duplicate_beds = [], []
    for allocation in active:
        if allocation["student_id"] in active_by_student:
            duplicate_students.append(allocation["student_id"])
        active_by_student[allocation["student_id"]] = allocation
        if allocation["bed_id"] in active_by_bed:
            duplicate_beds.append(allocation["bed_id"])
        active_by_bed[allocation["bed_id"]] = allocation
    report("Student with more than one active allocation", duplicate_students)
    report("Bed with more than one active allocation", duplicate_beds)

    # 3 and 4: bed status and allocations must agree.
    report("Bed marked occupied but nobody is allocated to it",
           [bed["id"] for bed in beds.values() if bed["status"] == "occupied" and bed["id"] not in active_by_bed])
    report("Active allocation on a bed that is not marked occupied",
           [allocation["id"] for allocation in active
            if beds.get(allocation["bed_id"], {}).get("status") != "occupied"])

    # 5: only active students may hold a bed.
    report("Active allocation for a warden or a deactivated student",
           [allocation["id"] for allocation in active
            if users.get(allocation["student_id"], {}).get("role") != "student"
            or not users.get(allocation["student_id"], {}).get("is_active")])

    # 6 and 7: a reserved bed belongs to exactly one pending request.
    reserved_for = {row["requested_bed_id"] for row in requests
                    if row["status"] == "Pending" and row.get("requested_bed_id")}
    report("Bed marked reserved but no pending request is holding it",
           [bed["id"] for bed in beds.values() if bed["status"] == "reserved" and bed["id"] not in reserved_for])
    report("Pending request whose preferred bed is not reserved",
           [row["id"] for row in requests if row["status"] == "Pending" and row.get("requested_bed_id")
            and beds.get(row["requested_bed_id"], {}).get("status") != "reserved"])

    # 8: every room has exactly as many beds as its capacity.
    beds_per_room = {}
    for bed in beds.values():
        beds_per_room[bed["room_id"]] = beds_per_room.get(bed["room_id"], 0) + 1
    report("Room whose number of beds does not match its capacity",
           [room["id"] for room in rooms.values() if beds_per_room.get(room["id"], 0) != room["capacity"]])

    # 9: a closed room cannot have usable beds.
    report("Inactive room with a bed that is still usable or in use",
           [bed["id"] for bed in beds.values()
            if rooms.get(bed["room_id"], {}).get("status") == "inactive"
            and bed["status"] in ("available", "occupied", "reserved")])

    # 10 and 11: finished records must carry their finishing time.
    report("Resolved complaint without a resolved time",
           [row["id"] for row in complaints if row["status"] == "Resolved" and not row.get("resolved_at")])
    report("Ended allocation without an end time",
           [row["id"] for row in allocations if row["status"] == "ended" and not row.get("ended_at")])

    # 12: Firestore has no foreign keys, so references to missing documents are looked for here.
    report("Record pointing to a user, bed or room that does not exist",
           [f"allocation {row['id']}" for row in allocations
            if row["student_id"] not in users or row["bed_id"] not in beds]
           + [f"complaint {row['id']}" for row in complaints
              if row["student_id"] not in users or row.get("room_id") not in rooms]
           + [f"bed {bed['id']}" for bed in beds.values() if bed["room_id"] not in rooms]
           + [f"request {row['id']}" for row in requests if row["student_id"] not in users])

    # 13: and no CHECK constraints, so the allowed values are looked at here.
    report("Record with a value that is not allowed",
           [f"user {row['id']}" for row in users.values() if row.get("role") not in ("student", "warden")]
           + [f"bed {row['id']}" for row in beds.values() if row.get("status") not in BED_STATUSES]
           + [f"room {row['id']}" for row in rooms.values() if row.get("status") not in ROOM_STATUSES]
           + [f"allocation {row['id']}" for row in allocations if row.get("status") not in ("active", "ended")]
           + [f"complaint {row['id']}" for row in complaints if row.get("status") not in COMPLAINT_STATUSES]
           + [f"request {row['id']}" for row in requests if row.get("status") not in ROOM_CHANGE_STATUSES])

    return problems


TOTAL_CHECKS = 13


def main():
    store = create_store()
    name = "Firestore"
    try:
        problems = find_problems(store)
    finally:
        store.close()

    if problems:
        print(f"{name} database has {len(problems)} problem(s):")
        for problem in problems:
            print("  -", problem)
        return 1
    print(f"{name} database is consistent: all {TOTAL_CHECKS} checks passed.")
    return 0


if __name__ == "__main__":
    import app as hostelhub_app              # starts the Firebase Admin SDK from the settings
    _ = hostelhub_app.app
    sys.exit(main())
