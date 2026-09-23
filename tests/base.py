"""
tests/base.py — Shared setup for every HostelHub test.

HostelHub keeps all of its data in Firebase. A test cannot call the real
Firestore (that needs a project, credentials and an internet connection), so each
test runs against the in-memory stand-in in tests/fake_firestore.py, filled with
the miniature hostel from tests/fixtures.py. Complaint photos go to a temporary
uploads folder per test.

Signing in also goes through Firebase, so the tests replace the token check with
a small stand-in: self.login(email) behaves exactly like a real sign-in, except
that Firebase's answer is supplied by the test.

Each test:
  1. gets a brand-new in-memory database with the fixture data
  2. uses Flask's test client to send requests without starting a server
  3. finishes by running check_database.find_problems(), so every workflow
     test also proves the data stayed consistent afterwards
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tests must never use real deployment settings from the developer's terminal.
for variable in ("VERCEL", "HOSTELHUB_ENV", "HOSTELHUB_DEBUG"):
    os.environ[variable] = ""
# The app refuses to start without Firebase credentials, so give it a fake one:
# every Firebase call is replaced in the tests anyway.
os.environ.setdefault("FIREBASE_SERVICE_ACCOUNT", "{}")

# Starting the real Firebase Admin SDK needs a real service-account key, so it is
# skipped while the app is imported. Every Firebase call is replaced below anyway.
import routes.auth  # noqa: E402

with mock.patch.object(routes.auth, "init_firebase", return_value=True):
    from app import app  # noqa: E402

from check_database import find_problems  # noqa: E402
from data.firestore_store import FirestoreStore  # noqa: E402
from fake_firestore import FakeFirestore  # noqa: E402
from fixtures import STUDENT_EMAIL, WARDEN_EMAIL, build  # noqa: E402
import query_helper  # noqa: E402

# Smallest valid PNG file (1x1 pixel), used to test image upload.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


class FakeAuth:
    """Firebase Authentication stand-in: the few Admin SDK calls firebase_accounts.py makes."""

    class UserNotFoundError(Exception):
        pass

    class EmailAlreadyExistsError(Exception):
        pass

    class Account:
        def __init__(self, uid, email, **fields):
            self.uid, self.email = uid, email
            self.password = fields.get("password")
            self.display_name = fields.get("display_name")
            self.email_verified = fields.get("email_verified", False)
            self.disabled = fields.get("disabled", False)

    def __init__(self):
        self.accounts = {}          # uid -> Account
        self.next_number = 1

    def get_user_by_email(self, email):
        for account in self.accounts.values():
            if account.email == email:
                return account
        raise self.UserNotFoundError(email)

    def create_user(self, email, **fields):
        if any(account.email == email for account in self.accounts.values()):
            raise self.EmailAlreadyExistsError(email)
        uid = f"fake-uid-{self.next_number}"
        self.next_number += 1
        self.accounts[uid] = self.Account(uid, email, **fields)
        return self.accounts[uid]

    def update_user(self, uid, **fields):
        if uid not in self.accounts:
            raise self.UserNotFoundError(uid)
        if "email" in fields and any(account.email == fields["email"] and account.uid != uid
                                     for account in self.accounts.values()):
            raise self.EmailAlreadyExistsError(fields["email"])
        for name, value in fields.items():
            setattr(self.accounts[uid], name, value)
        return self.accounts[uid]

    def delete_user(self, uid):
        if self.accounts.pop(uid, None) is None:
            raise self.UserNotFoundError(uid)


class HostelHubTestCase(unittest.TestCase):

    def setUp(self):
        self.firestore = FakeFirestore()
        self.auth = FakeAuth()
        self.data = build(self.store())

        for target, replacement in [
                ("data.create_store", lambda settings=None: FirestoreStore(self.firestore)),
                ("firebase_accounts.firebase_auth", lambda: self.auth)]:
            patcher = mock.patch(target, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        # Tests may change settings; put them back afterwards so no test affects the next one.
        saved_config = dict(app.config)
        self.addCleanup(lambda: (app.config.clear(), app.config.update(saved_config)))
        # Complaint photos go to a temporary uploads folder that is removed afterwards.
        self.upload_folder = tempfile.mkdtemp(prefix="hostelhub-uploads-")
        self.addCleanup(shutil.rmtree, self.upload_folder, ignore_errors=True)
        app.config.update(TESTING=True, COMPLAINT_IMAGE_STORAGE="local", UPLOAD_FOLDER=self.upload_folder)
        self.client = app.test_client()

    def tearDown(self):
        problems = find_problems(self.store())
        self.assertEqual(problems, [], "database became inconsistent during this test")

    # ---------------- database helpers ----------------
    def store(self):
        return FirestoreStore(self.firestore)

    def documents(self, collection, **filters):
        return self.store().find(collection, **filters)

    def document(self, collection, doc_id):
        return self.store().get(collection, doc_id)

    def user_by_email(self, email):
        return self.store().first("users", email=email)

    # SQL-style one-liners for checking the stored documents (see query_helper.py).
    def all(self, sql, params=()):
        return query_helper.run(self.store(), sql, params)

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def count(self, sql, params=()):
        """Run a query that selects one number, e.g. SELECT COUNT(*) FROM beds ..."""
        return list(self.one(sql, params).values())[0]

    def run_sql(self, sql, params=()):
        return query_helper.run(self.store(), sql, params)

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

    def login(self, email, provider="password", verified=True, uid=None, name=None, follow=True):
        """Sign in the way the real login page does, with Firebase's answer supplied here."""
        user = self.user_by_email(email)
        claims = {
            "email": email,
            "email_verified": verified,
            "uid": uid or (user["firebase_uid"] if user else f"uid-{email}"),
            "name": name or (user["name"] if user else None),
            "firebase": {"sign_in_provider": provider},
        }
        with mock.patch("routes.auth.verify_id_token", return_value=claims):
            return self.post("/firebase-login", {"idToken": "test-token"})

    def login_student(self):
        return self.login(STUDENT_EMAIL)

    def login_warden(self):
        return self.login(WARDEN_EMAIL)

    def login_as(self, user_id):
        """Log in as any user in the fixture data."""
        return self.login(self.document("users", user_id)["email"])

    def logout(self):
        return self.post("/logout")

    # ---------------- fixture lookups ----------------
    def demo_student_id(self):
        return self.data["student_ids"][0]

    def active_bed_of(self, student_id):
        allocation = self.store().first("allocations", student_id=student_id, status="active")
        return allocation["bed_id"] if allocation else None

    def bed_status(self, bed_id):
        return self.document("beds", bed_id)["status"]

    def free_bed_id(self, exclude=()):
        store = self.store()
        active_rooms = {room["id"] for room in store.find("rooms", status="active")}
        beds = sorted((bed for bed in store.find("beds", status="available")
                       if bed["room_id"] in active_rooms and bed["id"] not in exclude),
                      key=lambda bed: bed["id"])
        return beds[0]["id"]

    def bed_with_status(self, status):
        beds = sorted(self.store().find("beds", status=status), key=lambda bed: bed["id"])
        return beds[0]["id"]

    def unallocated_student_ids(self):
        store = self.store()
        allocated = {row["student_id"] for row in store.find("allocations", status="active")}
        return sorted(student["id"] for student in store.find("users", role="student", is_active=1)
                      if student["id"] not in allocated)

    def other_allocated_student_id(self):
        store = self.store()
        pending = {row["student_id"] for row in store.find("room_change_requests", status="Pending")}
        for allocation in sorted(store.find("allocations", status="active"), key=lambda row: row["id"]):
            if allocation["student_id"] != self.demo_student_id() and allocation["student_id"] not in pending:
                return allocation["student_id"]
        raise AssertionError("no other allocated student in the fixture data")

    def upload_complaint(self, image=None, description="The tube light near the window flickers all night."):
        data = {"category": "Light", "priority": "Medium", "description": description}
        if image is not None:
            data["image"] = image
        return self.post("/student/complaints/new", data, content_type="multipart/form-data")

    def stored_photos(self):
        return sorted(os.listdir(self.upload_folder))
