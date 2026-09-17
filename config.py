"""
config.py — Application settings and fixed "choice lists".

Everything that other files need to agree on (file paths, allowed
complaint categories, status names, ...) lives here so it is defined
in exactly one place.
"""

import os

# Absolute path of the folder that contains this file (the project root).
BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Flask reads UPPERCASE attributes of this class as settings."""

    # Used by Flask to sign the session cookie. In production this must be
    # a long random value provided through an environment variable.
    SECRET_KEY = os.environ.get("HOSTELHUB_SECRET_KEY", "dev-only-change-this-secret-key")

    # SQLite database file and the SQL file that creates the tables.
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
