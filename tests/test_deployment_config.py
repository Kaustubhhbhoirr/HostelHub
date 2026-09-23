"""Deployment readiness: Firebase and photo settings, environment variables, Vercel layout and Git hygiene.

Some tests start a separate Python process with different environment variables,
because config.py reads the environment when the app is first imported.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from base import app
from config import DEV_SECRET_KEY, check_production_settings
from data import local_now

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOOD_SECRET = "a" * 64


def fake_service_account(project="hostelhub-83310"):
    """A well-formed service-account key made up on the spot (it opens nothing).

    The Firebase Admin SDK checks the key's format when the app starts, so the
    startup tests need one that parses. No network call is made at startup.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return json.dumps({"type": "service_account", "project_id": project, "private_key_id": "test",
                       "private_key": pem, "client_email": "test@example.iam.gserviceaccount.com",
                       "client_id": "1", "token_uri": "https://oauth2.googleapis.com/token"})


FAKE_SERVICE_ACCOUNT = fake_service_account()
GOOD_PRODUCTION_ENV = {
    "VERCEL": "1",
    "HOSTELHUB_SECRET_KEY": GOOD_SECRET,
    "FIREBASE_SERVICE_ACCOUNT": FAKE_SERVICE_ACCOUNT,
}
DEPLOYMENT_VARIABLES = ("VERCEL", "HOSTELHUB_ENV", "HOSTELHUB_DEBUG", "HOSTELHUB_SECRET_KEY",
                        "FIREBASE_SERVICE_ACCOUNT", "FIREBASE_CREDENTIALS_FILE", "FIREBASE_CLIENT_CONFIG",
                        "COMPLAINT_IMAGE_STORAGE", "FIREBASE_STORAGE_BUCKET")


def import_app_with(env):
    """Import app.py in a fresh Python process with the given environment variables.

    Returns (exit code, output). The child prints the settings we want to check.
    """
    child_env = {key: value for key, value in os.environ.items() if key not in DEPLOYMENT_VARIABLES}
    # Blank (not just remove) the deployment variables, so a developer's local .env
    # file cannot fill them in again through load_dotenv().
    child_env.update({name: "" for name in DEPLOYMENT_VARIABLES})
    child_env.update(env)
    code = ("from app import app; import firebase_admin; c = app.config; "
            "print('DEBUG', c['DEBUG'], 'SECURE', c['SESSION_COOKIE_SECURE'], 'PRODUCTION', c['IS_PRODUCTION'], "
            "'FIREBASE', bool(firebase_admin._apps), 'IMAGES', c['COMPLAINT_IMAGE_STORAGE'])")
    result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, env=child_env,
                            capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout + result.stderr


def production_settings(**changes):
    settings = {"IS_PRODUCTION": True, "SECRET_KEY": GOOD_SECRET, "DEBUG": False,
                "FIREBASE_SERVICE_ACCOUNT": FAKE_SERVICE_ACCOUNT, "FIREBASE_CREDENTIALS_FILE": "",
                "ON_VERCEL": True, "COMPLAINT_IMAGE_STORAGE": "none"}
    settings.update(changes)
    return settings


class ProductionSettingsTests(unittest.TestCase):

    def test_local_development_needs_only_firebase(self):
        local = production_settings(IS_PRODUCTION=False, ON_VERCEL=False, SECRET_KEY=DEV_SECRET_KEY, DEBUG=True,
                                    COMPLAINT_IMAGE_STORAGE="local")
        self.assertEqual(check_production_settings(local), [])
        # A key file instead of the JSON variable is fine too.
        with_file = production_settings(IS_PRODUCTION=False, FIREBASE_SERVICE_ACCOUNT="",
                                        FIREBASE_CREDENTIALS_FILE="key.json")
        self.assertEqual(check_production_settings(with_file), [])

    def test_firebase_is_required_locally_too(self):
        """There is no local database to fall back to."""
        local = production_settings(IS_PRODUCTION=False, FIREBASE_SERVICE_ACCOUNT="")
        problems = check_production_settings(local)
        self.assertEqual(len(problems), 1)
        self.assertIn("FIREBASE_SERVICE_ACCOUNT", problems[0])

    def test_complete_production_settings_pass(self):
        self.assertEqual(check_production_settings(production_settings()), [])

    def test_each_unsafe_production_setting_is_reported(self):
        cases = [
            ({"SECRET_KEY": DEV_SECRET_KEY}, "HOSTELHUB_SECRET_KEY"),
            ({"SECRET_KEY": "too-short"}, "HOSTELHUB_SECRET_KEY"),
            ({"DEBUG": True}, "Debug"),
            ({"FIREBASE_SERVICE_ACCOUNT": ""}, "FIREBASE_SERVICE_ACCOUNT"),
            ({"COMPLAINT_IMAGE_STORAGE": "local"}, "temporary"),      # Vercel's disk does not keep files
            ({"COMPLAINT_IMAGE_STORAGE": "firebase"}, "COMPLAINT_IMAGE_STORAGE"),
        ]
        for change, expected in cases:
            problems = check_production_settings(production_settings(**change))
            self.assertEqual(len(problems), 1, change)
            self.assertIn(expected, problems[0], change)


class EnvironmentStartupTests(unittest.TestCase):

    def test_local_start_without_firebase_refuses_instead_of_falling_back(self):
        code, output = import_app_with({})
        self.assertNotEqual(code, 0)
        self.assertIn("HostelHub configuration error", output)
        self.assertIn("FIREBASE_SERVICE_ACCOUNT", output)

    def test_local_start_uses_safe_defaults_and_local_photos(self):
        code, output = import_app_with({"FIREBASE_SERVICE_ACCOUNT": FAKE_SERVICE_ACCOUNT})
        self.assertEqual(code, 0, output)
        self.assertIn("DEBUG False SECURE False PRODUCTION False FIREBASE True IMAGES local", output)

    def test_no_firebase_storage_bucket_is_needed(self):
        """Cloud Storage needs the Blaze plan; the app must start and work without it."""
        import storage
        with open(storage.__file__, encoding="utf-8") as file:
            self.assertNotIn("firebase_admin", file.read())

    def test_vercel_switches_photos_off_and_refuses_local_files(self):
        code, output = import_app_with(GOOD_PRODUCTION_ENV)
        self.assertEqual(code, 0, output)
        self.assertIn("IMAGES none", output)
        code, output = import_app_with({**GOOD_PRODUCTION_ENV, "COMPLAINT_IMAGE_STORAGE": "local"})
        self.assertNotEqual(code, 0)
        self.assertIn("COMPLAINT_IMAGE_STORAGE=local cannot be used on Vercel", output)

    def test_broken_service_account_stops_the_app(self):
        code, output = import_app_with({"FIREBASE_SERVICE_ACCOUNT": '{"type": "service_account"}'})
        self.assertNotEqual(code, 0)
        self.assertIn("could not start Firebase", output)

    def test_key_from_another_firebase_project_stops_the_app(self):
        """The login page and the server must use the same project (hostelhub-83310)."""
        code, output = import_app_with({"FIREBASE_SERVICE_ACCOUNT": fake_service_account("old-project")})
        self.assertNotEqual(code, 0)
        self.assertIn("'old-project'", output)
        self.assertIn("'hostelhub-83310'", output)

    def test_production_refuses_to_start_without_settings(self):
        code, output = import_app_with({"VERCEL": "1"})
        self.assertNotEqual(code, 0)
        self.assertIn("HostelHub configuration error", output)
        for name in ("HOSTELHUB_SECRET_KEY", "FIREBASE_SERVICE_ACCOUNT"):
            self.assertIn(name, output)
        self.assertNotIn(GOOD_SECRET, output)

    def test_production_start_with_complete_settings(self):
        # HOSTELHUB_DEBUG=1 must be ignored in production.
        code, output = import_app_with({**GOOD_PRODUCTION_ENV, "HOSTELHUB_DEBUG": "1"})
        self.assertEqual(code, 0, output)
        self.assertIn("DEBUG False SECURE True PRODUCTION True FIREBASE True", output)
        self.assertNotIn("PRIVATE KEY", output)

    def test_hostelhub_env_production_behaves_like_vercel(self):
        env = {**GOOD_PRODUCTION_ENV, "HOSTELHUB_ENV": "production"}
        del env["VERCEL"]
        code, output = import_app_with(env)
        self.assertEqual(code, 0, output)
        self.assertIn("SECURE True PRODUCTION True", output)


class FirebaseProjectTests(unittest.TestCase):
    """The one Firebase project, hostelhub-83310, and nothing from the old one."""

    def test_web_config_is_complete_and_for_hostelhub_83310(self):
        from config import FIREBASE_PROJECT
        self.assertEqual(FIREBASE_PROJECT["projectId"], "hostelhub-83310")
        self.assertEqual(FIREBASE_PROJECT["authDomain"], "hostelhub-83310.firebaseapp.com")
        # A Google API key is 39 characters; a shortened copy is rejected by Google.
        self.assertRegex(FIREBASE_PROJECT["apiKey"], r"^AIza[0-9A-Za-z_-]{35}$")
        self.assertNotIn("storageBucket", FIREBASE_PROJECT)          # Firebase Storage is not used

    def test_no_reference_to_the_old_firebase_project(self):
        for folder, _dirs, files in os.walk(PROJECT_ROOT):
            if any(part in folder for part in (".git", ".venv", "__pycache__", "report")):
                continue
            for name in files:
                if name.endswith((".py", ".html", ".js", ".md", ".txt", ".example", ".json")) and name != "bank.md":
                    path = os.path.join(folder, name)
                    if os.path.abspath(path) == os.path.abspath(__file__):
                        continue
                    with open(path, encoding="utf-8", errors="ignore") as file:
                        self.assertNotIn("hostelhub-5a230", file.read(), path)

    def test_relative_key_file_is_read_from_the_project_folder(self):
        code, output = import_app_with({"FIREBASE_CREDENTIALS_FILE": "secrets/does-not-exist.json"})
        self.assertNotEqual(code, 0)                        # the file is missing, so Firebase cannot start
        # (the error message prints Windows paths with doubled backslashes)
        self.assertIn(os.path.join(PROJECT_ROOT, "secrets", "does-not-exist.json"), output.replace("\\\\", "\\"))

    def test_key_files_never_go_to_vercel(self):
        with open(os.path.join(PROJECT_ROOT, ".vercelignore"), encoding="utf-8") as file:
            ignored = file.read().split()
        for entry in (".env", "secrets/", "*firebase-adminsdk*.json", "uploads/"):
            self.assertIn(entry, ignored)


class VercelLayoutTests(unittest.TestCase):

    def test_entrypoint_exports_flask_app(self):
        from flask import Flask
        self.assertTrue(os.path.isfile(os.path.join(PROJECT_ROOT, "app.py")))
        self.assertIsInstance(app, Flask)

    def test_static_files_are_in_public_folder(self):
        self.assertEqual(os.path.normpath(app.static_folder), os.path.join(PROJECT_ROOT, "public", "static"))
        self.assertEqual(app.static_url_path, "/static")
        for relative in ("css/style.css", "js/main.js", "js/room-map.js", "js/complaint-form.js", "images/logo.svg"):
            self.assertTrue(os.path.isfile(os.path.join(PROJECT_ROOT, "public", "static", relative)), relative)
        self.assertFalse(os.path.isdir(os.path.join(PROJECT_ROOT, "static")), "old static/ folder should be gone")

    def test_requirements_and_python_version(self):
        with open(os.path.join(PROJECT_ROOT, "requirements.txt"), encoding="utf-8", newline="") as file:
            raw = file.read()
        self.assertNotIn("\r", raw, "requirements.txt must use LF line endings")
        lines = [line.strip().lower() for line in raw.splitlines() if line.strip() and not line.startswith("#")]
        for package in ("flask", "firebase-admin", "python-dotenv"):
            self.assertTrue(any(line.startswith(package) for line in lines), f"{package} missing from requirements.txt")
        self.assertFalse(any("psycopg" in line or "supabase" in line or "gunicorn" in line for line in lines), lines)
        self.assertEqual(len(lines), 3, lines)
        with open(os.path.join(PROJECT_ROOT, ".python-version"), encoding="utf-8") as file:
            self.assertRegex(file.read().strip(), r"^3\.(12|13|14)$")

    def test_upload_limit_is_below_vercel_request_limit(self):
        vercel_limit = int(4.5 * 1024 * 1024)
        self.assertLess(app.config["MAX_CONTENT_LENGTH"], vercel_limit)
        with open(os.path.join(PROJECT_ROOT, "public", "static", "js", "complaint-form.js"), encoding="utf-8") as file:
            self.assertIn("4 * 1024 * 1024", file.read())

    def test_times_are_indian_standard_time(self):
        india_now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
        self.assertLess(abs((local_now() - india_now).total_seconds()), 5)

    def test_env_example_lists_names_without_values(self):
        with open(os.path.join(PROJECT_ROOT, ".env.example"), encoding="utf-8") as file:
            assignments = [line.strip() for line in file if "=" in line and not line.lstrip().startswith("#")]
        names = {line.split("=", 1)[0] for line in assignments}
        for required in ("HOSTELHUB_SECRET_KEY", "FIREBASE_SERVICE_ACCOUNT", "FIREBASE_CREDENTIALS_FILE",
                         "FIREBASE_CLIENT_CONFIG", "COMPLAINT_IMAGE_STORAGE"):
            self.assertIn(required, names)
        self.assertNotIn("FIREBASE_STORAGE_BUCKET", names)      # Firebase Storage is not used
        self.assertNotIn("HOSTELHUB_DATA_BACKEND", names)       # Firebase is the only backend
        for line in assignments:
            self.assertEqual(line.split("=", 1)[1], "", line)

    def test_no_other_hosting_configuration(self):
        """Vercel runs app.py directly; the old Render Blueprint is gone."""
        self.assertFalse(os.path.exists(os.path.join(PROJECT_ROOT, "render.yaml")))


class NoSupabaseTests(unittest.TestCase):
    """Supabase, PostgreSQL and SQLite were removed: nothing should refer to them any more."""

    def test_no_supabase_or_postgres_references_in_code(self):
        checked = 0
        for folder, _dirs, files in os.walk(PROJECT_ROOT):
            if any(part in folder for part in (".git", ".venv", "__pycache__", "report", "node_modules")):
                continue
            for name in files:
                if not name.endswith((".py", ".html", ".js", ".css", ".txt", ".yaml", ".yml", ".example")):
                    continue
                path = os.path.join(folder, name)
                if os.path.abspath(path) == os.path.abspath(__file__):
                    continue   # this file names the old services on purpose
                with open(path, encoding="utf-8", errors="ignore") as file:
                    text = file.read().lower()
                checked += 1
                words = ["supabase", "psycopg", "postgresql", "postgres", "import sqlite3"]
                if os.path.basename(folder) != "tests":        # the tests check that these are absent
                    words += ["quick login", "quick-login", "demo login", "demo-login"]
                for word in words:
                    self.assertNotIn(word, text, f"{word} still referenced in {path}")
        self.assertGreater(checked, 20)


@unittest.skipUnless(shutil.which("git") and os.path.isdir(os.path.join(PROJECT_ROOT, ".git")), "not a git checkout")
class GitHygieneTests(unittest.TestCase):

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30)

    def test_private_files_are_ignored(self):
        for path in (".env", ".env.production", ".venv/x", "bank.md", "routes/__pycache__/x.pyc",
                     # Firebase service-account keys, as downloaded from the console
                     "hostelhub-83310-firebase-adminsdk-fbsvc-0123456789.json", "serviceAccountKey.json",
                     "firebase-credentials.json", "secrets/key.json"):
            self.assertEqual(self.git("check-ignore", "-q", path).returncode, 0, f"{path} should be ignored")
        self.assertEqual(self.git("check-ignore", "-q", "uploads/complaints/photo.png").returncode, 0)
        for path in (".env.example", "app.py", "public/static/css/style.css", "uploads/complaints/.gitkeep"):
            self.assertEqual(self.git("check-ignore", "-q", path).returncode, 1, f"{path} should NOT be ignored")

    def test_no_private_files_are_tracked(self):
        tracked = self.git("ls-files").stdout.splitlines()
        self.assertTrue(tracked)
        for path in tracked:
            self.assertFalse(path.endswith((".db", ".sqlite", ".sqlite3", ".pyc")), path)
            self.assertFalse(os.path.basename(path).startswith(".env") and path != ".env.example", path)
            self.assertNotIn(path, ("bank.md",))
            self.assertNotIn("firebase-adminsdk", path)
            if path.startswith("uploads/"):
                self.assertEqual(path, "uploads/complaints/.gitkeep")     # never a student's photo


if __name__ == "__main__":
    unittest.main()
