"""Student CRUD, room CRUD, college requests, notifications, dashboard numbers, search and seed data."""

import unittest

from base import HostelHubTestCase
from routes.warden import percent


class StudentCrudTests(HostelHubTestCase):
    FORM = {"name": "Test Student", "email": "test.student@mes.ac.in", "student_id": "26CE999",
            "phone": "9123456789", "department": "Computer Engineering", "year_of_study": "1",
            "password": "Password123"}

    def test_create_edit_delete(self):
        self.login_warden()
        self.post("/warden/students/new", self.FORM)
        created = self.one("SELECT * FROM users WHERE email = 'test.student@mes.ac.in'")
        self.assertEqual((created["role"], created["student_id"]), ("student", "26CE999"))
        self.assertNotEqual(created["password_hash"], "Password123")

        self.post(f"/warden/students/{created['id']}/edit", {**self.FORM, "name": "Renamed Student", "password": ""})
        updated = self.one("SELECT * FROM users WHERE id = ?", (created["id"],))
        self.assertEqual(updated["name"], "Renamed Student")
        self.assertEqual(updated["password_hash"], created["password_hash"])   # blank = keep password

        response = self.post(f"/warden/students/{created['id']}/delete", follow_redirects=True)
        self.assertIn(b"was deleted", response.data)
        self.assertIsNone(self.one("SELECT id FROM users WHERE id = ?", (created["id"],)))

    def test_invalid_student_forms(self):
        self.login_warden()
        before = self.count("SELECT COUNT(*) FROM users")
        cases = [
            ({"email": "x@gmail.com"}, b"college address"),
            ({"email": "@mes.ac.in"}, b"college address"),
            ({"email": "a b@mes.ac.in"}, b"college address"),
            ({"phone": "12345"}, b"exactly 10 digits"),
            ({"department": "Astrology"}, b"choose a department"),
            ({"year_of_study": "7"}, b"year of study"),
            ({"password": "short"}, b"at least 8 characters"),
            ({"name": ""}, b"full name"),
            ({"email": "student@mes.ac.in", "student_id": "NEW001"}, b"already uses this email"),
            ({"student_id": "25CE001"}, b"already has this student ID"),
        ]
        for change, message in cases:
            response = self.post("/warden/students/new", {**self.FORM, **change})
            self.assertIn(message, response.data, change)
        self.assertEqual(self.count("SELECT COUNT(*) FROM users"), before)

    def test_student_with_history_is_deactivated_not_deleted(self):
        student_id = self.demo_student_id()
        bed_id = self.active_bed_of(student_id)
        self.login_warden()
        response = self.post(f"/warden/students/{student_id}/delete", follow_redirects=True)
        self.assertIn(b"cannot be deleted", response.data)
        self.assertEqual(self.bed_status(bed_id), "occupied")        # rollback kept the bed

        self.post(f"/warden/students/{student_id}/toggle-active")
        self.assertEqual(self.one("SELECT is_active FROM users WHERE id = ?", (student_id,))["is_active"], 0)
        self.assertEqual(self.bed_status(bed_id), "available")
        self.post(f"/warden/students/{student_id}/toggle-active")
        self.assertEqual(self.one("SELECT is_active FROM users WHERE id = ?", (student_id,))["is_active"], 1)

    def test_student_search_and_filters(self):
        self.login_warden()
        html = self.client.get("/warden/students?q=AARAV").get_data(as_text=True)
        self.assertIn("Aarav Sharma", html)                                   # case-insensitive
        self.assertIn("Aarav Sharma", self.client.get("/warden/students?q=25CE0").get_data(as_text=True))
        waiting = len(self.unallocated_student_ids())
        html = self.client.get("/warden/students?allocation=unallocated").get_data(as_text=True)
        self.assertEqual(html.count('data-href="/warden/students/'), waiting)
        for query in ("?q=%25", "?q=_", "?q='\"<>;--", "?block=Z", "?allocation=hacker", "?q=nobody-by-this-name"):
            self.assertEqual(self.client.get("/warden/students" + query).status_code, 200, query)
        self.assertIn(b"No students found", self.client.get("/warden/students?q=nobody-by-this-name").data)


class RoomCrudTests(HostelHubTestCase):
    ROOM = {"block": "d", "floor": "1", "room_number": "101", "capacity": "3", "status": "active"}

    def test_room_create_update_delete(self):
        self.login_warden()
        self.post("/warden/rooms/new", self.ROOM)
        room = self.one("SELECT * FROM rooms WHERE block = 'D' AND room_number = '101'")
        self.assertEqual(self.count("SELECT COUNT(*) FROM beds WHERE room_id = ?", (room["id"],)), 3)
        self.assertIn(b"already exists", self.post("/warden/rooms/new", self.ROOM).data)

        self.post(f"/warden/rooms/{room['id']}/edit", {**self.ROOM, "capacity": "4"})
        self.assertEqual(self.count("SELECT COUNT(*) FROM beds WHERE room_id = ?", (room["id"],)), 4)
        self.post(f"/warden/rooms/{room['id']}/edit", {**self.ROOM, "capacity": "2"})
        self.assertEqual(self.count("SELECT COUNT(*) FROM beds WHERE room_id = ?", (room["id"],)), 2)
        self.post(f"/warden/rooms/{room['id']}/edit", {**self.ROOM, "status": "inactive"})
        self.assertEqual(self.count("SELECT COUNT(*) FROM beds WHERE room_id = ? AND status = 'unavailable'",
                                    (room["id"],)), 3)

        self.post(f"/warden/rooms/{room['id']}/delete")
        self.assertIsNone(self.one("SELECT id FROM rooms WHERE id = ?", (room["id"],)))
        self.assertEqual(self.count("SELECT COUNT(*) FROM beds WHERE room_id = ?", (room["id"],)), 0)

    def test_room_rules(self):
        self.login_warden()
        occupied_room = self.one("""SELECT rooms.* FROM rooms JOIN beds ON beds.room_id = rooms.id
                                    WHERE beds.status = 'occupied' LIMIT 1""")
        form = {"block": occupied_room["block"], "floor": str(occupied_room["floor"]),
                "room_number": occupied_room["room_number"], "capacity": str(occupied_room["capacity"])}
        self.assertIn(b"Move the students out first", self.post(f"/warden/rooms/{occupied_room['id']}/edit",
                                                                 {**form, "status": "inactive"}).data)
        self.assertIn(b"has allocation or complaint history",
                      self.post(f"/warden/rooms/{occupied_room['id']}/delete", follow_redirects=True).data)
        # A free bed with allocation history cannot be removed by reducing capacity.
        self.post("/warden/rooms/new", self.ROOM)                                   # D-101, 3 beds
        room = self.one("SELECT * FROM rooms WHERE block = 'D' AND room_number = '101'")
        bed3 = self.one("SELECT id FROM beds WHERE room_id = ? AND bed_number = 3", (room["id"],))["id"]
        student_id = self.unallocated_student_ids()[0]
        self.post(f"/warden/rooms/beds/{bed3}/allocate", {"student_id": student_id})
        self.post(f"/warden/rooms/beds/{bed3}/vacate")                               # bed 3 free, but has history
        response = self.post(f"/warden/rooms/{room['id']}/edit", {**self.ROOM, "capacity": "2"})
        self.assertIn(b"has allocation history", response.data)
        self.assertEqual(self.count("SELECT COUNT(*) FROM beds WHERE room_id = ?", (room["id"],)), 3)
        self.assertEqual(self.one("SELECT capacity FROM rooms WHERE id = ?", (room["id"],))["capacity"], 3)
        self.assertEqual(self.one("SELECT status FROM rooms WHERE id = ?", (occupied_room["id"],))["status"], "active")
        for change, message in (({"capacity": "9"}, b"between 1 and 6"), ({"block": "A1"}, b"one or two letters"),
                                ({"floor": "-1"}, b"Floor must be"), ({"room_number": "../1"}, b"letters and digits"),
                                ({"status": "demolished"}, b"Invalid room status")):
            self.assertIn(message, self.post("/warden/rooms/new", {**self.ROOM, **change}).data, change)

    def test_room_search_and_map_navigation(self):
        self.login_warden()
        for query in ("A-201", "a201", "201"):
            html = self.client.get(f"/warden/rooms/?q={query}").get_data(as_text=True)
            self.assertIn("A-201", html, query)
        self.assertIn(b"No rooms found", self.client.get("/warden/rooms/?q=ZZ999").data)
        html = self.client.get("/warden/rooms/map?block=B&floor=2").get_data(as_text=True)
        self.assertIn("B-201", html)
        self.assertNotIn("A-201", html)
        # Invalid block/floor fall back to the first valid ones instead of crashing.
        self.assertIn("A-101", self.client.get("/warden/rooms/map?block=Q&floor=abc").get_data(as_text=True))
        bed_buttons = self.client.get("/warden/rooms/map?block=C&floor=1").get_data(as_text=True).count('class="bed bed-')
        self.assertEqual(bed_buttons, self.count("SELECT COUNT(*) FROM beds JOIN rooms ON rooms.id = beds.room_id "
                                                 "WHERE rooms.block = 'C' AND rooms.floor = 1"))


class CollegeRequestTests(HostelHubTestCase):
    FORM = {"request_type": "Replacement", "asset": "Study Table", "location": "Block B, Room 101",
            "quantity": "2", "priority": "Medium", "description": "Two study tables have broken legs.",
            "action": "draft"}

    def test_draft_edit_send_and_track(self):
        self.login_warden()
        self.post("/warden/college-requests/new", self.FORM)
        draft = self.one("SELECT * FROM college_maintenance_requests ORDER BY id DESC LIMIT 1")
        self.assertEqual((draft["status"], draft["quantity"], draft["asset"]), ("Draft", 2, "Study Table"))
        self.assertIn(b"Study Table", self.client.get("/warden/college-requests/").data)

        self.post(f"/warden/college-requests/{draft['id']}/edit", {**self.FORM, "quantity": "3", "action": "send"})
        sent = self.one("SELECT * FROM college_maintenance_requests WHERE id = ?", (draft["id"],))
        self.assertEqual((sent["status"], sent["quantity"]), ("Sent to College", 3))

        self.post(f"/warden/college-requests/{draft['id']}/status", {"status": "Approved", "college_remarks": "OK."})
        response = self.post(f"/warden/college-requests/{draft['id']}/status", {"status": "Draft"}, follow_redirects=True)
        self.assertIn(b"cannot go back to Draft", response.data)
        self.post(f"/warden/college-requests/{draft['id']}/delete")
        final = self.one("SELECT * FROM college_maintenance_requests WHERE id = ?", (draft["id"],))
        self.assertEqual((final["status"], final["college_remarks"]), ("Approved", "OK."))
        detail = self.client.get(f"/warden/college-requests/{draft['id']}").get_data(as_text=True)
        self.assertIn("Block B, Room 101", detail)

    def test_draft_can_be_deleted(self):
        self.login_warden()
        self.post("/warden/college-requests/new", self.FORM)
        draft = self.one("SELECT id FROM college_maintenance_requests ORDER BY id DESC LIMIT 1")
        self.post(f"/warden/college-requests/{draft['id']}/delete")
        self.assertIsNone(self.one("SELECT id FROM college_maintenance_requests WHERE id = ?", (draft["id"],)))

    def test_escalating_a_complaint_notifies_the_student(self):
        complaint = self.one("SELECT * FROM complaints WHERE status = 'Submitted' LIMIT 1")
        self.login_warden()
        form = self.client.get(f"/warden/college-requests/new?complaint_id={complaint['id']}").get_data(as_text=True)
        self.assertIn(f'name="complaint_id" value="{complaint["id"]}"', form)
        self.post("/warden/college-requests/new", {**self.FORM, "complaint_id": complaint["id"], "action": "send"})
        self.assertEqual(self.one("SELECT title FROM notifications WHERE user_id = ? ORDER BY id DESC",
                                  (complaint["student_id"],))["title"], "Complaint escalated to college")

    def test_invalid_college_forms(self):
        self.login_warden()
        before = self.count("SELECT COUNT(*) FROM college_maintenance_requests")
        for change, message in (({"quantity": "0"}, b"between 1 and 500"), ({"quantity": "two"}, b"between 1 and 500"),
                                ({"request_type": "Bribe"}, b"request type"), ({"asset": ""}, b"asset"),
                                ({"description": "short"}, b"at least 15")):
            self.assertIn(message, self.post("/warden/college-requests/new", {**self.FORM, **change}).data, change)
        self.assertEqual(self.count("SELECT COUNT(*) FROM college_maintenance_requests"), before)


class NotificationTests(HostelHubTestCase):

    def test_open_mark_all_and_clear(self):
        student_id = self.demo_student_id()
        unread = self.one("SELECT * FROM notifications WHERE user_id = ? AND is_read = 0", (student_id,))
        self.login_student()
        response = self.client.get(f"/notifications/{unread['id']}/open")
        self.assertEqual(response.location, unread["link"])
        self.assertEqual(self.one("SELECT is_read FROM notifications WHERE id = ?", (unread["id"],))["is_read"], 1)

        self.post("/notifications/read-all")
        self.assertEqual(self.count("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
                                    (student_id,)), 0)
        others_before = self.count("SELECT COUNT(*) FROM notifications WHERE user_id != ?", (student_id,))
        self.post("/notifications/clear-read")
        self.assertEqual(self.count("SELECT COUNT(*) FROM notifications WHERE user_id = ?", (student_id,)), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM notifications WHERE user_id != ?", (student_id,)),
                         others_before)

    def test_external_links_are_not_followed(self):
        student_id = self.demo_student_id()
        self.run_sql("INSERT INTO notifications (user_id, title, message, link, created_at) "
                     "VALUES (?, 't', 'm', '//evil.example.com', '2026-01-01 00:00:00')", (student_id,))
        note = self.one("SELECT id FROM notifications ORDER BY id DESC LIMIT 1")
        self.login_student()
        self.assertEqual(self.client.get(f"/notifications/{note['id']}/open").location, "/notifications")


class DashboardTests(HostelHubTestCase):

    def test_percent_never_divides_by_zero(self):
        self.assertEqual((percent(0, 0), percent(5, 5), percent(1, 3)), (0, 100, 33))

    def test_warden_numbers_match_the_database(self):
        self.login_warden()
        html = self.client.get("/warden/dashboard").get_data(as_text=True)
        occupied = self.count("SELECT COUNT(*) FROM beds WHERE status = 'occupied'")
        usable = self.count("SELECT COUNT(*) FROM beds WHERE status != 'unavailable'")
        for value in (self.count("SELECT COUNT(*) FROM users WHERE role = 'student' AND is_active = 1"),
                      self.count("SELECT COUNT(*) FROM beds"), occupied,
                      self.count("SELECT COUNT(*) FROM beds WHERE status = 'available'"),
                      self.count("SELECT COUNT(*) FROM room_change_requests WHERE status = 'Pending'")):
            self.assertIn(f'<div class="stat-value">{value}</div>', html)
        self.assertIn(f"{round(occupied * 100 / usable)}%", html)

    def test_pages_work_with_an_empty_hostel(self):
        for table in ("notifications", "college_maintenance_requests", "room_change_requests", "complaints",
                      "allocations", "beds", "rooms"):
            self.run_sql(f"DELETE FROM {table}")
        self.run_sql("DELETE FROM users WHERE role = 'student'")
        self.login_warden()
        for url in ("/warden/dashboard", "/warden/rooms/map", "/warden/rooms/", "/warden/students",
                    "/warden/complaints", "/warden/room-requests", "/warden/college-requests/", "/notifications"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertIn(b"0%", self.client.get("/warden/dashboard").data)
        self.assertIn(b"No rooms yet", self.client.get("/warden/rooms/map").data)

    def test_student_dashboard_without_a_bed(self):
        self.login_as(self.unallocated_student_ids()[0])
        for url in ("/student/dashboard", "/student/room", "/student/complaints/new", "/student/room-requests"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertIn(b"not been allocated a room yet", self.client.get("/student/dashboard").data)


class SeedDataTests(HostelHubTestCase):

    def test_demo_data_matches_the_documentation(self):
        self.assertEqual(self.count("SELECT COUNT(DISTINCT block) FROM rooms"), 3)
        self.assertEqual(self.count("SELECT COUNT(*) FROM rooms"), 32)
        self.assertEqual(self.count("SELECT COUNT(*) FROM beds"), 100)
        self.assertEqual(self.count("SELECT COUNT(*) FROM users WHERE role = 'student'"), 72)
        self.assertEqual(len(self.unallocated_student_ids()), 5)
        self.assertEqual(self.count("SELECT COUNT(*) FROM room_change_requests WHERE status = 'Pending'"), 2)
        statuses = {row[0] for row in self.all("SELECT DISTINCT status FROM complaints")}
        self.assertEqual(statuses, {"Submitted", "Acknowledged", "In Progress", "Resolved", "Rejected"})
        bed_statuses = {row[0] for row in self.all("SELECT DISTINCT status FROM beds")}
        self.assertEqual(bed_statuses, {"available", "occupied", "reserved", "maintenance", "unavailable"})
        self.assertTrue(self.count("SELECT COUNT(*) FROM users WHERE email NOT LIKE '%@mes.ac.in'") == 0)


if __name__ == "__main__":
    unittest.main()
