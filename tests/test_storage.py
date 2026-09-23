"""Complaint photos: optional, kept in the local uploads folder, or switched off.

Firebase Cloud Storage is not used (it needs the paid Blaze plan). These tests
prove that a complaint never depends on a photo: with photos switched off
(COMPLAINT_IMAGE_STORAGE=none, the default on Vercel) the complaint workflow is
unchanged, and with local storage the photo is a private file referenced from
Firestore by its generated name only.
"""

import io
import unittest
from unittest import mock

from base import HostelHubTestCase, TINY_PNG, app


class LocalPhotoTests(HostelHubTestCase):

    def setUp(self):
        super().setUp()
        self.login_student()

    def upload(self):
        return self.upload_complaint(image=(io.BytesIO(TINY_PNG), "fan.png", "image/png"))

    def latest_complaint(self):
        return self.one("SELECT * FROM complaints ORDER BY id DESC")

    def test_photo_is_a_local_file_and_firestore_keeps_only_its_name(self):
        self.upload()
        complaint = self.latest_complaint()
        self.assertRegex(complaint["image_path"], r"^[0-9a-f]{32}\.png$")
        self.assertEqual(self.stored_photos(), [complaint["image_path"]])
        self.assertFalse(any(isinstance(value, bytes) for value in complaint.values()))

    def test_only_owner_and_warden_can_view_the_photo(self):
        self.upload()
        complaint = self.latest_complaint()
        url = f"/complaints/{complaint['id']}/image"

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, TINY_PNG)
        self.assertIn("private", response.headers["Cache-Control"])
        # The page never shows where the file is kept.
        page = self.client.get(f"/student/complaints/{complaint['id']}").get_data(as_text=True)
        self.assertNotIn(self.upload_folder, page)
        self.assertNotIn("uploads/", page)

        self.logout()
        self.login_as(self.other_allocated_student_id())
        self.assertEqual(self.client.get(url).status_code, 404)
        self.logout()
        self.login_warden()
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_photo_is_deleted_if_the_complaint_cannot_be_saved(self):
        from data.store import StoreError

        with mock.patch("data.firestore_store.FirestoreStore.commit", side_effect=StoreError("database down")):
            response = self.upload()
        self.assertIn(b"could not be saved", response.data)
        self.assertEqual(self.stored_photos(), [])

    def test_unwritable_folder_fails_safely(self):
        before = self.count("SELECT COUNT(*) FROM complaints")
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            response = self.upload()
        self.assertIn(b"could not be saved", response.data)
        self.assertEqual(self.count("SELECT COUNT(*) FROM complaints"), before)

    def test_unsafe_stored_name_is_refused(self):
        self.upload()
        complaint = self.latest_complaint()
        self.run_sql("UPDATE complaints SET image_path = '../secret.png' WHERE id = ?", (complaint["id"],))
        self.assertEqual(self.client.get(f"/complaints/{complaint['id']}/image").status_code, 404)


class PhotosSwitchedOffTests(HostelHubTestCase):
    """COMPLAINT_IMAGE_STORAGE=none: the deployed default until a cloud image service is added."""

    def setUp(self):
        super().setUp()
        app.config["COMPLAINT_IMAGE_STORAGE"] = "none"
        self.login_student()

    def test_form_has_no_photo_field(self):
        page = self.client.get("/student/complaints/new").get_data(as_text=True)
        self.assertNotIn('name="image"', page)
        self.assertIn("Photo uploads are not available", page)

    def test_complaint_without_photo_works_end_to_end(self):
        """Student submits → warden sees and resolves → student sees the new status."""
        self.upload_complaint(description="The bathroom tap has been leaking all week.")
        complaint = self.one("SELECT * FROM complaints ORDER BY id DESC")
        self.assertEqual((complaint["status"], complaint["image_path"]), ("Submitted", None))
        self.logout()

        self.login_warden()
        self.assertIn(b"bathroom tap", self.client.get("/warden/complaints").data)
        self.post(f"/warden/complaints/{complaint['id']}/update",
                  {"status": "Resolved", "remarks": "Washer replaced."})
        self.logout()

        self.login_student()
        page = self.client.get(f"/student/complaints/{complaint['id']}").get_data(as_text=True)
        self.assertIn("Resolved", page)
        self.assertIn("Washer replaced.", page)

    def test_a_photo_sent_anyway_is_skipped_and_the_complaint_is_saved(self):
        response = self.upload_complaint(image=(io.BytesIO(TINY_PNG), "fan.png", "image/png"))
        self.assertEqual(response.status_code, 302)
        complaint = self.one("SELECT * FROM complaints ORDER BY id DESC")
        self.assertIsNone(complaint["image_path"])
        self.assertEqual(self.stored_photos(), [])
        self.assertIn(b"photo was not attached", self.client.get(response.location).data)

    def test_photo_uploaded_elsewhere_is_not_shown(self):
        """A photo kept on a laptop is not on the deployed server: the page says so, nothing breaks."""
        complaint_id = self.data["complaint_ids"][0]
        self.run_sql("UPDATE complaints SET image_path = ? WHERE id = ?",
                     ("0123456789abcdef0123456789abcdef.png", complaint_id))
        page = self.client.get(f"/student/complaints/{complaint_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"no longer available", page.data)
        self.assertEqual(self.client.get(f"/complaints/{complaint_id}/image").status_code, 404)


if __name__ == "__main__":
    unittest.main()
