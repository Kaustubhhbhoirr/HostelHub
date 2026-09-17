"""Authentication, authorization, CSRF, privacy, route inventory and error pages."""

import re
import unittest

from base import HostelHubTestCase, app


def build_url(rule):
    """Turn a rule like /warden/students/<int:student_id> into /warden/students/2."""
    return re.sub(r"<(?:int:)?[a-z_]+>", "2", rule.rule)


class AuthenticationTests(HostelHubTestCase):

    def test_invalid_and_empty_login_are_rejected(self):
        response = self.login("student@mes.ac.in", "wrong-password")
        self.assertIn(b"Invalid email or password", response.data)
        response = self.login("nobody@mes.ac.in", "Student@123")
        self.assertIn(b"Invalid email or password", response.data)
        response = self.login("", "")
        self.assertIn(b"Please enter both email and password", response.data)
        self.assertEqual(self.client.get("/student/dashboard").status_code, 302)

    def test_valid_logins_go_to_the_right_dashboard(self):
        self.assertEqual(self.login_student().location, "/student/dashboard")
        self.assertEqual(self.client.get("/student/dashboard").status_code, 200)
        self.logout()
        self.assertEqual(self.login_warden().location, "/warden/dashboard")
        self.assertEqual(self.client.get("/warden/dashboard").status_code, 200)

    def test_email_is_case_insensitive(self):
        self.assertEqual(self.login("  STUDENT@MES.AC.IN ", "Student@123").location, "/student/dashboard")

    def test_logout_clears_the_session(self):
        self.login_student()
        self.logout()
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)
        self.assertEqual(self.client.get("/student/dashboard").status_code, 302)

    def test_passwords_are_stored_hashed(self):
        for row in self.all("SELECT password_hash FROM users"):
            self.assertNotIn(row["password_hash"], ("Student@123", "Warden@123"))
            self.assertTrue(row["password_hash"].startswith(("scrypt:", "pbkdf2:")))

    def test_session_stores_only_the_user_id(self):
        self.login_student()
        with self.client.session_transaction() as session:
            self.assertIn("user_id", session)
            # Nothing else about the user (no role, no name) is kept in the cookie.
            self.assertTrue(set(session.keys()) <= {"user_id", "csrf_token", "_flashes"})

    def test_role_in_session_is_ignored(self):
        """Even if someone adds role=warden to the session, the database role is used."""
        self.login_student()
        with self.client.session_transaction() as session:
            session["role"] = "warden"
        self.assertEqual(self.client.get("/warden/dashboard").status_code, 403)

    def test_deactivated_student_cannot_log_in_and_is_logged_out(self):
        student_id = self.unallocated_student_ids()[0]   # no bed, so raw SQL keeps data consistent
        self.login_as(student_id)
        self.assertEqual(self.client.get("/student/dashboard").status_code, 200)
        self.run_sql("UPDATE users SET is_active = 0 WHERE id = ?", (student_id,))
        self.assertEqual(self.client.get("/student/dashboard").status_code, 302)   # kicked out
        response = self.login_as(student_id)
        self.assertIn(b"deactivated", response.data)


class CsrfTests(HostelHubTestCase):

    def test_post_without_token_is_rejected(self):
        response = self.client.post("/login", data={"email": "student@mes.ac.in", "password": "Student@123"})
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Form expired", response.data)

    def test_post_with_wrong_token_changes_nothing(self):
        self.login_warden()
        self.csrf_token()
        pending = self.one("SELECT id FROM room_change_requests WHERE status = 'Pending'")
        response = self.client.post(f"/warden/room-requests/{pending['id']}/decide",
                                    data={"action": "reject", "remarks": "forged", "csrf_token": "wrong"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.one("SELECT status FROM room_change_requests WHERE id = ?", (pending["id"],))["status"],
                         "Pending")

    def test_every_post_form_contains_the_token(self):
        self.login_warden()
        for url in ("/warden/rooms/map", "/warden/students/2", "/warden/complaints/1",
                    "/warden/room-requests/2", "/warden/college-requests/3", "/notifications", "/profile"):
            html = self.client.get(url).get_data(as_text=True)
            forms = re.findall(r"<form\b[^>]*method=\"post\"[^>]*>(.*?)</form>", html, flags=re.S | re.I)
            self.assertTrue(forms, url)
            for form in forms:
                self.assertIn('name="csrf_token"', form, url)


class RouteInventoryTests(HostelHubTestCase):
    """Request EVERY route in the app as a visitor, a student and a warden."""

    def rules(self, method):
        return [rule for rule in app.url_map.iter_rules()
                if method in rule.methods and rule.endpoint != "static"]

    def test_logged_out_visitor_is_sent_to_login(self):
        public = {"/login", "/"}
        for rule in self.rules("GET"):
            if rule.rule in public:
                continue
            response = self.client.get(build_url(rule))
            self.assertEqual(response.status_code, 302, rule.rule)
            self.assertEqual(response.location, "/login", rule.rule)
        for rule in self.rules("POST"):
            if rule.rule in ("/login", "/logout"):
                continue
            response = self.post(build_url(rule))
            self.assertEqual(response.status_code, 302, rule.rule)

    def test_student_cannot_open_any_warden_route(self):
        self.login_student()
        for method in ("GET", "POST"):
            for rule in self.rules(method):
                if rule.rule.startswith("/warden"):
                    url = build_url(rule)
                    response = self.client.get(url) if method == "GET" else self.post(url)
                    self.assertEqual(response.status_code, 403, f"{method} {rule.rule}")

    def test_warden_cannot_open_student_routes(self):
        self.login_warden()
        for rule in self.rules("GET"):
            if rule.rule.startswith("/student"):
                self.assertEqual(self.client.get(build_url(rule)).status_code, 403, rule.rule)

    def test_every_get_page_works_for_its_role(self):
        self.login_student()
        for rule in self.rules("GET"):
            if rule.rule.startswith("/student") and "<" not in rule.rule:
                self.assertEqual(self.client.get(rule.rule).status_code, 200, rule.rule)
        self.logout()
        self.login_warden()
        for rule in self.rules("GET"):
            if rule.rule.startswith("/warden") and "<" not in rule.rule:
                self.assertEqual(self.client.get(rule.rule).status_code, 200, rule.rule)

    def test_missing_records_return_404(self):
        self.login_warden()
        for url in ("/warden/students/99999", "/warden/students/99999/edit", "/warden/rooms/99999/edit",
                    "/warden/complaints/99999", "/warden/room-requests/99999",
                    "/warden/college-requests/99999", "/complaints/99999/image", "/notifications/99999/open",
                    "/warden/students/1"):   # user 1 is the warden, not a student
            self.assertEqual(self.client.get(url).status_code, 404, url)
        for url in ("/warden/rooms/beds/99999/allocate", "/warden/rooms/beds/99999/vacate",
                    "/warden/rooms/beds/99999/status", "/warden/complaints/99999/update",
                    "/warden/room-requests/99999/decide", "/warden/students/99999/delete"):
            self.assertEqual(self.post(url).status_code, 404, url)

    def test_custom_error_pages(self):
        response = self.client.get("/this-page-does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertIn(b"Page not found", response.data)
        self.login_student()
        response = self.client.get("/warden/dashboard")
        self.assertIn(b"Access denied", response.data)
        self.assertNotIn(b"Traceback", response.data)


class PrivacyTests(HostelHubTestCase):

    def test_student_cannot_view_another_students_complaint(self):
        other = self.one("SELECT id FROM complaints WHERE student_id != ? LIMIT 1", (self.demo_student_id(),))
        self.login_student()
        self.assertEqual(self.client.get(f"/student/complaints/{other['id']}").status_code, 404)

    def test_student_lists_show_only_own_records(self):
        self.login_student()
        html = self.client.get("/student/complaints").get_data(as_text=True)
        own = self.count("SELECT COUNT(*) FROM complaints WHERE student_id = ?", (self.demo_student_id(),))
        self.assertEqual(html.count("data-href=\"/student/complaints/"), own)
        other_request = self.one("SELECT id FROM room_change_requests WHERE student_id != ?", (self.demo_student_id(),))
        html = self.client.get("/student/room-requests").get_data(as_text=True)
        self.assertNotIn(f"RCR-{other_request['id']:04d}", html)

    def test_student_cannot_cancel_another_students_request(self):
        other = self.one("SELECT * FROM room_change_requests WHERE status = 'Pending'")
        self.login_student()
        self.assertEqual(self.post(f"/student/room-requests/{other['id']}/cancel").status_code, 404)
        self.assertEqual(self.one("SELECT status FROM room_change_requests WHERE id = ?", (other["id"],))["status"],
                         "Pending")

    def test_roommate_private_details_are_not_shown(self):
        roommate_phones = self.all(
            """SELECT users.phone FROM allocations JOIN users ON users.id = allocations.student_id
               JOIN beds ON beds.id = allocations.bed_id
               WHERE allocations.status = 'active' AND users.id != ?
                 AND beds.room_id = (SELECT beds.room_id FROM allocations JOIN beds ON beds.id = allocations.bed_id
                                     WHERE allocations.student_id = ? AND allocations.status = 'active')""",
            (self.demo_student_id(), self.demo_student_id()))
        self.assertTrue(roommate_phones)
        self.login_student()
        html = self.client.get("/student/room").get_data(as_text=True)
        for row in roommate_phones:
            self.assertNotIn(row["phone"], html)

    def test_notifications_are_private(self):
        others = self.one("SELECT id FROM notifications WHERE user_id != ? LIMIT 1", (self.demo_student_id(),))
        self.login_student()
        self.assertEqual(self.client.get(f"/notifications/{others['id']}/open").status_code, 404)


if __name__ == "__main__":
    unittest.main()
