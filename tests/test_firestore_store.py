"""The Firestore store, tested against the in-memory stand-in.

These tests prove the guarantees the application relies on: numbered ids, reads
that already see this request's changes, all-or-nothing commits, no lost updates
when two requests change the same document, and the "only one active
allocation" rules (one per bed, one per student).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.firestore_store import CLAIMS, COUNTERS, FirestoreStore  # noqa: E402
from data.store import ConflictError, DuplicateError  # noqa: E402
from fake_firestore import FakeFirestore  # noqa: E402


def a_user(name="Aarav Sharma", email="aarav@student.mes.ac.in", role="student", active=1):
    return {"name": name, "email": email, "role": role, "student_id": "25CE001",
            "phone": None, "department": "Computer Engineering", "year_of_study": 2,
            "is_active": active, "firebase_uid": None, "created_at": "2026-09-01 10:00:00"}


class FirestoreStoreTests(unittest.TestCase):

    def setUp(self):
        self.client = FakeFirestore()
        self.store = FirestoreStore(self.client)

    def stored(self, collection):
        return self.client.data.get(collection, {})

    # ---------------- ids and basic documents ----------------

    def test_ids_are_numbers_that_count_up(self):
        first = self.store.insert("users", a_user())
        second = self.store.insert("users", a_user(email="b@student.mes.ac.in"))
        self.assertEqual((first, second), (1, 2))
        self.store.commit()
        self.assertEqual(sorted(self.stored("users")), ["1", "2"])
        self.assertEqual(self.stored(COUNTERS)["users"]["next"], 3)
        self.assertEqual(self.store.get("users", 1)["id"], 1)

    def test_insert_update_find_and_delete(self):
        user_id = self.store.insert("users", a_user())
        self.store.commit()

        self.store.update("users", user_id, {"phone": "9876543210"})
        self.store.commit()
        self.assertEqual(self.store.get("users", user_id)["phone"], "9876543210")
        self.assertEqual(len(self.store.find("users", role="student")), 1)
        self.assertEqual(self.store.find("users", role="warden"), [])
        self.assertEqual(self.store.count("users", is_active=1), 1)

        self.store.delete("users", user_id)
        self.store.commit()
        self.assertIsNone(self.store.get("users", user_id))
        self.assertEqual(self.stored("users"), {})

    # ---------------- all-or-nothing ----------------

    def test_changes_are_only_written_when_the_request_commits(self):
        self.store.insert("users", a_user())
        # Visible to this request...
        self.assertEqual(len(self.store.find("users")), 1)
        # ...but nothing has reached Firestore yet.
        self.assertEqual(self.stored("users"), {})
        self.assertEqual(self.client.batches, 0)

        self.store.commit()
        self.assertEqual(len(self.stored("users")), 1)
        self.assertEqual(self.client.batches, 1, "one batch = one all-or-nothing write")

    def test_rollback_throws_the_changes_away(self):
        user_id = self.store.insert("users", a_user())
        self.store.commit()
        self.store.update("users", user_id, {"name": "Changed"})
        self.store.delete("users", user_id)
        self.store.rollback()
        self.assertEqual(self.store.get("users", user_id)["name"], "Aarav Sharma")

    def test_inserted_then_updated_in_one_request_is_one_new_document(self):
        user_id = self.store.insert("users", a_user())
        self.store.update("users", user_id, {"phone": "9876543210"})
        self.store.commit()
        self.assertEqual(self.stored("users")[str(user_id)]["phone"], "9876543210")

    # ---------------- two requests at the same moment ----------------

    def test_a_document_changed_meanwhile_is_not_overwritten(self):
        """Two wardens open the same bed; the second save is refused instead of overwriting."""
        bed_id = self.store.insert("beds", {"room_id": 1, "bed_number": 1, "status": "available"})
        self.store.commit()

        first, second = FirestoreStore(self.client), FirestoreStore(self.client)
        self.assertEqual(first.get("beds", bed_id)["status"], "available")
        self.assertEqual(second.get("beds", bed_id)["status"], "available")

        first.update("beds", bed_id, {"status": "occupied"})
        first.commit()

        second.update("beds", bed_id, {"status": "reserved"})
        second.insert("notifications", {"user_id": 1, "title": "t", "message": "", "type": "system",
                                        "link": None, "is_read": 0, "created_at": "2026-09-01 10:00:00"})
        with self.assertRaises(ConflictError):
            second.commit()
        # Nothing from the refused request was written, not even its other changes.
        self.assertEqual(self.stored("beds")[str(bed_id)]["status"], "occupied")
        self.assertEqual(self.stored("notifications"), {})

        # Trying again (a new request) reads the current state and works.
        retry = FirestoreStore(self.client)
        self.assertEqual(retry.get("beds", bed_id)["status"], "occupied")

    def test_the_first_read_is_the_one_that_counts(self):
        """A decision made on an old copy is not rescued by reading the document again."""
        bed_id = self.store.insert("beds", {"room_id": 1, "bed_number": 1, "status": "available"})
        self.store.commit()
        request = FirestoreStore(self.client)
        request.get("beds", bed_id)                        # "the bed is available"
        other = FirestoreStore(self.client)
        other.update("beds", bed_id, {"status": "maintenance"})
        other.commit()
        request.update("beds", bed_id, {"status": "occupied"})   # re-reads, but the check uses the first copy
        with self.assertRaises(ConflictError):
            request.commit()
        self.assertEqual(self.stored("beds")[str(bed_id)]["status"], "maintenance")

    def test_two_requests_cannot_allocate_the_same_bed(self):
        first, second = FirestoreStore(self.client), FirestoreStore(self.client)
        first.claim("bed-5")
        with self.assertRaises(DuplicateError):
            second.claim("bed-5")
        first.commit()
        second.rollback()
        self.assertIn("bed-5", self.stored(CLAIMS))

    # ---------------- the "only one" rules ----------------

    def test_a_bed_cannot_be_claimed_twice(self):
        self.store.claim("bed-5")
        with self.assertRaises(DuplicateError):
            self.store.claim("bed-5")
        self.assertIn("bed-5", self.stored(CLAIMS))

    def test_claims_taken_in_a_failed_request_are_given_back(self):
        self.store.claim("bed-5")
        self.store.rollback()
        self.assertEqual(self.stored(CLAIMS), {})
        self.store.claim("bed-5")      # free again
        self.store.commit()
        self.assertIn("bed-5", self.stored(CLAIMS))

    def test_a_bed_released_and_taken_again_in_one_request_is_allowed(self):
        """A room change frees the student's slot and takes it again in the same request."""
        self.store.claim("student-7")
        self.store.commit()

        self.store.release("student-7")
        self.store.claim("student-7")          # must not raise
        self.store.commit()
        self.assertIn("student-7", self.stored(CLAIMS))

    def test_release_only_takes_effect_on_commit(self):
        self.store.claim("bed-9")
        self.store.commit()
        self.store.release("bed-9")
        self.assertIn("bed-9", self.stored(CLAIMS), "still held until the request commits")
        self.store.commit()
        self.assertNotIn("bed-9", self.stored(CLAIMS))


if __name__ == "__main__":
    unittest.main()
