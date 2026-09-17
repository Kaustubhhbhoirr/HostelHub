"""
tests/base.py — Shared setup for every HostelHub test.

Each test:
  1. builds a brand-new temporary database with seed.py (the demo database is never touched)
  2. uses Flask's test client to send requests without starting a server
  3. finishes by running check_database.find_problems(), so every workflow
     test also proves the data is still consistent afterwards
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402
from check_database import find_problems  # noqa: E402
from seed import seed_database  # noqa: E402

# Smallest valid PNG file (1x1 pixel), used to test image upload.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


class HostelHubTestCase(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        seed_database(self.db_path)
        app.config.update(TESTING=True, DATABASE=self.db_path,
                          UPLOAD_FOLDER=os.path.join(self.temp_dir, "uploads"))
        self.client = app.test_client()

    def tearDown(self):
        try:
            conn = sqlite3.connect(self.db_path)
            problems = find_problems(conn)
            conn.close()
            self.assertEqual(problems, [], "database became inconsistent during this test")
        finally:
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ---------------- database helpers ----------------
    def all(self, sql, params=()):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def count(self, sql, params=()):
        return self.all(sql, params)[0][0]

    def run_sql(self, sql, params=()):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    # ---------------- request helpers ----------------
    def csrf_token(self):
        """Put a known CSRF token into the test session (like a real page load would)."""
        with self.client.session_transaction() as session:
            return session.setdefault("csrf_token", "test-csrf-token")

    def post(self, url, data=None, **kwargs):
        """POST with the CSRF token included, exactly like a real HostelHub form."""
        data = dict(data or {})
        data.setdefault("csrf_token", self.csrf_token())
        return self.client.post(url, data=data, **kwargs)

    def login(self, email, password):
        return self.post("/login", {"email": email, "password": password})

    def login_student(self):
        return self.login("student@mes.ac.in", "Student@123")

    def login_warden(self):
        return self.login("warden@mes.ac.in", "Warden@123")

    def login_as(self, user_id):
        """Log in as any seeded student (they all share the demo password)."""
        email = self.one("SELECT email FROM users WHERE id = ?", (user_id,))["email"]
        return self.login(email, "Student@123")

    def logout(self):
        return self.post("/logout")

    # ---------------- demo data lookups ----------------
    def demo_student_id(self):
        return self.one("SELECT id FROM users WHERE email = 'student@mes.ac.in'")["id"]

    def active_bed_of(self, student_id):
        row = self.one("SELECT bed_id FROM allocations WHERE student_id = ? AND status = 'active'", (student_id,))
        return row["bed_id"] if row else None

    def bed_status(self, bed_id):
        return self.one("SELECT status FROM beds WHERE id = ?", (bed_id,))["status"]

    def free_bed_id(self, exclude=()):
        rows = self.all("""SELECT beds.id FROM beds JOIN rooms ON rooms.id = beds.room_id
                           WHERE beds.status = 'available' AND rooms.status = 'active' ORDER BY beds.id""")
        return [row["id"] for row in rows if row["id"] not in exclude][0]

    def bed_with_status(self, status):
        return self.one("SELECT id FROM beds WHERE status = ? ORDER BY id", (status,))["id"]

    def unallocated_student_ids(self):
        rows = self.all("""SELECT id FROM users WHERE role = 'student' AND is_active = 1
                           AND id NOT IN (SELECT student_id FROM allocations WHERE status = 'active')
                           ORDER BY id""")
        return [row["id"] for row in rows]

    def other_allocated_student_id(self):
        return self.one("""SELECT student_id FROM allocations WHERE status = 'active' AND student_id != ?
                           AND student_id NOT IN (SELECT student_id FROM room_change_requests WHERE status = 'Pending')
                           ORDER BY student_id LIMIT 1""", (self.demo_student_id(),))["student_id"]

    def upload_complaint(self, image=None, description="The tube light near the window flickers all night."):
        data = {"category": "Light", "priority": "Medium", "description": description}
        if image is not None:
            data["image"] = image
        return self.post("/student/complaints/new", data, content_type="multipart/form-data")
