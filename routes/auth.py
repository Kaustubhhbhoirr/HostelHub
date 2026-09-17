"""
routes/auth.py — Login, logout and page protection.

The login mechanism is kept in two small functions:

    authenticate_local(email, password)  -> checks email + password
    login_user(user)                     -> stores the user id in the session

When Firebase Google login is added later, only authenticate_local() needs
to be replaced (verify the Google token, then find the user by email).
Everything else — roles, sessions, protected pages — stays the same.
"""

import hmac
import secrets
from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from database import query_one

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

    return render_template("auth/login.html", email=email)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
