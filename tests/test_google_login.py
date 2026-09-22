"""Google sign-in (Firebase) — only for authentication; all hostel data stays in HostelHub's database.

Real Google accounts cannot be used in automated tests, so firebase_admin's
verify_id_token() is replaced by a fake that returns the claims we choose.
That lets us test every rule in authenticate_google() and the /firebase-login route.
"""

import unittest
from unittest import mock

import firebase_admin
from firebase_admin import auth as firebase_auth

from base import HostelHubTestCase, app

GOOGLE_STUDENT = "kaustubhb25comp@student.mes.ac.in"
PUBLIC_CLIENT_CONFIG = ('{"apiKey": "public-test-key", "authDomain": "demo.firebaseapp.com", '
                        '"projectId": "demo", "appId": "1:2:web:3"}')


def google_claims(email=GOOGLE_STUDENT, uid="google-uid-1", verified=True, **extra):
    return {"email": email, "uid": uid, "email_verified": verified, **extra}


class GoogleLoginTests(HostelHubTestCase):

    def setUp(self):
        super().setUp()
        # Pretend the Firebase Admin SDK is initialised (no real credentials needed).
        self.firebase_ready = mock.patch.dict(firebase_admin._apps, {"[DEFAULT]": object()}, clear=True)
        self.firebase_ready.start()
        app.config["FIREBASE_CLIENT_CONFIG"] = PUBLIC_CLIENT_CONFIG

    def tearDown(self):
        self.firebase_ready.stop()
        app.config["FIREBASE_CLIENT_CONFIG"] = ""
        super().tearDown()

    def google_login(self, claims=None, token="fake-id-token", error=None):
        target = "firebase_admin.auth.verify_id_token"
        side_effect = error if error else None
        with mock.patch(target, return_value=claims or google_claims(), side_effect=side_effect) as verify:
            response = self.post("/firebase-login", {"idToken": token})
        return response, verify

    def logged_in_user_id(self):
        with self.client.session_transaction() as session:
            return session.get("user_id")

    # ---------------- successful sign-in ----------------
    def test_registered_student_signs_in_and_lands_on_dashboard(self):
        response, verify = self.google_login()
        verify.assert_called_once_with("fake-id-token")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True, "redirect": "/student/dashboard"})
        user = self.one("SELECT * FROM users WHERE email = ?", (GOOGLE_STUDENT,))
        self.assertEqual(self.logged_in_user_id(), user["id"])
        self.assertEqual(user["firebase_uid"], "google-uid-1")       # Google account linked on first sign-in
        page = self.client.get("/student/dashboard", follow_redirects=True).get_data(as_text=True)
        self.assertIn("Kaustubh", page)
        self.assertIn("A-102", page)                                  # seeded bed

    def test_role_comes_from_database_not_from_google(self):
        response, _ = self.google_login(google_claims(role="warden", admin=True))
        self.assertEqual(response.get_json()["redirect"], "/student/dashboard")
        self.assertEqual(self.client.get("/warden/dashboard").status_code, 403)

    def test_warden_can_also_use_google(self):
        response, _ = self.google_login(google_claims(email="warden@mes.ac.in", uid="warden-uid"))
        self.assertEqual(response.get_json()["redirect"], "/warden/dashboard")

    def test_email_capital_letters_are_ignored(self):
        response, _ = self.google_login(google_claims(email=GOOGLE_STUDENT.upper()))
        self.assertEqual(response.status_code, 200)

    def test_second_sign_in_with_same_google_account_works(self):
        self.google_login()
        self.logout()
        response, _ = self.google_login()
        self.assertEqual(response.status_code, 200)

    # ---------------- refused sign-ins ----------------
    def assert_refused(self, response, status, message):
        self.assertEqual(response.status_code, status, response.get_json())
        self.assertIn(message, response.get_json()["error"])
        self.assertIsNone(self.logged_in_user_id())

    def test_unregistered_college_email_is_refused(self):
        response, _ = self.google_login(google_claims(email="newperson@student.mes.ac.in"))
        self.assert_refused(response, 404, "not registered")

    def test_non_college_google_account_is_refused(self):
        response, _ = self.google_login(google_claims(email="someone@gmail.com"))
        self.assert_refused(response, 403, "MES college Google account")
        response, _ = self.google_login(google_claims(email="x@student.mes.ac.in.evil.com"))
        self.assert_refused(response, 403, "MES college Google account")

    def test_unverified_email_is_refused(self):
        response, _ = self.google_login(google_claims(verified=False))
        self.assert_refused(response, 403, "verified email")

    def test_invalid_or_expired_token_is_refused(self):
        response, _ = self.google_login(error=ValueError("malformed token"))
        self.assert_refused(response, 401, "could not be verified")
        expired = firebase_auth.ExpiredIdTokenError("Token expired", cause=None)
        response, _ = self.google_login(error=expired)
        self.assert_refused(response, 401, "could not be verified")

    def test_missing_token_is_refused(self):
        response = self.post("/firebase-login", {})
        self.assert_refused(response, 400, "sign-in token")

    def test_request_without_csrf_token_is_refused(self):
        with mock.patch("firebase_admin.auth.verify_id_token", return_value=google_claims()) as verify:
            response = self.client.post("/firebase-login", data={"idToken": "fake-id-token"})
        self.assertEqual(response.status_code, 400)
        verify.assert_not_called()
        self.assertIsNone(self.logged_in_user_id())

    def test_deactivated_account_is_refused(self):
        self.run_sql("UPDATE users SET is_active = 0 WHERE email = ?", (GOOGLE_STUDENT,))
        # Deactivating normally frees the bed; do the same so the data stays consistent.
        self.run_sql("""UPDATE beds SET status = 'available' WHERE id =
                        (SELECT bed_id FROM allocations a JOIN users u ON u.id = a.student_id
                         WHERE u.email = ? AND a.status = 'active')""", (GOOGLE_STUDENT,))
        self.run_sql("""UPDATE allocations SET status = 'ended', ended_at = '2026-09-19 00:00:00'
                        WHERE student_id = (SELECT id FROM users WHERE email = ?)""", (GOOGLE_STUDENT,))
        response, _ = self.google_login()
        self.assert_refused(response, 403, "deactivated")

    def test_account_linked_to_another_google_account_is_refused(self):
        self.google_login()                       # links google-uid-1
        self.logout()
        response, _ = self.google_login(google_claims(uid="someone-else"))
        self.assert_refused(response, 403, "different Google account")
        self.assertEqual(self.one("SELECT firebase_uid FROM users WHERE email = ?", (GOOGLE_STUDENT,))["firebase_uid"],
                         "google-uid-1")

    def test_server_without_firebase_refuses_politely(self):
        firebase_admin._apps.clear()
        response, verify = self.google_login()
        self.assert_refused(response, 503, "not configured")
        verify.assert_not_called()

    def test_google_only_account_has_no_usable_password(self):
        for password in ("Student@123", "", "password"):
            self.login(GOOGLE_STUDENT, password)
            self.assertIsNone(self.logged_in_user_id())

    # ---------------- login page ----------------
    def test_login_page_shows_google_button_only_when_configured(self):
        page = self.client.get("/login").get_data(as_text=True)
        self.assertIn('id="googleSignIn"', page)
        self.assertIn("public-test-key", page)            # the PUBLIC web config is on the page
        self.assertNotIn("private_key", page)             # the secret service account never is
        app.config["FIREBASE_CLIENT_CONFIG"] = ""
        self.assertNotIn('id="googleSignIn"', self.client.get("/login").get_data(as_text=True))
        app.config["FIREBASE_CLIENT_CONFIG"] = PUBLIC_CLIENT_CONFIG
        firebase_admin._apps.clear()                       # server cannot verify tokens -> no button
        self.assertNotIn('id="googleSignIn"', self.client.get("/login").get_data(as_text=True))

    def test_broken_client_config_hides_button_instead_of_crashing(self):
        app.config["FIREBASE_CLIENT_CONFIG"] = "{not json"
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="googleSignIn"', response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
