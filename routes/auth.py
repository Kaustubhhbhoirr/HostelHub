"""
routes/auth.py — Login, logout and page protection.

There are two ways to prove who you are, and both end the same way:

    authenticate_local(email, password)   -> email + password (demo accounts)
    authenticate_google(id_token)         -> "Sign in with Google" via Firebase
    login_user(user)                      -> stores ONLY the user id in the session

Google login flow:
    1. The login page opens Google's sign-in popup (Firebase JavaScript SDK).
    2. Firebase gives the browser a signed "ID token" for that Google account.
    3. The browser POSTs the token (plus our CSRF token) to /firebase-login.
    4. authenticate_google() asks the Firebase Admin SDK to verify the token's
       signature, then looks the email up in OUR users table.
    5. Only accounts the warden has already registered can log in, and the
       role (student / warden) still comes from our database, never from Google.
"""

import hmac
import secrets
from functools import wraps

import json

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash

from config import COLLEGE_EMAIL_DOMAINS
from database import DatabaseError, execute, get_db, query_one

auth_bp = Blueprint("auth", __name__)


# ------------------------------------------------------------------
# Authentication functions
# ------------------------------------------------------------------

def authenticate_local(email, password):
    """Return the user row if the email and password are correct, else None."""
    user = query_one("SELECT * FROM users WHERE email = ?", (email,))
    if user is None:
        return None
    # The database stores only a hash. check_password_hash() hashes the typed
    # password the same way and compares the results.
    if not check_password_hash(user["password_hash"], password):
        return None
    return user


class GoogleLoginError(Exception):
    """A Google sign-in was refused. `message` is safe to show; `status` is the HTTP code."""

    def __init__(self, message, status):
        super().__init__(message)
        self.message = message
        self.status = status


def authenticate_google(id_token):
    """Verify a Firebase ID token and return the matching HostelHub user.

    Raises GoogleLoginError with a friendly message if anything is wrong.
    """
    if not id_token:
        raise GoogleLoginError("Google did not return a sign-in token. Please try again.", 400)

    # Imported here so the rest of the app still works if firebase-admin is missing.
    import firebase_admin
    from firebase_admin import auth as firebase_auth

    if not firebase_admin._apps:
        current_app.logger.error("Google login attempted but the Firebase Admin SDK is not initialised.")
        raise GoogleLoginError("Google sign-in is not configured on this server.", 503)

    try:
        # Checks Google's signature, the expiry time and that the token was made for OUR Firebase project.
        claims = firebase_auth.verify_id_token(id_token)
    except (ValueError, firebase_auth.InvalidIdTokenError, firebase_auth.ExpiredIdTokenError,
            firebase_auth.RevokedIdTokenError, firebase_auth.CertificateFetchError) as error:
        current_app.logger.warning("Rejected Google ID token: %s", error)
        raise GoogleLoginError("Your Google sign-in could not be verified. Please try again.", 401)

    email = (claims.get("email") or "").strip().lower()
    uid = claims.get("uid") or claims.get("sub")
    if not email or not claims.get("email_verified"):
        raise GoogleLoginError("Your Google account has no verified email address.", 403)
    if not email.endswith(COLLEGE_EMAIL_DOMAINS):
        raise GoogleLoginError("Please sign in with your MES college Google account "
                               "(@student.mes.ac.in or @mes.ac.in).", 403)

    # The warden must have registered this email first — Google alone is not enough.
    user = query_one("SELECT * FROM users WHERE email = ?", (email,))
    if user is None:
        raise GoogleLoginError(f"{email} is not registered in HostelHub. "
                               "Please ask the hostel warden to add your account.", 404)
    if not user["is_active"]:
        raise GoogleLoginError("This account has been deactivated. Please contact the hostel office.", 403)

    if user["firebase_uid"] and user["firebase_uid"] != uid:
        # The email is already linked to a different Google account: refuse, don't overwrite.
        raise GoogleLoginError("This HostelHub account is linked to a different Google account.", 403)
    if not user["firebase_uid"]:
        # First Google sign-in: remember which Google account belongs to this user.
        try:
            execute("UPDATE users SET firebase_uid = ? WHERE id = ?", (uid, user["id"]))
            get_db().commit()
        except DatabaseError:
            get_db().rollback()
            raise GoogleLoginError("Could not complete sign-in. Please try again.", 500)
    return user


def init_firebase(app):
    """Start the Firebase Admin SDK once, when the app starts (called from app.py).

    Returns True when Google sign-in can work. Problems are logged, never shown to users,
    and password login keeps working either way.
    """
    import firebase_admin
    from firebase_admin import credentials

    if firebase_admin._apps:
        return True
    raw = app.config.get("FIREBASE_SERVICE_ACCOUNT", "")
    if not raw:
        app.logger.info("FIREBASE_SERVICE_ACCOUNT is not set: Google sign-in is disabled.")
        return False
    try:
        firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
        return True
    except (ValueError, KeyError) as error:
        # Usually a broken copy-paste of the JSON (missing quote or line break).
        app.logger.error("Could not start Firebase Admin SDK (check FIREBASE_SERVICE_ACCOUNT): %s",
                         type(error).__name__)
        return False


def firebase_client_config():
    """The PUBLIC Firebase web config for the login page, or None if Google login is not set up.

    (This config is designed to be public; the secret service account never leaves the server.)
    """
    import firebase_admin

    raw = current_app.config.get("FIREBASE_CLIENT_CONFIG", "")
    if not raw or not firebase_admin._apps:
        return None      # no button if the server could not verify Google tokens anyway
    try:
        config = json.loads(raw)
    except ValueError:
        current_app.logger.error("FIREBASE_CLIENT_CONFIG is not valid JSON.")
        return None
    needed = ("apiKey", "authDomain", "projectId", "appId")
    return {key: config[key] for key in needed} if all(config.get(key) for key in needed) else None


def login_user(user):
    """Start a session for this user.

    We store ONLY the user id. The role is always read again from the
    database, so nobody can become a warden by editing the browser.
    """
    session.clear()
    session["user_id"] = user["id"]


def home_url_for(role):
    """Each role has a different home page."""
    if role == "warden":
        return url_for("warden.dashboard")
    return url_for("student.dashboard")


@auth_bp.before_app_request
def load_logged_in_user():
    """Runs before every request: loads the logged-in user into g.user."""
    g.user = None
    user_id = session.get("user_id")
    if user_id is None:
        return

    user = query_one(
        """SELECT id, name, email, role, student_id, phone, department, year_of_study,
                  is_active, created_at
           FROM users WHERE id = ?""",
        (user_id,),
    )
    # A deleted or deactivated account is logged out immediately.
    if user is None or not user["is_active"]:
        session.clear()
        return
    g.user = user


# ------------------------------------------------------------------
# CSRF protection (Cross-Site Request Forgery)
# ------------------------------------------------------------------
# Problem: another website could secretly submit a form to HostelHub using
# the logged-in user's browser cookie (for example "approve room change").
# Fix: every session gets a random secret token. Every POST form sends it back
# in a hidden field. A forged form on another site cannot know the token, so
# the request is rejected.

def get_csrf_token():
    """Return this session's CSRF token, creating one the first time."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


@auth_bp.app_context_processor
def inject_csrf_token():
    """Lets templates write {{ csrf_token() }} inside forms."""
    return {"csrf_token": get_csrf_token}


@auth_bp.before_app_request
def check_csrf_token():
    """Reject any POST request that does not carry the correct token."""
    if request.method != "POST":
        return
    expected = session.get("csrf_token")
    sent = request.form.get("csrf_token", "")
    # compare_digest compares in constant time (does not leak how many characters matched).
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400)


# ------------------------------------------------------------------
# Page protection decorators
# ------------------------------------------------------------------
# Usage:
#     @student_bp.route("/dashboard")
#     @student_required
#     def dashboard(): ...
#
# A decorator wraps the view function and runs a check before it.

def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped_view


def student_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login"))
        if g.user["role"] != "student":
            abort(403)  # backend check — hiding menu links is not enough
        return view(*args, **kwargs)
    return wrapped_view


def warden_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login"))
        if g.user["role"] != "warden":
            abort(403)
        return view(*args, **kwargs)
    return wrapped_view


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(home_url_for(g.user["role"]))

    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please enter both email and password.", "danger")
        else:
            user = authenticate_local(email, password)
            if user is None:
                # Same message for "wrong email" and "wrong password" so
                # attackers cannot find out which emails exist.
                flash("Invalid email or password.", "danger")
            elif not user["is_active"]:
                flash("This account has been deactivated. Please contact the hostel office.", "danger")
            else:
                login_user(user)
                first_name = user["name"].replace("Dr. ", "").split()[0]
                flash(f"Welcome back, {first_name}!", "success")
                return redirect(home_url_for(user["role"]))

    return render_template("auth/login.html", email=email, firebase_config=firebase_client_config())


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


# ------------------------------------------------------------------
# Google sign-in (Firebase)
# ------------------------------------------------------------------

@auth_bp.route("/firebase-login", methods=["POST"])
def firebase_login():
    """Called by the login page's JavaScript after the Google popup.

    Like every POST, it must carry our CSRF token (checked in check_csrf_token()).
    It answers with JSON because JavaScript, not a normal form, reads the reply.
    """
    try:
        user = authenticate_google(request.form.get("idToken", ""))
    except GoogleLoginError as error:
        return jsonify({"error": error.message}), error.status

    login_user(user)
    first_name = user["name"].replace("Dr. ", "").split()[0]
    flash(f"Welcome back, {first_name}!", "success")
    return jsonify({"success": True, "redirect": home_url_for(user["role"])})
