"""
check_database.py — Look for inconsistent data in the HostelHub database.

Run it any time (for example after a demo) to prove the data is still correct:

    python check_database.py

Each check is a SQL query that should return NO rows. If a query returns
rows, something broke a hostel rule and the problem is printed.
The automated tests also call find_problems() after every test.
"""

import sys

from config import Config
from database import connect, is_postgres, run

# (description, SQL that returns the broken rows)
CHECKS = [
    ("Student with more than one active allocation",
     """SELECT student_id AS problem_id FROM allocations WHERE status = 'active'
        GROUP BY student_id HAVING COUNT(*) > 1"""),
    ("Bed with more than one active allocation",
     """SELECT bed_id AS problem_id FROM allocations WHERE status = 'active'
        GROUP BY bed_id HAVING COUNT(*) > 1"""),
    ("Bed marked occupied but nobody is allocated to it",
     """SELECT beds.id AS problem_id FROM beds
        WHERE beds.status = 'occupied'
          AND NOT EXISTS (SELECT 1 FROM allocations a WHERE a.bed_id = beds.id AND a.status = 'active')"""),
    ("Active allocation on a bed that is not marked occupied",
     """SELECT a.id AS problem_id FROM allocations a JOIN beds ON beds.id = a.bed_id
        WHERE a.status = 'active' AND beds.status != 'occupied'"""),
    ("Active allocation for a warden or a deactivated student",
     """SELECT a.id AS problem_id FROM allocations a JOIN users ON users.id = a.student_id
        WHERE a.status = 'active' AND (users.role != 'student' OR users.is_active = 0)"""),
    ("Bed marked reserved but no pending request is holding it",
     """SELECT beds.id AS problem_id FROM beds
        WHERE beds.status = 'reserved'
          AND NOT EXISTS (SELECT 1 FROM room_change_requests r
                          WHERE r.requested_bed_id = beds.id AND r.status = 'Pending')"""),
    ("Pending request whose preferred bed is not reserved",
     """SELECT r.id AS problem_id FROM room_change_requests r JOIN beds ON beds.id = r.requested_bed_id
        WHERE r.status = 'Pending' AND beds.status != 'reserved'"""),
    ("Room whose number of beds does not match its capacity",
     """SELECT rooms.id AS problem_id FROM rooms LEFT JOIN beds ON beds.room_id = rooms.id
        GROUP BY rooms.id HAVING COUNT(beds.id) != rooms.capacity"""),
    ("Inactive room with a bed that is still usable or in use",
     """SELECT beds.id AS problem_id FROM beds JOIN rooms ON rooms.id = beds.room_id
        WHERE rooms.status = 'inactive' AND beds.status IN ('available', 'occupied', 'reserved')"""),
    ("Resolved complaint without a resolved time",
     "SELECT id AS problem_id FROM complaints WHERE status = 'Resolved' AND resolved_at IS NULL"),
    ("Ended allocation without an end time",
     "SELECT id AS problem_id FROM allocations WHERE status = 'ended' AND ended_at IS NULL"),
]


def find_problems(conn):
    """Return a list of human-readable problems (an empty list means all good).

    Works with a SQLite or a PostgreSQL connection from database.connect().
    """
    problems = []
    for description, sql in CHECKS:
        rows = run(conn, sql).fetchall()
        if rows:
            ids = ", ".join(str(row["problem_id"]) for row in rows[:10])
            problems.append(f"{description}: {ids}")

    if not is_postgres(conn):
        # SQLite's own check: rows whose foreign key points to a missing parent row.
        # (PostgreSQL never allows such rows to exist, so it needs no extra check.)
        for row in run(conn, "PRAGMA foreign_key_check").fetchall():
            problems.append(f"Broken foreign key: {row[0]} row {row[1]} points to a missing {row[2]} row")
    return problems


if __name__ == "__main__":
    # Checks the local SQLite file, or the PostgreSQL database if DATABASE_URL is set.
    connection = connect(Config.DATABASE_URL, Config.DATABASE)
    found = find_problems(connection)
    connection.close()
    engine = "PostgreSQL" if Config.DATABASE_URL else "SQLite"
    if found:
        print(f"Problems found ({engine}):")
        for problem in found:
            print("  -", problem)
        sys.exit(1)
    print(f"{engine} database is consistent: all {len(CHECKS)} checks passed.")
