"""Deployment readiness: production settings, environment variables, Vercel layout and Git hygiene.

Some tests start a separate Python process with different environment variables,
because config.py reads the environment when the app is first imported.
"""

import os
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

from base import app
from config import DEV_SECRET_KEY, check_production_settings
from database import local_now

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOOD_SECRET = "a" * 64

# A complete, valid production configuration (fake values, nothing real).
GOOD_PRODUCTION_ENV = {
    "VERCEL": "1",
    "HOSTELHUB_SECRET_KEY": GOOD_SECRET,
    "DATABASE_URL": "postgresql://user:password@db.example.com:6543/postgres",
    "SUPABASE_URL": "https://example-project.supabase.co",
    "SUPABASE_SECRET_KEY": "example-secret",
}
DEPLOYMENT_VARIABLES = ("VERCEL", "HOSTELHUB_ENV", "HOSTELHUB_DEBUG", "HOSTELHUB_SECRET_KEY", "DATABASE_URL",
                        "SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_BUCKET", "HOSTELHUB_TEST_DATABASE_URL")


def import_app_with(env):
    """Import app.py in a fresh Python process with the given environment variables.

    Returns (exit code, output). The child prints the settings we want to check.
    """
    child_env = {key: value for key, value in os.environ.items() if key not in DEPLOYMENT_VARIABLES}
    child_env.update(env)
    code = ("from app import app; c = app.config; "
            "print('DEBUG', c['DEBUG'], 'SECURE', c['SESSION_COOKIE_SECURE'], 'PRODUCTION', c['IS_PRODUCTION'], "
            "'POSTGRES', c['DATABASE_URL'].startswith('postgresql://'), 'BUCKET', c['SUPABASE_BUCKET'])")
    result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, env=child_env,
                            capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout + result.stderr


def production_settings(**changes):
    settings = {"IS_PRODUCTION": True, "SECRET_KEY": GOOD_SECRET, "DEBUG": False,
                "DATABASE_URL": GOOD_PRODUCTION_ENV["DATABASE_URL"],
                "SUPABASE_URL": GOOD_PRODUCTION_ENV["SUPABASE_URL"], "SUPABASE_SECRET_KEY": "example-secret"}
    settings.update(changes)
    return settings


class ProductionSettingsTests(unittest.TestCase):

    def test_local_development_needs_no_settings(self):
        local = {"IS_PRODUCTION": False, "SECRET_KEY": DEV_SECRET_KEY, "DEBUG": True,
                 "DATABASE_URL": "", "SUPABASE_URL": "", "SUPABASE_SECRET_KEY": ""}
        self.assertEqual(check_production_settings(local), [])

    def test_complete_production_settings_pass(self):
        self.assertEqual(check_production_settings(production_settings()), [])

    def test_each_unsafe_production_setting_is_reported(self):
        cases = [
            ({"SECRET_KEY": DEV_SECRET_KEY}, "HOSTELHUB_SECRET_KEY"),
            ({"SECRET_KEY": "too-short"}, "HOSTELHUB_SECRET_KEY"),
            ({"DEBUG": True}, "Debug"),
            ({"DATABASE_URL": ""}, "DATABASE_URL"),
            ({"DATABASE_URL": "sqlite:///database/hostelhub.db"}, "DATABASE_URL"),
            ({"SUPABASE_URL": ""}, "SUPABASE_URL"),
            ({"SUPABASE_URL": "http://insecure.example.com"}, "SUPABASE_URL"),
            ({"SUPABASE_SECRET_KEY": ""}, "SUPABASE_SECRET_KEY"),
        ]
        for change, expected in cases:
            problems = check_production_settings(production_settings(**change))
            self.assertEqual(len(problems), 1, change)
            self.assertIn(expected, problems[0], change)


class EnvironmentStartupTests(unittest.TestCase):

    def test_local_start_uses_safe_defaults(self):
        code, output = import_app_with({"DATABASE_URL": ""})
        self.assertEqual(code, 0, output)
        self.assertIn("DEBUG False SECURE False PRODUCTION False POSTGRES False BUCKET complaint-photos", output)

    def test_production_refuses_to_start_without_settings(self):
        code, output = import_app_with({"VERCEL": "1"})
        self.assertNotEqual(code, 0)
        self.assertIn("HostelHub production configuration error", output)
        for name in ("HOSTELHUB_SECRET_KEY", "DATABASE_URL", "SUPABASE_URL"):
            self.assertIn(name, output)
        self.assertNotIn(GOOD_SECRET, output)

    def test_production_start_with_complete_settings(self):
        # HOSTELHUB_DEBUG=1 must be ignored in production.
        code, output = import_app_with({**GOOD_PRODUCTION_ENV, "HOSTELHUB_DEBUG": "1"})
        self.assertEqual(code, 0, output)
        self.assertIn("DEBUG False SECURE True PRODUCTION True POSTGRES True", output)

    def test_hostelhub_env_production_behaves_like_vercel(self):
        env = {**GOOD_PRODUCTION_ENV, "HOSTELHUB_ENV": "production"}
        del env["VERCEL"]
        code, output = import_app_with(env)
        self.assertEqual(code, 0, output)
        self.assertIn("SECURE True PRODUCTION True", output)

    def test_production_never_seeds_the_database_on_startup(self):
        """With a PostgreSQL URL, importing the app must not try to create or connect to a database."""
        code, output = import_app_with(GOOD_PRODUCTION_ENV)   # db.example.com does not exist
        self.assertEqual(code, 0, output)
        self.assertNotIn("psycopg", output)


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
        with open(os.path.join(PROJECT_ROOT, "requirements.txt"), encoding="utf-8") as file:
            lines = [line.strip().lower() for line in file if line.strip() and not line.startswith("#")]
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(any(line.startswith("flask") for line in lines))
        self.assertTrue(any(line.startswith("psycopg[binary]") for line in lines))
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
        for required in ("HOSTELHUB_SECRET_KEY", "DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY"):
            self.assertIn(required, names)
        for line in assignments:
            name, value = line.split("=", 1)
            # Only the non-secret default bucket name may have a value.
            self.assertTrue(value == "" or (name == "SUPABASE_BUCKET" and value == "complaint-photos"), line)


@unittest.skipUnless(shutil.which("git") and os.path.isdir(os.path.join(PROJECT_ROOT, ".git")), "not a git checkout")
class GitHygieneTests(unittest.TestCase):

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30)

    def test_private_files_are_ignored(self):
        for path in ("database/hostelhub.db", "any.sqlite3", ".env", ".env.production", ".venv/x",
                     "uploads/complaints/photo.png", "bank.md", "routes/__pycache__/x.pyc"):
            self.assertEqual(self.git("check-ignore", "-q", path).returncode, 0, f"{path} should be ignored")
        for path in (".env.example", "uploads/complaints/.gitkeep", "app.py", "public/static/css/style.css"):
            self.assertEqual(self.git("check-ignore", "-q", path).returncode, 1, f"{path} should NOT be ignored")

    def test_no_private_files_are_tracked(self):
        tracked = self.git("ls-files").stdout.splitlines()
        self.assertTrue(tracked)
        for path in tracked:
            self.assertFalse(path.endswith((".db", ".sqlite", ".sqlite3", ".pyc")), path)
            self.assertFalse(os.path.basename(path).startswith(".env") and path != ".env.example", path)
            self.assertNotIn(path, ("bank.md",))
            if path.startswith("uploads/"):
                self.assertEqual(path, "uploads/complaints/.gitkeep")


if __name__ == "__main__":
    unittest.main()
