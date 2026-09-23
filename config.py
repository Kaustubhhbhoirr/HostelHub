"""
config.py — Application settings and fixed "choice lists".

Everything that other files need to agree on (file paths, allowed
complaint categories, status names, ...) lives here so it is defined
in exactly one place.
"""

import json
import os

# Absolute path of the folder that contains this file (the project root).
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# ------------------------------------------------------------------
# Firebase: the only backend HostelHub uses
# ------------------------------------------------------------------
# Firestore stores every record and Firebase Authentication handles email/password
# and Google sign-in. (Firebase Cloud Storage is not used: it needs the paid Blaze
# plan. Complaint photos are optional; see COMPLAINT_IMAGE_STORAGE below.)
# The same Firebase project is used on a laptop and on Vercel, so a complaint
# submitted on localhost is immediately visible to the warden anywhere.
#
# This web configuration is PUBLIC (Firebase is designed this way): it only
# identifies the project for the browser. The secret service-account key is read
# from the environment, never from this file.
FIREBASE_PROJECT = {
    "apiKey": "AIzaSyANMfxmhPsMoIeKe7YInMaAAt405Gm12Nc",
    "authDomain": "hostelhub-83310.firebaseapp.com",
    "projectId": "hostelhub-83310",
    "messagingSenderId": "858224007054",
    "appId": "1:858224007054:web:d0eef80405c00182bc76df",
    "measurementId": "G-YYW88HGTSZ",
}

# ------------------------------------------------------------------
# Where is the app running?
# ------------------------------------------------------------------
# Environment variables are settings given to the program from OUTSIDE the
# code (the terminal, or the Vercel dashboard). Secrets live there, never in Git.
#
# Vercel automatically sets VERCEL=1 on its servers, so the app can never silently
# run in development mode there. HOSTELHUB_ENV=production lets you test production
# behaviour on your own computer too.
ON_VERCEL = os.environ.get("VERCEL") == "1"
IS_PRODUCTION = ON_VERCEL or os.environ.get("HOSTELHUB_ENV") == "production"

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

    # Complaint photos are optional (see storage.py):
    #   local - files in uploads/complaints/ on this computer (default locally)
    #   none  - photo uploads switched off (default on Vercel, whose disk is temporary)
    # The complaint document keeps only the generated file name, never the image.
    ON_VERCEL = ON_VERCEL
    COMPLAINT_IMAGE_STORAGE = (os.environ.get("COMPLAINT_IMAGE_STORAGE", "").strip().lower()
                               or ("none" if ON_VERCEL else "local"))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads", "complaints")

    # Flask rejects any request body bigger than 4 MB (returns error 413).
    # Vercel refuses request bodies over 4.5 MB, so the limit stays below that.
    MAX_CONTENT_LENGTH = 4 * 1024 * 1024

    # Session cookie hardening: JavaScript cannot read the cookie, and modern
    # browsers do not send it with most cross-site POST requests. This is only an
    # EXTRA defence; the real CSRF protection is the token check in routes/auth.py.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # In production the site is served over HTTPS, so the cookie is never sent over plain HTTP.
    SESSION_COOKIE_SECURE = IS_PRODUCTION

    # Firebase:
    #   FIREBASE_CLIENT_CONFIG   - PUBLIC web config (JSON) sent to the login page. It is
    #                              designed to be public; the project default is below.
    #   FIREBASE_SERVICE_ACCOUNT - SECRET service-account key (JSON). The server uses it for
    #                              Firebase Authentication and Firestore.
    #                              Never commit it and never send it to the browser.
    #   FIREBASE_CREDENTIALS_FILE - alternative to the variable above: the path of a
    #                              service-account JSON file kept outside Git.
    FIREBASE_CLIENT_CONFIG = os.environ.get("FIREBASE_CLIENT_CONFIG", "").strip() or json.dumps(FIREBASE_PROJECT)
    FIREBASE_SERVICE_ACCOUNT = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
    # A relative path (e.g. secrets/key.json) is read from the project folder.
    FIREBASE_CREDENTIALS_FILE = (
        os.path.normpath(os.path.join(BASE_DIR, os.environ["FIREBASE_CREDENTIALS_FILE"].strip()))
        if os.environ.get("FIREBASE_CREDENTIALS_FILE", "").strip() else "")


def check_production_settings(settings):
    """Return a list of configuration problems that make production unsafe.

    `settings` is Flask's app.config (a dictionary). Firebase credentials are
    required everywhere; the secret key and debug rules only in production.
    """
    problems = []
    # Firebase is the only backend, so its credentials are required everywhere,
    # including on a laptop: there is no local database to fall back to.
    if not (settings.get("FIREBASE_SERVICE_ACCOUNT") or settings.get("FIREBASE_CREDENTIALS_FILE")):
        problems.append("FIREBASE_SERVICE_ACCOUNT (or FIREBASE_CREDENTIALS_FILE) must be set: "
                        "HostelHub stores all data in Firebase and has no local database.")
    image_storage = settings.get("COMPLAINT_IMAGE_STORAGE", "none")
    if image_storage not in ("local", "none"):
        problems.append("COMPLAINT_IMAGE_STORAGE must be 'local' or 'none'.")
    elif image_storage == "local" and settings.get("ON_VERCEL"):
        problems.append("COMPLAINT_IMAGE_STORAGE=local cannot be used on Vercel: its disk is temporary, "
                        "so photos would be lost. Use 'none' until a cloud image service is added.")
    if settings["IS_PRODUCTION"]:
        if settings["SECRET_KEY"] == DEV_SECRET_KEY or len(settings["SECRET_KEY"]) < 32:
            problems.append("HOSTELHUB_SECRET_KEY must be set to a random value of at least 32 characters.")
        if settings["DEBUG"]:
            problems.append("Debug mode must be off in production.")
    return problems


# ------------------------------------------------------------------
# Choice lists used by forms, validation and templates
# ------------------------------------------------------------------

# MES college email rules:
#   - every STUDENT account must end with @student.mes.ac.in (their college Google account)
#   - staff such as the warden use @mes.ac.in
# Only these two domains may sign in with Google.
STUDENT_EMAIL_DOMAIN = "@student.mes.ac.in"
STAFF_EMAIL_DOMAIN = "@mes.ac.in"
COLLEGE_EMAIL_DOMAINS = (STUDENT_EMAIL_DOMAIN, STAFF_EMAIL_DOMAIN)

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
