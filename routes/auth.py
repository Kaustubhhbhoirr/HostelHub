"""
routes/auth.py — Login, logout and page protection.

Firebase Authentication is the only place where credentials live. HostelHub never
stores or checks a password itself.

    1. The login page signs the user in with the Firebase JavaScript SDK, either
       with an email and password or with the Google pop-up.
    2. Firebase gives the browser a signed "ID token" for that account.
    3. The browser POSTs the token (plus our CSRF token) to /firebase-login.
    4. authenticate_firebase() verifies the token with the Firebase Admin SDK and
       then finds the matching user document in Firestore.
    5. login_user() stores ONLY the Firestore user id in the session. The role is
       read from the user document on every request, never from the token.

Only MES college accounts may sign in, and only if the hostel office has already
registered them in Firestore (the warden adds students; wardens are added when the
hostel is set up). Signing in with Google does not create an account by itself.

    name@student.mes.ac.in  ->  student
    name@mes.ac.in          ->  warden

The stored role must match the email domain, so a student address can never hold
a warden record by mistake.

The same person may sign in with a password on one day and with Google on the
next. Firebase keeps ONE account per email address and links both sign-in methods
to it (same uid), and the user document is matched by email, so no second
HostelHub account is ever created. The uid is stored on the document the first
time; a different Firebase account claiming the same address is refused.
"""

import hmac
import json
import secrets
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)

from config import COLLEGE_EMAIL_DOMAINS, FIREBASE_PROJECT, STAFF_EMAIL_DOMAIN, STUDENT_EMAIL_DOMAIN
from data import DatabaseError, get_store

auth_bp = Blueprint("auth", __name__)


# ------------------------------------------------------------------
# Firebase Authentication
# ------------------------------------------------------------------

class LoginError(Exception):
    """A sign-in was refused. `message` is safe to show; `status` is the HTTP code."""

    def __init__(self, message, status):
        super().__init__(message)
        self.message = message
        self.status = status


def role_for_email(email):
    """The role an institutional email address gets. Nobody can choose their own role."""
    if email.endswith(STUDENT_EMAIL_DOMAIN):
        return "student"
    if email.endswith(STAFF_EMAIL_DOMAIN):
        return "warden"
    return None


def verify_id_token(id_token):
    """Check the token's signature, expiry and project with the Firebase Admin SDK."""
    import firebase_admin
    from firebase_admin import auth as firebase_auth

    if not firebase_admin._apps:
        current_app.logger.error("Sign-in attempted but the Firebase Admin SDK is not initialised.")
        raise LoginError("Sign-in is not configured on this server.", 503)
    try:
        return firebase_auth.verify_id_token(id_token)
    except (ValueError, firebase_auth.InvalidIdTokenError, firebase_auth.ExpiredIdTokenError,
            firebase_auth.RevokedIdTokenError, firebase_auth.CertificateFetchError) as error:
        current_app.logger.warning("Rejected Firebase ID token: %s", error)
        raise LoginError("Your sign-in could not be verified. Please try again.", 401)


def authenticate_firebase(id_token):
    """Verify a Firebase ID token and return the matching HostelHub user document.

    Raises LoginError with a friendly message if anything is wrong.
    """
    if not id_token:
        raise LoginError("No sign-in token was received. Please try again.", 400)

    claims = verify_id_token(id_token)
    email = (claims.get("email") or "").strip().lower()
    uid = claims.get("uid") or claims.get("sub")
    provider = (claims.get("firebase") or {}).get("sign_in_provider", "")

    if not email:
        raise LoginError("This account has no email address.", 403)
    # A Google account always carries a verified address. An email/password account
    # is created by the hostel office with the Admin SDK, so its address is trusted
    # even before the user confirms it.
    if provider not in ("password", "custom") and not claims.get("email_verified"):
        raise LoginError("Your Google account has no verified email address.", 403)
    if not email.endswith(COLLEGE_EMAIL_DOMAINS):
        raise LoginError("Please sign in with your MES college account "
                         "(@student.mes.ac.in or @mes.ac.in).", 403)

    store = get_store()
    user = store.first("users", email=email)
    if user is None:
        raise LoginError(f"{email} is not registered in HostelHub. "
                         "Please ask the hostel warden to add your account.", 404)
    if user["role"] != role_for_email(email):
        current_app.logger.error("User %s has role %r, which does not match the email domain.",
                                 user["id"], user["role"])
        raise LoginError("This account is not set up correctly. Please contact the hostel office.", 403)
    if not user["is_active"]:
        raise LoginError("This account has been deactivated. Please contact the hostel office.", 403)

    if user.get("firebase_uid") and user["firebase_uid"] != uid:
        # The address already belongs to another Firebase account: refuse, don't overwrite.
        raise LoginError("This HostelHub account is linked to a different sign-in account. "
                         "Please contact the hostel office.", 403)
    if not user.get("firebase_uid"):
        # First sign-in: remember which Firebase account belongs to this user.
        try:
            store.update("users", user["id"], {"firebase_uid": uid})
            store.commit()
            user["firebase_uid"] = uid
        except DatabaseError:
            store.rollback()
            raise LoginError("Could not complete sign-in. Please try again.", 500)
    return user


def init_firebase(app):
    """Start the Firebase Admin SDK (Authentication and Firestore).

    HostelHub cannot work without it, so a failure here is fatal: there is no
    local database to fall back to.
    """
    import firebase_admin
    from firebase_admin import credentials

    if firebase_admin._apps:
        return True

    raw = app.config.get("FIREBASE_SERVICE_ACCOUNT", "")
    path = app.config.get("FIREBASE_CREDENTIALS_FILE", "")
    try:
        if raw:
            certificate = credentials.Certificate(json.loads(raw))
        elif path:
            certificate = credentials.Certificate(path)
        else:
            raise ValueError("no service account provided")
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "HostelHub could not start Firebase. Set FIREBASE_SERVICE_ACCOUNT (the service-account "
            "JSON) or FIREBASE_CREDENTIALS_FILE (its path). "
            f"Reason: {type(error).__name__}: {error}"
        ) from error

    # The login page (web config) and the server (service account) must use the SAME
    # Firebase project, otherwise every sign-in token is rejected and the data would
    # be read from a different project than the one people sign in to.
    try:
        web_project = json.loads(app.config.get("FIREBASE_CLIENT_CONFIG") or "{}").get("projectId")
    except ValueError:
        web_project = None
    if web_project and certificate.project_id != web_project:
        raise RuntimeError(
            f"HostelHub configuration error: the service account belongs to Firebase project "
            f"'{certificate.project_id}' but the web config (FIREBASE_CLIENT_CONFIG) is for "
            f"'{web_project}'. Both must come from the same project."
        )

    if certificate.project_id != FIREBASE_PROJECT["projectId"]:
        # Allowed (for example for a test project), but never by accident.
        app.logger.warning("HostelHub is using Firebase project '%s', not the HostelHub project '%s'. "
                           "Check FIREBASE_SERVICE_ACCOUNT and FIREBASE_CLIENT_CONFIG.",
                           certificate.project_id, FIREBASE_PROJECT["projectId"])

    try:
        firebase_admin.initialize_app(certificate)
        return True
    except ValueError as error:
        raise RuntimeError(
            "HostelHub could not start Firebase. Set FIREBASE_SERVICE_ACCOUNT (the service-account "
            "JSON) or FIREBASE_CREDENTIALS_FILE (its path). "
            f"Reason: {type(error).__name__}: {error}"
        ) from error


def firebase_client_config():
    """The PUBLIC Firebase web config for the login page, or None if it is unusable.

    (This config is designed to be public; the secret service account never leaves the server.)
    """
    import firebase_admin

    raw = current_app.config.get("FIREBASE_CLIENT_CONFIG", "")
    if not raw or not firebase_admin._apps:
        return None      # no sign-in form if the server could not verify tokens anyway
    try:
        config = json.loads(raw)
    except ValueError:
        current_app.logger.error("FIREBASE_CLIENT_CONFIG is not valid JSON.")
        return None
    needed = ("apiKey", "authDomain", "projectId", "appId")
    return {key: config[key] for key in needed} if all(config.get(key) for key in needed) else None


def login_user(user):
    """Start a session for this user.

    We store ONLY the user id. The role is always read again from Firestore, so
    nobody can become a warden by editing the browser.
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

    user = get_store().get("users", user_id)
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

@auth_bp.route("/login")
def login():
    """The login page. Signing in happens in the browser with Firebase, then the
    ID token is posted to /firebase-login below."""
    if g.user is not None:
        return redirect(home_url_for(g.user["role"]))
    return render_template("auth/login.html", firebase_config=firebase_client_config())


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/firebase-login", methods=["POST"])
def firebase_login():
    """Called by the login page after Firebase signed the user in.

    Like every POST it must carry our CSRF token (checked in check_csrf_token()).
    It answers with JSON because JavaScript, not a normal form, reads the reply.
    """
    try:
        user = authenticate_firebase(request.form.get("idToken", ""))
    except LoginError as error:
        return jsonify({"error": error.message}), error.status

    login_user(user)
    first_name = user["name"].replace("Dr. ", "").split()[0]
    flash(f"Welcome back, {first_name}!", "success")
    return jsonify({"success": True, "redirect": home_url_for(user["role"])})
