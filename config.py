"""
config.py — Application settings and fixed "choice lists".

Everything that other files need to agree on (file paths, allowed
complaint categories, status names, ...) lives here so it is defined
in exactly one place.
"""

import os

# Absolute path of the folder that contains this file (the project root).
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# ------------------------------------------------------------------
# Where is the app running?
# ------------------------------------------------------------------
# Environment variables are settings given to the program from OUTSIDE the
# code (the terminal, or the Vercel dashboard). Secrets live there, never in Git.
#
# Vercel automatically sets VERCEL=1 on its servers. HOSTELHUB_ENV=production
# lets you test production behaviour on your own computer too.
IS_PRODUCTION = os.environ.get("VERCEL") == "1" or os.environ.get("HOSTELHUB_ENV") == "production"

# Fallback key for local development ONLY. Production refuses to start with it.
DEV_SECRET_KEY = "dev-only-change-this-secret-key"


class Config:
    """Flask reads UPPERCASE attributes of this class as settings."""

    IS_PRODUCTION = IS_PRODUCTION

    # Used by Flask to sign the session cookie. In production this must be
    # a long random value provided through an environment variable.
    SECRET_KEY = os.environ.get("HOSTELHUB_SECRET_KEY") or DEV_SECRET_KEY

    # Debug pages show source code and stack traces, so they are only allowed
    # locally and only when explicitly requested with HOSTELHUB_DEBUG=1.
    DEBUG = os.environ.get("HOSTELHUB_DEBUG") == "1" and not IS_PRODUCTION

    # PostgreSQL connection string for deployment, e.g. postgresql://user:password@host:6543/postgres
    # Empty (the default) means: use the local SQLite file below.
    DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

    # SQLite database file (local development) and the SQL file that creates the tables.
    DATABASE = os.path.join(BASE_DIR, "database", "hostelhub.db")
    SCHEMA_FILE = os.path.join(BASE_DIR, "database", "schema.sql")

    # Complaint images are saved here and the database only stores the file name.
    # This folder is deliberately NOT inside /static: images are sent through a
    # route that first checks who is asking (see complaint_image in complaints.py).
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "complaints")

    # Flask rejects any request body bigger than 5 MB (returns error 413).
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024

    # Session cookie hardening: JavaScript cannot read the cookie, and modern
    # browsers do not send it with most cross-site POST requests. This is only an
    # EXTRA defence; the real CSRF protection is the token check in routes/auth.py.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # In production the site is served over HTTPS, so the cookie is never sent over plain HTTP.
    SESSION_COOKIE_SECURE = IS_PRODUCTION


def check_production_settings(settings):
    """Return a list of configuration problems that make production unsafe.

    `settings` is Flask's app.config (a dictionary). Locally this always
    returns an empty list, so development needs no extra setup.
    """
    problems = []
    if settings["IS_PRODUCTION"]:
        if settings["SECRET_KEY"] == DEV_SECRET_KEY or len(settings["SECRET_KEY"]) < 32:
            problems.append("HOSTELHUB_SECRET_KEY must be set to a random value of at least 32 characters.")
        if settings["DEBUG"]:
            problems.append("Debug mode must be off in production.")
        # Vercel's file system is temporary, so a SQLite file would lose data.
        if not settings["DATABASE_URL"].startswith(("postgres://", "postgresql://")):
            problems.append("DATABASE_URL must point to a hosted PostgreSQL database.")
    return problems


# ------------------------------------------------------------------
# Choice lists used by forms, validation and templates
# ------------------------------------------------------------------

# Only college accounts may be created (Firebase will enforce this later too).
ALLOWED_EMAIL_DOMAIN = "mes.ac.in"

# A set is used because we only need fast "is this extension allowed?" checks.
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}

COMPLAINT_CATEGORIES = (
    "Fan", "Light", "Electrical", "Plumbing", "Furniture",
    "Bathroom", "Door/Lock", "Water", "Cleaning", "Other",
)

COMPLAINT_PRIORITIES = ("Low", "Medium", "High")

COMPLAINT_STATUSES = ("Submitted", "Acknowledged", "In Progress", "Resolved", "Rejected")

# Statuses that still need the warden's attention.
OPEN_COMPLAINT_STATUSES = ("Submitted", "Acknowledged", "In Progress")

# Which status a complaint may move to from its current status.
# Resolved and Rejected are final, so they have no next statuses.
COMPLAINT_NEXT_STATUSES = {
    "Submitted": ["Acknowledged", "In Progress", "Resolved", "Rejected"],
    "Acknowledged": ["In Progress", "Resolved", "Rejected"],
    "In Progress": ["Resolved", "Rejected"],
    "Resolved": [],
    "Rejected": [],
}

ROOM_CHANGE_REASONS = (
    "Roommate compatibility issue",
    "Health or medical reason",
    "Room maintenance problem",
    "Want to stay closer to classmates",
    "Need a quieter room for studies",
    "Other",
)

ROOM_CHANGE_STATUSES = ("Pending", "Approved", "Rejected", "Cancelled")

ROOM_STATUSES = ("active", "maintenance", "inactive")

# Bed statuses a warden may set by hand. "occupied" and "reserved" are only
# set by the allocation and room-change logic, never directly.
MANUAL_BED_STATUSES = ("available", "maintenance", "unavailable")

COLLEGE_REQUEST_TYPES = (
    "Repair", "Replacement", "New Equipment", "Electrical Work",
    "Plumbing Work", "Furniture Replacement", "Other",
)

COLLEGE_REQUEST_STATUSES = (
    "Draft", "Sent to College", "Under Review", "Approved", "Rejected", "Completed",
)

DEPARTMENTS = (
    "Computer Engineering",
    "Information Technology",
    "Electronics & Telecommunication",
    "Mechanical Engineering",
    "AI & Data Science",
)
