"""Maintenance complaints: creation, image upload security, status workflow, notifications."""

import io
import os
import unittest

from base import HostelHubTestCase, TINY_PNG, app
from fixtures import COMPLAINTS


class ComplaintUploadTests(HostelHubTestCase):

    def latest_complaint(self):
        return self.one("SELECT * FROM complaints ORDER BY id DESC LIMIT 1")

    def test_complaint_with_valid_image(self):
        self.login_student()
        response = self.upload_complaint(image=(io.BytesIO(TINY_PNG), "light photo.png", "image/png"))
        self.assertEqual(response.status_code, 302)

        complaint = self.latest_complaint()
        self.assertEqual(complaint["status"], "Submitted")
        self.assertEqual(complaint["student_id"], self.demo_student_id())
        # Only a generated file name is stored — not the user's name and not the bytes.
        self.assertRegex(complaint["image_path"], r"^[0-9a-f]{32}\.png$")
        self.assertEqual(self.stored_photos(), [complaint["image_path"]])

        # The owner can open the image through the protected route.
        response = self.client.get(f"/complaints/{complaint['id']}/image")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, TINY_PNG)
        response.close()

    def test_complaint_without_image_works(self):
        self.login_student()
        self.upload_complaint()
        complaint = self.latest_complaint()
        self.assertIsNone(complaint["image_path"])
        self.assertEqual(self.client.get(f"/student/complaints/{complaint['id']}").status_code, 200)
        self.assertEqual(self.client.get(f"/complaints/{complaint['id']}/image").status_code, 404)

    def test_image_access_is_protected(self):
        self.login_student()
        self.upload_complaint(image=(io.BytesIO(TINY_PNG), "a.png", "image/png"))
        complaint = self.latest_complaint()
        image_url = f"/complaints/{complaint['id']}/image"
        self.logout()

        # Logged-out visitor
        self.assertEqual(self.client.get(image_url).status_code, 302)
        # The old public location does not exist any more
        self.assertEqual(self.client.get(f"/static/uploads/complaints/{complaint['image_path']}").status_code, 404)
        # Another student
        self.login_as(self.other_allocated_student_id())
        self.assertEqual(self.client.get(image_url).status_code, 404)
        self.logout()
        # Warden
        self.login_warden()
        response = self.client.get(image_url)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertIn(image_url.encode(), self.client.get(f"/warden/complaints/{complaint['id']}").data)

    def test_invalid_files_are_rejected(self):
        self.login_student()
        before = self.count("SELECT COUNT(*) FROM complaints")
        bad_files = [
            (io.BytesIO(b"MZ fake program"), "virus.exe", "application/octet-stream"),
            (io.BytesIO(b"<script>alert(1)</script>"), "page.png", "image/png"),   # renamed, wrong content
            (io.BytesIO(TINY_PNG), "image.svg", "image/svg+xml"),                  # extension not allowed
        ]
        for bad in bad_files:
            response = self.upload_complaint(image=bad)
            self.assertIn(b"Only PNG, JPG", response.data, bad[1])
        self.assertEqual(self.count("SELECT COUNT(*) FROM complaints"), before)
        self.assertEqual(self.stored_photos(), [])

    def test_missing_image_file_does_not_break_the_page(self):
        self.login_student()
        self.upload_complaint(image=(io.BytesIO(TINY_PNG), "a.png", "image/png"))
        complaint = self.latest_complaint()
        os.remove(os.path.join(self.upload_folder, complaint["image_path"]))
        response = self.client.get(f"/student/complaints/{complaint['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"no longer available", response.data)
        self.assertEqual(self.client.get(f"/complaints/{complaint['id']}/image").status_code, 404)

    def test_path_traversal_in_stored_name_is_blocked(self):
        complaint = self.one("SELECT id FROM complaints WHERE student_id = ?", (self.demo_student_id(),))
        self.run_sql("UPDATE complaints SET image_path = '../../app.py' WHERE id = ?", (complaint["id"],))
        self.login_warden()
        self.assertEqual(self.client.get(f"/complaints/{complaint['id']}/image").status_code, 404)

    def test_form_validation(self):
        self.login_student()
        before = self.count("SELECT COUNT(*) FROM complaints")
        self.assertIn(b"at least 15 characters", self.upload_complaint(description="short").data)
        response = self.post("/student/complaints/new", {"category": "Wi-Fi", "priority": "Low",
                                                          "description": "A valid long enough description."})
        self.assertIn(b"Please choose a category", response.data)
        response = self.post("/student/complaints/new", {"category": "Fan", "priority": "Urgent!!",
                                                          "description": "A valid long enough description."})
        self.assertIn(b"valid priority", response.data)
        self.assertEqual(self.count("SELECT COUNT(*) FROM complaints"), before)

    def test_student_without_bed_cannot_complain(self):
        student_id = self.unallocated_student_ids()[0]
        self.login_as(student_id)
        response = self.upload_complaint()
        self.assertIn(b"allocated room", response.data)
        self.assertEqual(self.count("SELECT COUNT(*) FROM complaints WHERE student_id = ?", (student_id,)), 0)

    def test_new_complaint_notifies_student_and_wardens_only(self):
        before = self.count("SELECT COUNT(*) FROM notifications")
        self.login_student()
        self.upload_complaint()
        rows = sorted(self.documents("notifications"), key=lambda row: row["id"])[before:]
        new_rows = [{**row, "role": self.document("users", row["user_id"])["role"]} for row in rows]
        recipients = sorted((row["role"], row["user_id"]) for row in new_rows)
        wardens = self.count("SELECT COUNT(*) FROM users WHERE role = 'warden'")
        self.assertEqual(len(new_rows), 1 + wardens)
        self.assertIn(("student", self.demo_student_id()), recipients)
        self.assertEqual(sum(1 for role, _ in recipients if role == "student"), 1)


class ComplaintWorkflowTests(HostelHubTestCase):

    def submitted(self):
        return self.one("SELECT * FROM complaints WHERE status = 'Submitted' ORDER BY id LIMIT 1")

    def test_full_status_flow_with_remarks_and_notifications(self):
        complaint = self.submitted()
        self.login_warden()
        for status in ("Acknowledged", "In Progress", "Resolved"):
            self.post(f"/warden/complaints/{complaint['id']}/update", {"status": status, "remarks": f"Now {status}"})
            row = self.one("SELECT * FROM complaints WHERE id = ?", (complaint["id"],))
            self.assertEqual(row["status"], status)
            note = self.one("SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                            (complaint["student_id"],))
            self.assertIn(status, note["message"])
        self.assertIsNotNone(row["resolved_at"])
        self.assertEqual(row["warden_remarks"], "Now Resolved")

    def test_remark_only_update_notifies_student(self):
        complaint = self.submitted()
        self.login_warden()
        self.post(f"/warden/complaints/{complaint['id']}/update", {"status": "Submitted", "remarks": "Will check today."})
        note = self.one("SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1", (complaint["student_id"],))
        self.assertEqual(note["title"], "Warden added remarks")
        self.assertEqual(self.one("SELECT status FROM complaints WHERE id = ?", (complaint["id"],))["status"], "Submitted")

    def test_invalid_transitions_are_refused(self):
        resolved = self.one("SELECT * FROM complaints WHERE status = 'Resolved' LIMIT 1")
        self.login_warden()
        response = self.post(f"/warden/complaints/{resolved['id']}/update",
                             {"status": "In Progress", "remarks": "x"}, follow_redirects=True)
        self.assertIn(b"cannot be changed", response.data)
        complaint = self.submitted()
        self.post(f"/warden/complaints/{complaint['id']}/update", {"status": "Hacked", "remarks": "x"})
        self.post(f"/warden/complaints/{complaint['id']}/update", {"status": "Rejected", "remarks": ""})
        self.assertEqual(self.one("SELECT status FROM complaints WHERE id = ?", (complaint["id"],))["status"], "Submitted")
        self.assertEqual(self.one("SELECT status FROM complaints WHERE id = ?", (resolved["id"],))["status"], "Resolved")

    def test_student_cannot_update_complaint_status(self):
        own = self.one("SELECT id FROM complaints WHERE student_id = ? AND status = 'Submitted'", (self.demo_student_id(),))
        self.login_student()
        self.assertEqual(self.post(f"/warden/complaints/{own['id']}/update", {"status": "Resolved"}).status_code, 403)
        self.assertEqual(self.one("SELECT status FROM complaints WHERE id = ?", (own["id"],))["status"], "Submitted")

    def test_warden_filters(self):
        self.login_warden()
        high_open = self.count("SELECT COUNT(*) FROM complaints WHERE priority = 'High' "
                               "AND status IN ('Submitted', 'Acknowledged', 'In Progress')")
        html = self.client.get("/warden/complaints?status=open&priority=High").get_data(as_text=True)
        self.assertEqual(html.count('data-href="/warden/complaints/'), high_open)
        for query in ("?q=%25", "?q='; DROP TABLE complaints; --", "?category=Nope&priority=&block=Z",
                      "?q=zzzz-no-match", "?status=<script>"):
            response = self.client.get("/warden/complaints" + query)
            self.assertEqual(response.status_code, 200, query)
        self.assertIn(b"No complaints match", self.client.get("/warden/complaints?q=zzzz-no-match").data)
        self.assertEqual(self.count("SELECT COUNT(*) FROM complaints"), len(COMPLAINTS))


if __name__ == "__main__":
    unittest.main()
