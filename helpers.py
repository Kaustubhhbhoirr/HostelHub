"""
helpers.py — Small reusable functions shared by several route files.

Contents:
  1. Notifications      -> create_notification(), notify_wardens()
  2. Bed allocation     -> get_active_allocation(), allocate_bed(), vacate_bed(), move_student()
  3. Room-change utils  -> release_reserved_bed(), cancel_pending_room_requests()
  4. Image uploads      -> save_complaint_image(), complaint_image_exists(), delete_complaint_image()

IMPORTANT: none of these functions call commit(). The route that uses them
commits once at the end. If anything fails, the route calls rollback() and the
database goes back to how it was before (a "transaction").

The two allocation rules ("one active allocation per bed" and "one per student")
are checked here in Python AND enforced by Firestore itself: the store keeps a
small guard document per rule, which cannot be created twice, so two wardens
allocating the same bed at the same moment cannot both succeed.
"""

import uuid

import storage
from config import ALLOWED_IMAGE_EXTENSIONS
from data import get_store, now_str
from data.queries import get_active_allocation, get_bed, occupant_of_bed


class InvalidAllocationError(Exception):
    """Raised when an allocation would break a hostel rule.

    Example: trying to give a student a bed that is already occupied.
    The message is safe to show to the user with flash().
    """


# ------------------------------------------------------------------
# 1. Notifications
# ------------------------------------------------------------------

def create_notification(user_id, title, message, notif_type="system", link=None):
    """Store one notification for one user."""
    get_store().insert("notifications", {
        "user_id": user_id, "title": title, "message": message, "type": notif_type,
        "link": link, "is_read": 0, "created_at": now_str(),
    })


def notify_wardens(title, message, notif_type="system", link=None):
    """Send the same notification to every active warden."""
    for warden in get_store().find("users", role="warden", is_active=1):
        create_notification(warden["id"], title, message, notif_type, link)


# ------------------------------------------------------------------
# 2. Bed allocation
# ------------------------------------------------------------------

def bed_label(bed):
    """Human readable bed name, e.g. 'A-201 · Bed 2'."""
    return f"{bed['block']}-{bed['room_number']} · Bed {bed['bed_number']}"


def allocate_bed(student_id, bed_id, allow_reserved=False):
    """Give a free bed to a student who currently has no bed.

    allow_reserved=True is used only when approving a room-change request,
    because the requested bed was reserved for that same student.
    """
    store = get_store()
    student = store.get("users", student_id)
    if student is None or student["role"] != "student":
        raise InvalidAllocationError("Student not found.")
    if not student["is_active"]:
        raise InvalidAllocationError("A deactivated student cannot be given a bed.")

    bed = get_bed(bed_id)
    if bed is None:
        raise InvalidAllocationError("The selected bed does not exist.")
    if bed["room_status"] != "active":
        raise InvalidAllocationError(f"Room {bed['block']}-{bed['room_number']} is not open for allocation.")

    allowed_statuses = {"available", "reserved"} if allow_reserved else {"available"}
    if bed["status"] not in allowed_statuses:
        raise InvalidAllocationError(f"{bed_label(bed)} is not available (status: {bed['status']}).")

    if get_active_allocation(student_id) is not None:
        raise InvalidAllocationError("This student already has a bed. Vacate it first or use a room change.")

    # Take both "only one" slots. If another request took one a moment ago, the
    # store raises DuplicateError and the route rolls everything back.
    store.claim(f"bed-{bed_id}")
    store.claim(f"student-{student_id}")

    # Two related changes: a new allocation row AND the bed becomes occupied.
    store.insert("allocations", {"student_id": student_id, "bed_id": bed_id,
                                 "allocated_at": now_str(), "ended_at": None, "status": "active"})
    store.update("beds", bed_id, {"status": "occupied"})
    return bed


def vacate_bed(student_id):
    """End the student's active allocation and make their bed available again."""
    store = get_store()
    allocation = get_active_allocation(student_id)
    if allocation is None:
        raise InvalidAllocationError("This student does not have a bed to vacate.")

    store.update("allocations", allocation["allocation_id"], {"status": "ended", "ended_at": now_str()})
    store.update("beds", allocation["bed_id"], {"status": "available"})
    store.release(f"bed-{allocation['bed_id']}")
    store.release(f"student-{student_id}")
    return allocation


def move_student(student_id, new_bed_id, allow_reserved=False):
    """Move a student from their current bed to a new bed.

    OLD bed: occupied -> available,   old allocation -> ended
    NEW bed: available -> occupied,   new allocation -> active
    """
    current = get_active_allocation(student_id)
    if current is not None and current["bed_id"] == new_bed_id:
        raise InvalidAllocationError("The student is already in this bed.")
    if current is not None:
        vacate_bed(student_id)
    return allocate_bed(student_id, new_bed_id, allow_reserved)


# ------------------------------------------------------------------
# 3. Room-change helpers
# ------------------------------------------------------------------

def release_reserved_bed(bed_id):
    """A reserved bed goes back to available (only if it is still reserved)."""
    if not bed_id:
        return
    store = get_store()
    bed = store.get("beds", bed_id)
    if bed and bed["status"] == "reserved":
        store.update("beds", bed_id, {"status": "available"})


def cancel_pending_room_requests(student_id, remark):
    """Cancel a student's pending room-change requests and free their reserved beds.

    Used when the warden vacates, deactivates or deletes the student,
    because the pending request no longer makes sense.
    """
    store = get_store()
    pending = store.find("room_change_requests", student_id=student_id, status="Pending")
    for request_row in pending:
        release_reserved_bed(request_row.get("requested_bed_id"))
        store.update("room_change_requests", request_row["id"],
                     {"status": "Cancelled", "warden_remarks": remark, "updated_at": now_str()})
    return len(pending)


# ------------------------------------------------------------------
# 4. Image uploads
# ------------------------------------------------------------------

def is_allowed_image(filename):
    """True if the file name ends with an allowed image extension."""
    if "." not in filename:
        return False
    extension = filename.rsplit(".", 1)[1].lower()
    return extension in ALLOWED_IMAGE_EXTENSIONS


# The first bytes ("magic numbers") of real image files. A renamed .exe or .html
# file does not start with any of these, even if its name ends in .png.
IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",   # PNG
    b"\xff\xd8\xff",          # JPEG
    b"GIF87a", b"GIF89a",      # GIF
)


def has_image_signature(file):
    """Peek at the start of the uploaded file to check it really is an image."""
    header = file.stream.read(12)
    file.stream.seek(0)   # rewind so the whole file is read again when it is stored
    is_webp = header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    return is_webp or header.startswith(IMAGE_SIGNATURES)


class PhotosDisabled(ValueError):
    """A photo was sent, but this server does not store photos (COMPLAINT_IMAGE_STORAGE=none)."""


def save_complaint_image(file):
    """Save an uploaded complaint image and return the generated file name.

    Returns None when no file was chosen (the image is optional).
    Raises ValueError with a friendly message when the file is not allowed.
    """
    if file is None or file.filename == "":
        return None
    if not storage.images_enabled():
        raise PhotosDisabled("Photo uploads are not available on this server.")

    # Three checks: the file name extension, the type reported by the browser,
    # and the real content of the file.
    if (not is_allowed_image(file.filename)
            or not (file.mimetype or "").startswith("image/")
            or not has_image_signature(file)):
        raise ValueError("Only PNG, JPG, JPEG, WEBP or GIF images can be uploaded.")

    # We never use the user's own file name. A random name avoids overwriting
    # other photos and blocks tricks like "../../app.py".
    extension = file.filename.rsplit(".", 1)[1].lower()
    new_filename = f"{uuid.uuid4().hex}.{extension}"

    # storage.py decides WHERE it goes (the local uploads folder, or nowhere when
    # photo uploads are switched off on this server).
    try:
        storage.save_file(new_filename, file.read(), file.mimetype)
    except storage.StorageError:
        raise ValueError("The image could not be saved. Please try again.")

    # Only this short file name goes into the database, never the image bytes.
    return new_filename


def complaint_image_exists(filename):
    """True if the complaint has a photo that can be shown."""
    return storage.file_exists(filename)


def delete_complaint_image(filename):
    """Remove a saved photo (used when the complaint itself could not be saved)."""
    if filename:
        storage.delete_file(filename)
