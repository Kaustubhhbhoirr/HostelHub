"""
helpers.py — Small reusable functions shared by several route files.

Contents:
  1. Notifications      -> create_notification(), notify_wardens()
  2. Bed allocation     -> get_active_allocation(), allocate_bed(), vacate_bed(), move_student()
  3. Room-change utils  -> release_reserved_bed(), cancel_pending_room_requests()
  4. Image uploads      -> save_complaint_image(), complaint_image_exists(), delete_complaint_image()

IMPORTANT: none of these functions call commit(). The route that uses them
commits once at the end. If anything fails, the route calls rollback() and
the database goes back to how it was before (a "transaction").
"""

import uuid

import storage
from config import ALLOWED_IMAGE_EXTENSIONS
from database import execute, now_str, query_all, query_one


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
    execute(
        """INSERT INTO notifications (user_id, title, message, type, link, is_read, created_at)
           VALUES (?, ?, ?, ?, ?, 0, ?)""",
        (user_id, title, message, notif_type, link, now_str()),
    )


def notify_wardens(title, message, notif_type="system", link=None):
    """Send the same notification to every active warden."""
    wardens = query_all("SELECT id FROM users WHERE role = 'warden' AND is_active = 1")
    for warden in wardens:
        create_notification(warden["id"], title, message, notif_type, link)


# ------------------------------------------------------------------
# 2. Bed allocation
# ------------------------------------------------------------------

def get_active_allocation(student_id):
    """Return the student's current bed + room details, or None if they have no bed."""
    return query_one(
        """SELECT allocations.id AS allocation_id, allocations.allocated_at,
                  beds.id AS bed_id, beds.bed_number,
                  rooms.id AS room_id, rooms.block, rooms.floor, rooms.room_number,
                  rooms.capacity, rooms.status AS room_status
           FROM allocations
           JOIN beds  ON beds.id  = allocations.bed_id
           JOIN rooms ON rooms.id = beds.room_id
           WHERE allocations.student_id = ? AND allocations.status = 'active'""",
        (student_id,),
    )


def get_bed(bed_id):
    """Return a bed together with its room details, or None."""
    return query_one(
        """SELECT beds.id, beds.bed_number, beds.status, rooms.id AS room_id, rooms.block,
                  rooms.floor, rooms.room_number, rooms.status AS room_status
           FROM beds JOIN rooms ON rooms.id = beds.room_id
           WHERE beds.id = ?""",
        (bed_id,),
    )


def bed_label(bed):
    """Human readable bed name, e.g. 'A-201 · Bed 2'."""
    return f"{bed['block']}-{bed['room_number']} · Bed {bed['bed_number']}"


def allocate_bed(student_id, bed_id, allow_reserved=False):
    """Give a free bed to a student who currently has no bed.

    allow_reserved=True is used only when approving a room-change request,
    because the requested bed was reserved for that same student.
    """
    student = query_one("SELECT id, role, is_active FROM users WHERE id = ?", (student_id,))
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

    # Two related changes: a new allocation row AND the bed becomes occupied.
    execute(
        "INSERT INTO allocations (student_id, bed_id, allocated_at, status) VALUES (?, ?, ?, 'active')",
        (student_id, bed_id, now_str()),
    )
    execute("UPDATE beds SET status = 'occupied' WHERE id = ?", (bed_id,))
    return bed


def vacate_bed(student_id):
    """End the student's active allocation and make their bed available again."""
    allocation = get_active_allocation(student_id)
    if allocation is None:
        raise InvalidAllocationError("This student does not have a bed to vacate.")

    execute("UPDATE allocations SET status = 'ended', ended_at = ? WHERE id = ?",
            (now_str(), allocation["allocation_id"]))
    execute("UPDATE beds SET status = 'available' WHERE id = ?", (allocation["bed_id"],))
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
    if bed_id:
        execute("UPDATE beds SET status = 'available' WHERE id = ? AND status = 'reserved'", (bed_id,))


def cancel_pending_room_requests(student_id, remark):
    """Cancel a student's pending room-change requests and free their reserved beds.

    Used when the warden vacates, deactivates or deletes the student,
    because the pending request no longer makes sense.
    """
    pending = query_all(
        "SELECT id, requested_bed_id FROM room_change_requests WHERE student_id = ? AND status = 'Pending'",
        (student_id,),
    )
    for request_row in pending:
        release_reserved_bed(request_row["requested_bed_id"])
        execute(
            "UPDATE room_change_requests SET status = 'Cancelled', warden_remarks = ?, updated_at = ? WHERE id = ?",
            (remark, now_str(), request_row["id"]),
        )
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


def save_complaint_image(file):
    """Save an uploaded complaint image and return the generated file name.

    Returns None when no file was chosen (the image is optional).
    Raises ValueError with a friendly message when the file is not allowed.
    """
    if file is None or file.filename == "":
        return None

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

    # storage.py decides WHERE it goes: the local uploads folder, or the private
    # Supabase bucket in deployment.
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
