"""
routes/account.py — Pages every logged-in user has: notifications and profile.

Both students and wardens use these routes. Every query filters by
g.user["id"], so a user can only ever see or change their OWN records.
"""

import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import execute, get_db, query_all, query_one
from helpers import get_active_allocation
from routes.auth import login_required

account_bp = Blueprint("account", __name__)


# ------------------------------------------------------------------
# Notifications
# ------------------------------------------------------------------

@account_bp.route("/notifications")
@login_required
def notifications():
    show = request.args.get("show", "all")   # "all" or "unread"
    sql = "SELECT * FROM notifications WHERE user_id = ?"
    if show == "unread":
        sql += " AND is_read = 0"
    sql += " ORDER BY created_at DESC, id DESC"
    items = query_all(sql, (g.user["id"],))
    return render_template("account/notifications.html", notifications=items, show=show)


@account_bp.route("/notifications/<int:notification_id>/open")
@login_required
def open_notification(notification_id):
    """Mark one notification as read, then go to the page it points to."""
    item = query_one("SELECT * FROM notifications WHERE id = ? AND user_id = ?",
                     (notification_id, g.user["id"]))
    if item is None:
        abort(404)

    execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notification_id,))
    get_db().commit()

    # Only follow links that point inside HostelHub (they start with a single "/").
    link = item["link"] or ""
    if link.startswith("/") and not link.startswith("//"):
        return redirect(link)
    return redirect(url_for("account.notifications"))


@account_bp.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_read():
    execute("UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0", (g.user["id"],))
    get_db().commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("account.notifications"))


@account_bp.route("/notifications/clear-read", methods=["POST"])
@login_required
def clear_read():
    """DELETE example: remove notifications the user has already read."""
    execute("DELETE FROM notifications WHERE user_id = ? AND is_read = 1", (g.user["id"],))
    get_db().commit()
    flash("Read notifications cleared.", "success")
    return redirect(url_for("account.notifications"))


# ------------------------------------------------------------------
# Profile
# ------------------------------------------------------------------

@account_bp.route("/profile")
@login_required
def profile():
    allocation = None
    if g.user["role"] == "student":
        allocation = get_active_allocation(g.user["id"])
    return render_template("account/profile.html", allocation=allocation)


@account_bp.route("/profile/phone", methods=["POST"])
@login_required
def update_phone():
    phone = request.form.get("phone", "").strip()
    # A phone number must be exactly 10 digits (or empty to remove it).
    if phone and not (phone.isdigit() and len(phone) == 10):
        flash("Phone number must be exactly 10 digits.", "danger")
        return redirect(url_for("account.profile"))

    execute("UPDATE users SET phone = ? WHERE id = ?", (phone or None, g.user["id"]))
    get_db().commit()
    flash("Contact number updated.", "success")
    return redirect(url_for("account.profile"))


@account_bp.route("/profile/password", methods=["POST"])
@login_required
def change_password():
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    user = query_one("SELECT password_hash FROM users WHERE id = ?", (g.user["id"],))

    # Collect every problem, then show the first one.
    errors = []
    if not check_password_hash(user["password_hash"], current_password):
        errors.append("Your current password is incorrect.")
    if len(new_password) < 8:
        errors.append("The new password must be at least 8 characters long.")
    if new_password != confirm_password:
        errors.append("The new passwords do not match.")

    if errors:
        flash(errors[0], "danger")
        return redirect(url_for("account.profile"))

    try:
        execute("UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new_password), g.user["id"]))
        get_db().commit()
        flash("Password changed successfully.", "success")
    except sqlite3.Error:
        get_db().rollback()
        flash("Could not change the password. Please try again.", "danger")
    return redirect(url_for("account.profile"))
