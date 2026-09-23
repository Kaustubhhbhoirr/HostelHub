"""
app.py — Starting point of HostelHub.

Run with:   python app.py
Then open:  http://localhost:5000

This file:
  1. creates the Flask app and loads settings from config.py
  2. starts Firebase (Authentication and Firestore) and refuses to
     start without it, because Firebase is the only database
  3. registers the route files (blueprints)
  4. adds template helpers (date formatting, notification counts)
  5. registers friendly error pages
"""

from datetime import datetime
from dotenv import load_dotenv

# Read settings from a local .env file if there is one (git-ignored, never committed).
# Variables already set in the terminal or hosting dashboard take priority.
load_dotenv()

from flask import Flask, g, redirect, render_template, url_for

import data
from config import Config, check_production_settings
from data import local_now
from data.queries import dashboard_data, notifications_for, unread_notification_count
from routes.account import account_bp
from routes.auth import auth_bp, home_url_for, init_firebase
from routes.college import college_bp
from routes.complaints import complaints_bp
from routes.requests import requests_bp
from routes.rooms import rooms_bp
from routes.student import student_bp
from routes.warden import warden_bp

# Static files (CSS, JavaScript, logo) live in public/static/.
# - On Vercel, everything inside public/ is served directly by Vercel's CDN,
#   so /static/css/style.css never has to go through Python.
# - Locally, Flask serves the same folder at the same /static/... URLs.
app = Flask(__name__, static_folder="public/static", static_url_path="/static")
app.config.from_object(Config)

# Stop immediately with a clear message instead of running with missing settings
# (for example without Firebase credentials, or with the public development secret key).
config_problems = check_production_settings(app.config)
if config_problems:
    raise RuntimeError("HostelHub configuration error: " + " ".join(config_problems))
data.init_app(app)

# Firebase is Authentication and Firestore. Without it there
# is nothing to read or write, so this raises instead of falling back to anything.
init_firebase(app)

# Each blueprint is a group of related routes kept in its own file.
for blueprint in (auth_bp, account_bp, student_bp, warden_bp, rooms_bp,
                  complaints_bp, requests_bp, college_bp):
    app.register_blueprint(blueprint)


# ------------------------------------------------------------------
# Template filters: {{ value | filter_name }} inside Jinja templates
# ------------------------------------------------------------------

def parse_datetime(value):
    """Convert our stored text 'YYYY-MM-DD HH:MM:SS' into a datetime object."""
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


@app.template_filter("datetime")
def format_datetime(value):
    moment = parse_datetime(value)
    return moment.strftime("%d %b %Y, %I:%M %p") if moment else "—"


@app.template_filter("date")
def format_date(value):
    moment = parse_datetime(value)
    return moment.strftime("%d %b %Y") if moment else "—"


@app.template_filter("timeago")
def time_ago(value):
    """'5 minutes ago', '3 days ago' ... easier to read on dashboards."""
    moment = parse_datetime(value)
    if moment is None:
        return "—"
    seconds = int((local_now() - moment).total_seconds())
    if seconds < 60:
        return "just now"
    # (limit in seconds, unit size in seconds, unit name)
    for limit, size, unit in ((3600, 60, "minute"), (86400, 3600, "hour"), (2592000, 86400, "day")):
        if seconds < limit:
            amount = seconds // size
            return f"{amount} {unit}{'s' if amount != 1 else ''} ago"
    return format_date(value)


@app.template_filter("slug")
def slugify(value):
    """'In Progress' -> 'in-progress' (used to pick a CSS badge colour)."""
    return str(value).strip().lower().replace(" ", "-").replace("/", "-")


@app.template_filter("initials")
def initials(name):
    """'Aarav Sharma' -> 'AS' for the avatar circle."""
    words = [word for word in str(name).replace(".", " ").split() if word.lower() not in ("dr", "mr", "ms", "mrs")]
    return "".join(word[0] for word in words[:2]).upper()


@app.context_processor
def inject_layout_data():
    """Values available in EVERY template (sidebar badges and bell icon)."""
    if g.get("user") is None:
        return {}

    user_id = g.user["id"]
    unread = unread_notification_count(user_id)
    latest = notifications_for(user_id, limit=5)
    nav_counts = {}
    if g.user["role"] == "warden":
        totals = dashboard_data()
        nav_counts["complaints"] = totals["pending_complaints"]
        nav_counts["requests"] = totals["pending_requests"]
    return {"unread_count": unread, "latest_notifications": latest, "nav_counts": nav_counts}


# ------------------------------------------------------------------
# Home page and error pages
# ------------------------------------------------------------------

@app.route("/")
def index():
    if g.user is None:
        return redirect(url_for("auth.login"))
    return redirect(home_url_for(g.user["role"]))


ERROR_MESSAGES = {
    400: ("Form expired", "This form could not be verified. Go back, refresh the page and try again."),
    403: ("Access denied", "You do not have permission to open this page."),
    404: ("Page not found", "The page or record you are looking for does not exist."),
    413: ("File too large", "Uploaded files must be smaller than 4 MB."),
    500: ("Something went wrong", "An unexpected error occurred. Please try again."),
}


def render_error(code):
    title, message = ERROR_MESSAGES[code]
    return render_template("errors/error.html", code=code, title=title, message=message), code


app.register_error_handler(400, lambda error: render_error(400))
app.register_error_handler(403, lambda error: render_error(403))
app.register_error_handler(404, lambda error: render_error(404))
app.register_error_handler(413, lambda error: render_error(413))
app.register_error_handler(500, lambda error: render_error(500))


if __name__ == "__main__":
    # Debug mode shows code and stack traces in the browser, so it is OFF unless
    # you ask for it locally with HOSTELHUB_DEBUG=1 (see config.py).
    app.run(debug=app.config["DEBUG"])
