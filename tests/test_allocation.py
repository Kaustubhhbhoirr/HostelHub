"""Bed allocation, vacating, database-level protection and room change transactions."""

import sqlite3
import unittest
from unittest import mock

from base import HostelHubTestCase
from database import IntegrityError


class AllocationTests(HostelHubTestCase):

    def allocate(self, bed_id, student_id):
        return self.post(f"/warden/rooms/beds/{bed_id}/allocate", {"student_id": student_id}, follow_redirects=True)

    def active_count(self, column, value):
        return self.count(f"SELECT COUNT(*) FROM allocations WHERE {column} = ? AND status = 'active'", (value,))

    def test_normal_allocation_updates_bed_map_and_dashboard(self):
        student_id = self.unallocated_student_ids()[0]
        bed_id = self.free_bed_id()
        self.login_warden()
        before = self.client.get("/warden/dashboard").get_data(as_text=True)
        available_before = self.count("SELECT COUNT(*) FROM beds WHERE status = 'available'")

        response = self.allocate(bed_id, student_id)
        self.assertIn(b"allocated to", response.data)
        self.assertEqual(self.bed_status(bed_id), "occupied")
        self.assertEqual(self.active_bed_of(student_id), bed_id)
        # The student is told about the new room.
        self.assertEqual(self.one("SELECT title FROM notifications WHERE user_id = ? ORDER BY id DESC",
                                  (student_id,))["title"], "Room allocated")
        # The map shows the bed as occupied (red) straight away — read from the database.
        self.assertIn(f'data-bed-id="{bed_id}"', response.get_data(as_text=True))
        self.assertRegex(response.get_data(as_text=True),
                         rf'class="bed bed-occupied"\s+data-bed-id="{bed_id}"')
        # Dashboard numbers changed.
        after = self.client.get("/warden/dashboard").get_data(as_text=True)
        self.assertNotEqual(before, after)
        self.assertIn(f'<div class="stat-value">{available_before - 1}</div>', after)

    def test_invalid_allocations_fail_safely(self):
        waiting = self.unallocated_student_ids()
        allocated_student = self.other_allocated_student_id()
        self.login_warden()
        cases = [
            (self.bed_with_status("occupied"), waiting[0], b"is not available"),
            (self.bed_with_status("maintenance"), waiting[0], b"not"),
            (self.bed_with_status("unavailable"), waiting[0], b"not"),
            (self.bed_with_status("reserved"), waiting[0], b"is not available"),
            (self.free_bed_id(), allocated_student, b"already has a bed"),
            (self.free_bed_id(), 99999, b"Student not found"),
            (self.free_bed_id(), 1, b"Student not found"),      # user 1 is the warden
            (self.free_bed_id(), "", b"Please choose a student"),
            (self.free_bed_id(), "abc", b"Please choose a student"),
        ]
        for bed_id, student_id, message in cases:
            status_before = self.bed_status(bed_id)
            response = self.allocate(bed_id, student_id)
            self.assertIn(message, response.data, (bed_id, student_id))
            self.assertEqual(self.bed_status(bed_id), status_before)
        self.assertEqual(self.post("/warden/rooms/beds/99999/allocate", {"student_id": waiting[0]}).status_code, 404)
        for student_id in waiting:
            self.assertIsNone(self.active_bed_of(student_id))

    def test_deactivated_student_cannot_be_allocated(self):
        student_id = self.unallocated_student_ids()[0]
        self.run_sql("UPDATE users SET is_active = 0 WHERE id = ?", (student_id,))
        self.login_warden()
        self.assertIn(b"deactivated", self.allocate(self.free_bed_id(), student_id).data)

    def test_vacate_and_vacate_again(self):
        student_id = self.other_allocated_student_id()
        bed_id = self.active_bed_of(student_id)
        self.login_warden()
        self.post(f"/warden/rooms/beds/{bed_id}/vacate")
        self.assertEqual(self.bed_status(bed_id), "available")
        self.assertIsNone(self.active_bed_of(student_id))
        ended = self.one("SELECT * FROM allocations WHERE student_id = ? AND bed_id = ? ORDER BY id DESC",
                         (student_id, bed_id))
        self.assertEqual(ended["status"], "ended")
        self.assertIsNotNone(ended["ended_at"])
        # Vacating again (or vacating any free bed) changes nothing.
        response = self.post(f"/warden/rooms/beds/{bed_id}/vacate", follow_redirects=True)
        self.assertIn(b"not occupied", response.data)
        self.assertEqual(self.count("SELECT COUNT(*) FROM allocations WHERE student_id = ?", (student_id,)), 1)

    def test_manual_bed_status_rules(self):
        self.login_warden()
        free = self.free_bed_id()
        self.post(f"/warden/rooms/beds/{free}/status", {"status": "maintenance"})
        self.assertEqual(self.bed_status(free), "maintenance")
        self.post(f"/warden/rooms/beds/{free}/status", {"status": "available"})
        self.assertEqual(self.bed_status(free), "available")
        occupied = self.bed_with_status("occupied")
        self.post(f"/warden/rooms/beds/{occupied}/status", {"status": "available"})
        self.assertEqual(self.bed_status(occupied), "occupied")
        self.post(f"/warden/rooms/beds/{free}/status", {"status": "occupied"})   # not a manual status
        self.assertEqual(self.bed_status(free), "available")
        inactive_room_bed = self.bed_with_status("unavailable")
        self.post(f"/warden/rooms/beds/{inactive_room_bed}/status", {"status": "available"})
        self.assertEqual(self.bed_status(inactive_room_bed), "unavailable")

    def test_student_cannot_allocate_vacate_or_change_beds(self):
        self.login_student()
        free = self.free_bed_id()
        occupied = self.bed_with_status("occupied")
        self.assertEqual(self.post(f"/warden/rooms/beds/{free}/allocate",
                                   {"student_id": self.demo_student_id()}).status_code, 403)
        self.assertEqual(self.post(f"/warden/rooms/beds/{occupied}/vacate").status_code, 403)
        self.assertEqual(self.post(f"/warden/rooms/beds/{free}/status", {"status": "unavailable"}).status_code, 403)
        self.assertEqual((self.bed_status(free), self.bed_status(occupied)), ("available", "occupied"))

    def test_database_blocks_double_allocation_by_itself(self):
        """The partial UNIQUE indexes work even if the Python checks were skipped."""
        occupied_bed = self.bed_with_status("occupied")
        waiting = self.unallocated_student_ids()[0]
        with self.assertRaises(IntegrityError):
            self.run_sql("INSERT INTO allocations (student_id, bed_id, allocated_at, status) "
                         "VALUES (?, ?, '2026-01-01 00:00:00', 'active')", (waiting, occupied_bed))
        with self.assertRaises(IntegrityError):
            self.run_sql("INSERT INTO allocations (student_id, bed_id, allocated_at, status) "
                         "VALUES (?, ?, '2026-01-01 00:00:00', 'active')", (self.demo_student_id(), self.free_bed_id()))
        # Ended rows are history, so many are allowed.
        self.run_sql("INSERT INTO allocations (student_id, bed_id, allocated_at, ended_at, status) "
                     "VALUES (?, ?, '2025-01-01 00:00:00', '2025-06-01 00:00:00', 'ended')", (waiting, occupied_bed))

    def test_foreign_keys_and_check_constraints(self):
        with self.assertRaises(IntegrityError):
            self.run_sql("INSERT INTO allocations (student_id, bed_id, allocated_at) VALUES (99999, 1, 'now')")
        with self.assertRaises(IntegrityError):
            self.run_sql("INSERT INTO complaints (student_id, room_id, category, description, created_at, updated_at) "
                         "VALUES (2, 99999, 'Fan', 'x', 'now', 'now')")
        with self.assertRaises(IntegrityError):
            self.run_sql("UPDATE beds SET status = 'broken' WHERE id = 1")
        with self.assertRaises(IntegrityError):
            self.run_sql("UPDATE users SET role = 'admin' WHERE id = 2")


class RoomChangeTests(HostelHubTestCase):

    def request_move(self, bed_id=None):
        """The demo student asks to move (optionally to a specific bed)."""
        self.login_student()
        self.post("/student/room-requests/new", {
            "reason": "Need a quieter room for studies", "requested_bed_id": str(bed_id or ""),
            "details": "The corridor outside my room is very noisy at night."})
        self.logout()
        return self.one("SELECT * FROM room_change_requests WHERE student_id = ? AND status = 'Pending'",
                        (self.demo_student_id(),))

    def decide(self, request_id, **data):
        self.login_warden()
        return self.post(f"/warden/room-requests/{request_id}/decide", data, follow_redirects=True)

    def test_successful_approval_moves_the_student(self):
        student_id = self.demo_student_id()
        old_bed = self.active_bed_of(student_id)
        target = self.free_bed_id()
        room_request = self.request_move(target)
        self.assertEqual(self.bed_status(target), "reserved")           # yellow on the map

        self.login_warden()
        page = self.client.get("/warden/rooms/map?block=A&floor=1").get_data(as_text=True)
        self.assertRegex(page, rf'class="bed bed-reserved"\s+data-bed-id="{target}"')
        self.logout()

        response = self.decide(room_request["id"], action="approve", bed_id=target, remarks="Approved.")
        self.assertIn(b"Room change approved", response.data)
        self.assertEqual(self.bed_status(old_bed), "available")
        self.assertEqual(self.bed_status(target), "occupied")
        self.assertEqual([row["bed_id"] for row in self.all(
            "SELECT bed_id FROM allocations WHERE student_id = ? AND status = 'active'", (student_id,))], [target])
        row = self.one("SELECT * FROM room_change_requests WHERE id = ?", (room_request["id"],))
        self.assertEqual((row["status"], row["assigned_bed_id"]), ("Approved", target))
        self.assertEqual(self.one("SELECT title FROM notifications WHERE user_id = ? ORDER BY id DESC",
                                  (student_id,))["title"], "Room change approved")

    def test_failure_during_approval_rolls_everything_back(self):
        student_id = self.demo_student_id()
        old_bed = self.active_bed_of(student_id)
        target = self.free_bed_id()
        room_request = self.request_move(target)
        notifications_before = self.count("SELECT COUNT(*) FROM notifications")
        allocations_before = self.count("SELECT COUNT(*) FROM allocations")

        # Make the LAST step (creating the notification) fail, after the bed moves already ran.
        with mock.patch("routes.requests.create_notification", side_effect=sqlite3.OperationalError("disk I/O error")):
            response = self.decide(room_request["id"], action="approve", bed_id=target)
        self.assertIn(b"No changes were saved", response.data)

        self.assertEqual(self.active_bed_of(student_id), old_bed)
        self.assertEqual(self.bed_status(old_bed), "occupied")
        self.assertEqual(self.bed_status(target), "reserved")
        self.assertEqual(self.one("SELECT status FROM room_change_requests WHERE id = ?",
                                  (room_request["id"],))["status"], "Pending")
        self.assertEqual(self.count("SELECT COUNT(*) FROM notifications"), notifications_before)
        self.assertEqual(self.count("SELECT COUNT(*) FROM allocations"), allocations_before)

        # After the failure, a normal approval still works.
        self.post("/logout")
        response = self.decide(room_request["id"], action="approve", bed_id=target)
        self.assertIn(b"Room change approved", response.data)

    def test_rejection_releases_bed_and_keeps_allocation(self):
        student_id = self.demo_student_id()
        old_bed = self.active_bed_of(student_id)
        target = self.free_bed_id()
        room_request = self.request_move(target)
        response = self.decide(room_request["id"], action="reject", remarks="")
        self.assertIn(b"add a remark", response.data)                   # remarks required
        self.decide(room_request["id"], action="reject", remarks="No beds free in that block.")
        self.assertEqual(self.one("SELECT status FROM room_change_requests WHERE id = ?",
                                  (room_request["id"],))["status"], "Rejected")
        self.assertEqual(self.bed_status(target), "available")
        self.assertEqual(self.active_bed_of(student_id), old_bed)
        self.assertEqual(self.one("SELECT title FROM notifications WHERE user_id = ? ORDER BY id DESC",
                                  (student_id,))["title"], "Room change request rejected")

    def test_processed_request_cannot_be_processed_again(self):
        room_request = self.request_move(self.free_bed_id())
        self.decide(room_request["id"], action="reject", remarks="No.")
        response = self.post(f"/warden/room-requests/{room_request['id']}/decide",
                             {"action": "approve", "bed_id": room_request["requested_bed_id"]}, follow_redirects=True)
        self.assertIn(b"already been processed", response.data)
        self.assertEqual(self.active_bed_of(self.demo_student_id()), room_request["current_bed_id"])

    def test_invalid_approvals_change_nothing(self):
        pending = self.all("SELECT * FROM room_change_requests WHERE status = 'Pending' ORDER BY id")
        first = pending[0]
        old_bed = self.active_bed_of(first["student_id"])
        cases = [
            (pending[1]["requested_bed_id"], b"is not available"),   # another student's reserved bed
            (self.bed_with_status("occupied"), b"is not available"),
            (self.bed_with_status("maintenance"), b"not"),
            (99999, b"does not exist"),
            ("", b"Choose the bed"),
        ]
        for bed_id, message in cases:
            response = self.decide(first["id"], action="approve", bed_id=bed_id)
            self.assertIn(message, response.data, bed_id)
            self.assertEqual(self.one("SELECT status FROM room_change_requests WHERE id = ?", (first["id"],))["status"],
                             "Pending")
            self.assertEqual(self.bed_status(first["requested_bed_id"]), "reserved")
            self.assertEqual(self.active_bed_of(first["student_id"]), old_bed)
        response = self.decide(first["id"], action="delete-everything")
        self.assertIn(b"Unknown action", response.data)

    def test_student_request_rules(self):
        self.login_student()
        # A bed that is not free cannot be requested.
        response = self.post("/student/room-requests/new", {
            "reason": "Other", "requested_bed_id": str(self.bed_with_status("occupied")),
            "details": "Please move me to this bed, it is closer to my friends."})
        self.assertIn(b"no longer available", response.data)
        response = self.post("/student/room-requests/new", {"reason": "Because", "details": "short"})
        self.assertIn(b"choose a reason", response.data)
        self.assertEqual(self.count("SELECT COUNT(*) FROM room_change_requests WHERE student_id = ? AND status = 'Pending'",
                                    (self.demo_student_id(),)), 0)
        self.logout()

        room_request = self.request_move(self.free_bed_id())
        self.login_student()
        response = self.client.get("/student/room-requests/new", follow_redirects=True)
        self.assertIn(b"already have a pending", response.data)
        # Cancelling frees the reserved bed.
        self.post(f"/student/room-requests/{room_request['id']}/cancel")
        self.assertEqual(self.bed_status(room_request["requested_bed_id"]), "available")
        self.logout()

        # A student with no bed cannot ask to change rooms.
        self.login_as(self.unallocated_student_ids()[0])
        response = self.client.get("/student/room-requests/new", follow_redirects=True)
        self.assertIn(b"need a room allocation", response.data)

    def test_vacating_a_student_cancels_their_pending_request(self):
        pending = self.one("SELECT * FROM room_change_requests WHERE status = 'Pending'")
        bed_id = self.active_bed_of(pending["student_id"])
        self.login_warden()
        self.post(f"/warden/rooms/beds/{bed_id}/vacate")
        self.assertEqual(self.one("SELECT status FROM room_change_requests WHERE id = ?", (pending["id"],))["status"],
                         "Cancelled")
        self.assertEqual(self.bed_status(pending["requested_bed_id"]), "available")


if __name__ == "__main__":
    unittest.main()
