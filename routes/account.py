"""
routes/account.py — Pages every logged-in user has: notifications and profile.

Both students and wardens use these routes. Every query filters by
g.user["id"], so a user can only ever see or change their OWN records.
"""

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from data import get_store
from data.queries import get_active_allocation, notifications_for
from routes.auth import firebase_client_config, login_required

account_bp = Blueprint("account", __name__)


# ------------------------------------------------------------------
# Notifications
# ------------------------------------------------------------------

@account_bp.route("/notifications")
@login_required
def notifications():
    show = request.args.get("show", "all")   # "all" or "unread"
    items = notifications_for(g.user["id"], unread_only=(show == "unread"))
    return render_template("account/notifications.html", notifications=items, show=show)


@account_bp.route("/notifications/<int:notification_id>/open")
@login_required
def open_notification(notification_id):
    """Mark one notification as read, then go to the page it points to."""
    store = get_store()
    item = store.get("notifications", notification_id)
    if item is None or item["user_id"] != g.user["id"]:
        abort(404)

    store.update("notifications", notification_id, {"is_read": 1})
    store.commit()

    # Only follow links that point inside HostelHub (they start with a single "/").
    link = item["link"] or ""
    if link.startswith("/") and not link.startswith("//"):
        return redirect(link)
    return redirect(url_for("account.notifications"))


@account_bp.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_read():
    store = get_store()
    for item in store.find("notifications", user_id=g.user["id"], is_read=0):
        store.update("notifications", item["id"], {"is_read": 1})
    store.commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("account.notifications"))


@account_bp.route("/notifications/clear-read", methods=["POST"])
@login_required
def clear_read():
    """DELETE example: remove notifications the user has already read."""
    store = get_store()
    for item in store.find("notifications", user_id=g.user["id"], is_read=1):
        store.delete("notifications", item["id"])
    store.commit()
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
    # The password itself lives in Firebase Authentication, so the page changes it
    # in the browser with the Firebase SDK (see account/profile.html).
    return render_template("account/profile.html", allocation=allocation,
                           firebase_config=firebase_client_config())


@account_bp.route("/profile/phone", methods=["POST"])
@login_required
def update_phone():
    phone = request.form.get("phone", "").strip()
    # A phone number must be exactly 10 digits (or empty to remove it).
    if phone and not (phone.isdigit() and len(phone) == 10):
        flash("Phone number must be exactly 10 digits.", "danger")
        return redirect(url_for("account.profile"))

    store = get_store()
    store.update("users", g.user["id"], {"phone": phone or None})
    store.commit()
    flash("Contact number updated.", "success")
    return redirect(url_for("account.profile"))
