"""Private photo storage in deployment mode (Supabase Storage), tested with a fake server.

We cannot call the real Supabase service in automated tests, so a tiny local
HTTP server imitates the three Storage API endpoints HostelHub uses and checks
that every request carries the secret key. This proves HostelHub sends the right
requests and keeps photos private; it does not test Supabase itself.
"""

import io
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from base import HostelHubTestCase, TINY_PNG, app

SECRET = "test-secret-key"
BUCKET = "complaint-photos"


class FakeSupabaseStorage(BaseHTTPRequestHandler):
    objects = {}          # "bucket/file" -> (content_type, bytes)
    requests_seen = []    # (method, path)

    def log_message(self, *args):   # keep test output quiet
        pass

    def authorised(self):
        return (self.headers.get("apikey") == SECRET
                and self.headers.get("Authorization") == f"Bearer {SECRET}")

    def reply(self, status, body=b"{}", content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.requests_seen.append(("POST", self.path))
        if not self.authorised():
            return self.reply(403, b'{"error":"Unauthorized"}')
        key = self.path.removeprefix("/storage/v1/object/")
        length = int(self.headers.get("Content-Length", 0))
        self.objects[key] = (self.headers.get("Content-Type"), self.rfile.read(length))
        self.reply(200, b'{"Key":"%s"}' % key.encode())

    def do_GET(self):
        self.requests_seen.append(("GET", self.path))
        prefix = "/storage/v1/object/authenticated/"
        # Private buckets can only be read through the authenticated endpoint with the key.
        if not self.path.startswith(prefix) or not self.authorised():
            return self.reply(400, b'{"error":"not found"}')
        stored = self.objects.get(self.path.removeprefix(prefix))
        if stored is None:
            return self.reply(400, b'{"statusCode":"404","error":"not_found"}')
        self.reply(200, stored[1], stored[0])

    def do_DELETE(self):
        self.requests_seen.append(("DELETE", self.path))
        if not self.authorised():
            return self.reply(403)
        self.objects.pop(self.path.removeprefix("/storage/v1/object/"), None)
        self.reply(200)


class SupabaseStorageTests(HostelHubTestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSupabaseStorage)
        cls.server_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        super().setUp()
        FakeSupabaseStorage.objects.clear()
        FakeSupabaseStorage.requests_seen.clear()
        app.config.update(SUPABASE_URL=self.server_url, SUPABASE_SECRET_KEY=SECRET, SUPABASE_BUCKET=BUCKET)

    def upload(self):
        return self.upload_complaint(image=(io.BytesIO(TINY_PNG), "fan.png", "image/png"))

    def latest_complaint(self):
        return self.one("SELECT * FROM complaints ORDER BY id DESC LIMIT 1")

    def test_photo_is_stored_in_bucket_not_on_local_disk(self):
        self.login_student()
        self.assertEqual(self.upload().status_code, 302)
        complaint = self.latest_complaint()
        stored = FakeSupabaseStorage.objects[f"{BUCKET}/{complaint['image_path']}"]
        self.assertEqual(stored, ("image/png", TINY_PNG))
        self.assertFalse(os.path.isdir(app.config["UPLOAD_FOLDER"]))    # nothing written locally

    def test_only_owner_and_warden_can_view_the_private_photo(self):
        self.login_student()
        self.upload()
        complaint = self.latest_complaint()
        url = f"/complaints/{complaint['id']}/image"

        response = self.client.get(url)
        self.assertEqual((response.status_code, response.data, response.mimetype), (200, TINY_PNG, "image/png"))
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn(("GET", f"/storage/v1/object/authenticated/{BUCKET}/{complaint['image_path']}"),
                      FakeSupabaseStorage.requests_seen)
        self.logout()

        requests_before = len(FakeSupabaseStorage.requests_seen)
        self.assertEqual(self.client.get(url).status_code, 302)          # logged out
        self.login_as(self.other_allocated_student_id())
        self.assertEqual(self.client.get(url).status_code, 404)          # another student
        # Unauthorised users are refused BEFORE HostelHub asks the storage service.
        self.assertEqual(len(FakeSupabaseStorage.requests_seen), requests_before)
        self.logout()
        self.login_warden()
        self.assertEqual(self.client.get(url).data, TINY_PNG)            # warden

        # The page links to HostelHub's route, never to the storage service.
        page = self.client.get(f"/warden/complaints/{complaint['id']}").get_data(as_text=True)
        self.assertIn(url, page)
        self.assertNotIn(self.server_url, page)
        self.assertNotIn(SECRET, page)

    def test_missing_object_returns_404(self):
        self.login_student()
        self.upload()
        complaint = self.latest_complaint()
        FakeSupabaseStorage.objects.clear()
        self.assertEqual(self.client.get(f"/complaints/{complaint['id']}/image").status_code, 404)

    def test_wrong_key_or_unreachable_storage_fails_safely(self):
        self.login_student()
        before = self.count("SELECT COUNT(*) AS n FROM complaints")

        app.config["SUPABASE_SECRET_KEY"] = "wrong-key"
        self.assertIn(b"could not be saved", self.upload().data)

        app.config["SUPABASE_URL"] = "http://127.0.0.1:9"                # nothing listens here
        self.assertIn(b"could not be saved", self.upload().data)

        self.assertEqual(self.count("SELECT COUNT(*) AS n FROM complaints"), before)
        self.assertEqual(FakeSupabaseStorage.objects, {})

    def test_photo_is_deleted_if_the_complaint_cannot_be_saved(self):
        import sqlite3
        self.login_student()
        with mock.patch("routes.complaints.notify_wardens", side_effect=sqlite3.OperationalError("db down")):
            self.assertIn(b"could not be saved", self.upload().data)
        self.assertEqual(FakeSupabaseStorage.objects, {})
        self.assertTrue(any(method == "DELETE" for method, _ in FakeSupabaseStorage.requests_seen))

    def test_unsafe_stored_name_never_reaches_storage(self):
        complaint = self.one("SELECT id FROM complaints WHERE student_id = ?", (self.demo_student_id(),))
        self.run_sql("UPDATE complaints SET image_path = '../secret.png' WHERE id = ?", (complaint["id"],))
        self.login_warden()
        self.assertEqual(self.client.get(f"/complaints/{complaint['id']}/image").status_code, 404)
        self.assertEqual(FakeSupabaseStorage.requests_seen, [])


if __name__ == "__main__":
    unittest.main()
