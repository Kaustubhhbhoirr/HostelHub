"""
routes/rooms.py — Room management (CRUD) and the visual bed allocation map.

Rooms:  room_list, room_new, room_edit, room_delete
Map:    bed_map (the RedBus-style visual layout)
Beds:   bed_allocate, bed_vacate, bed_status

Every colour on the map comes from beds.status in the database.
Nothing about occupancy is hard-coded in HTML or JavaScript.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from config import MANUAL_BED_STATUSES, ROOM_STATUSES
from database import DatabaseError, IntegrityError, execute, get_db, query_all, query_one
from helpers import (InvalidAllocationError, allocate_bed, bed_label, cancel_pending_room_requests,
                     create_notification, get_bed, vacate_bed)
from routes.auth import warden_required

rooms_bp = Blueprint("rooms", __name__, url_prefix="/warden/rooms")


def get_room_or_404(room_id):
    room = query_one("SELECT * FROM rooms WHERE id = ?", (room_id,))
    if room is None:
        abort(404)
    return room


# ------------------------------------------------------------------
# Rooms — READ
# ------------------------------------------------------------------

@rooms_bp.route("/")
@warden_required
def room_list():
    search = request.args.get("q", "").strip()
    block = request.args.get("block", "")
    status = request.args.get("status", "")

    conditions, params = [], []
    if search:
        # Matches "204", "A-204" or "a204": remove the dash/spaces and compare block+number.
        conditions.append("UPPER(rooms.block || rooms.room_number) LIKE ?")
        params.append(f"%{search.upper().replace('-', '').replace(' ', '')}%")
    if block:
        conditions.append("rooms.block = ?")
        params.append(block)
    if status in ROOM_STATUSES:
        conditions.append("rooms.status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rooms = query_all(
        f"""SELECT rooms.*,
                   COUNT(beds.id) AS bed_count,
                   SUM(CASE WHEN beds.status = 'occupied' THEN 1 ELSE 0 END) AS occupied,
                   SUM(CASE WHEN beds.status = 'available' THEN 1 ELSE 0 END) AS available
            FROM rooms LEFT JOIN beds ON beds.room_id = rooms.id
            {where}
            GROUP BY rooms.id
            ORDER BY rooms.block, rooms.floor, rooms.room_number""",
        tuple(params),
    )
    blocks = [row["block"] for row in query_all("SELECT DISTINCT block FROM rooms ORDER BY block")]
    return render_template("rooms/list.html", rooms=rooms, blocks=blocks, block=block, search=search,
                           status=status, room_statuses=ROOM_STATUSES)


# ------------------------------------------------------------------
# Rooms — CREATE / UPDATE
# ------------------------------------------------------------------

def read_room_form():
    """Read + validate the room form. Returns (data, errors)."""
    data = {
        "block": request.form.get("block", "").strip().upper(),
        "floor": request.form.get("floor", "").strip(),
        "room_number": request.form.get("room_number", "").strip(),
        "capacity": request.form.get("capacity", "").strip(),
        "status": request.form.get("status", "active"),
    }
    errors = []
    if not (data["block"].isalpha() and 1 <= len(data["block"]) <= 2):
        errors.append("Block must be one or two letters, e.g. A.")
    if not data["floor"].isdigit() or int(data["floor"]) > 20:
        errors.append("Floor must be a number between 0 and 20.")
    if not data["room_number"].isalnum():
        errors.append("Room number can contain only letters and digits, e.g. 204.")
    if not data["capacity"].isdigit() or not 1 <= int(data["capacity"]) <= 6:
        errors.append("Capacity must be between 1 and 6 beds.")
    if data["status"] not in ROOM_STATUSES:
        errors.append("Invalid room status.")
    return data, errors


@rooms_bp.route("/new", methods=["GET", "POST"])
@rooms_bp.route("/<int:room_id>/edit", methods=["GET", "POST"], endpoint="room_edit")
@warden_required
def room_new(room_id=None):
    """One view handles both 'add room' (no room_id) and 'edit room'."""
    room = get_room_or_404(room_id) if room_id else None
    values = dict(room) if room else {"status": "active", "capacity": 4}

    if request.method == "POST":
        data, errors = read_room_form()
        values.update(data)
        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template("rooms/form.html", room=room, values=values, room_statuses=ROOM_STATUSES)

        db = get_db()
        try:
            if room is None:
                create_room(data)
                flash(f"Room {data['block']}-{data['room_number']} created with {data['capacity']} beds.", "success")
            else:
                update_room(room, data)
                flash(f"Room {data['block']}-{data['room_number']} updated.", "success")
            db.commit()
            return redirect(url_for("rooms.room_list"))
        except ValueError as error:          # a business rule was broken (see update_room)
            db.rollback()
            flash(str(error), "danger")
        except IntegrityError:        # UNIQUE (block, room_number)
            db.rollback()
            flash(f"Room {data['block']}-{data['room_number']} already exists.", "danger")

    return render_template("rooms/form.html", room=room, values=values, room_statuses=ROOM_STATUSES)


def bed_status_for_room(room_status):
    """New beds in a closed room are unavailable; otherwise they start available."""
    return {"active": "available", "maintenance": "maintenance"}.get(room_status, "unavailable")


def create_room(data):
    room_id = execute(
        "INSERT INTO rooms (block, floor, room_number, capacity, status) VALUES (?, ?, ?, ?, ?)",
        (data["block"], int(data["floor"]), data["room_number"], int(data["capacity"]), data["status"]),
    )
    # A room with capacity 4 gets beds numbered 1, 2, 3, 4.
    for bed_number in range(1, int(data["capacity"]) + 1):
        execute("INSERT INTO beds (room_id, bed_number, status) VALUES (?, ?, ?)",
                (room_id, bed_number, bed_status_for_room(data["status"])))


def update_room(room, data):
    new_capacity = int(data["capacity"])
    beds = query_all("SELECT * FROM beds WHERE room_id = ? ORDER BY bed_number", (room["id"],))
    busy_beds = [bed for bed in beds if bed["status"] in ("occupied", "reserved")]

    # Closing a room or putting it under maintenance is only allowed when nobody lives there.
    if data["status"] != "active" and busy_beds:
        raise ValueError("Move the students out first: this room still has occupied or reserved beds.")

    execute("UPDATE rooms SET block = ?, floor = ?, room_number = ?, capacity = ?, status = ? WHERE id = ?",
            (data["block"], int(data["floor"]), data["room_number"], new_capacity, data["status"], room["id"]))

    # --- Capacity change ---
    if new_capacity > len(beds):
        for bed_number in range(len(beds) + 1, new_capacity + 1):
            execute("INSERT INTO beds (room_id, bed_number, status) VALUES (?, ?, ?)",
                    (room["id"], bed_number, bed_status_for_room(data["status"])))
    elif new_capacity < len(beds):
        # Remove beds from the highest number down; they must be free.
        beds_to_remove = beds[new_capacity:]
        if any(bed["status"] in ("occupied", "reserved") for bed in beds_to_remove):
            raise ValueError(f"Cannot reduce capacity to {new_capacity}: a bed that would be removed is in use.")
        for bed in beds_to_remove:
            try:
                execute("DELETE FROM beds WHERE id = ?", (bed["id"],))
            except IntegrityError:
                # Old allocations or requests still point to this bed (foreign key), so it
                # cannot be removed. The route rolls back the whole edit.
                raise ValueError(f"Bed {bed['bed_number']} has allocation history and cannot be removed. "
                                 f"Mark it unavailable on the map instead of reducing capacity.")

    # --- Status change: keep the free beds in step with the room ---
    if data["status"] != room["status"]:
        new_bed_status = bed_status_for_room(data["status"])
        execute(
            """UPDATE beds SET status = ?
               WHERE room_id = ? AND bed_number <= ? AND status IN ('available', 'maintenance', 'unavailable')""",
            (new_bed_status, room["id"], new_capacity),
        )


# ------------------------------------------------------------------
# Rooms — DELETE
# ------------------------------------------------------------------

@rooms_bp.route("/<int:room_id>/delete", methods=["POST"])
@warden_required
def room_delete(room_id):
    room = get_room_or_404(room_id)
    label = f"{room['block']}-{room['room_number']}"
    db = get_db()
    try:
        # Beds are deleted automatically with the room (ON DELETE CASCADE).
        execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        db.commit()
        flash(f"Room {label} deleted.", "success")
    except IntegrityError:
        # Allocations or complaints still reference this room's beds.
        db.rollback()
        flash(f"Room {label} has allocation or complaint history, so it cannot be deleted. "
              f"Set its status to inactive instead.", "warning")
    return redirect(url_for("rooms.room_list"))


# ------------------------------------------------------------------
# Visual allocation map
# ------------------------------------------------------------------

@rooms_bp.route("/map")
@warden_required
def bed_map():
    blocks = [row["block"] for row in query_all("SELECT DISTINCT block FROM rooms ORDER BY block")]
    if not blocks:
        return render_template("rooms/map.html", blocks=[], rooms=[])

    block = request.args.get("block", blocks[0])
    if block not in blocks:
        block = blocks[0]
    floors = [row["floor"] for row in query_all(
        "SELECT DISTINCT floor FROM rooms WHERE block = ? ORDER BY floor", (block,))]
    floor = request.args.get("floor", type=int)
    if floor not in floors:
        floor = floors[0]

    room_rows = query_all(
        "SELECT * FROM rooms WHERE block = ? AND floor = ? ORDER BY room_number", (block, floor))
    # One query for all beds on this floor, including the occupant (if any).
    bed_rows = query_all(
        """SELECT beds.*, users.id AS student_id, users.name AS student_name,
                  users.student_id AS roll_number, users.department, allocations.allocated_at,
                  req_user.name AS reserved_for
           FROM beds
           JOIN rooms ON rooms.id = beds.room_id
           LEFT JOIN allocations ON allocations.bed_id = beds.id AND allocations.status = 'active'
           LEFT JOIN users ON users.id = allocations.student_id
           LEFT JOIN room_change_requests AS rcr ON rcr.requested_bed_id = beds.id AND rcr.status = 'Pending'
           LEFT JOIN users AS req_user ON req_user.id = rcr.student_id
           WHERE rooms.block = ? AND rooms.floor = ?
           ORDER BY beds.bed_number""",
        (block, floor),
    )

    # Group beds under their room: {room_id: [bed, bed, ...]}
    beds_by_room = {}
    for bed in bed_rows:
        beds_by_room.setdefault(bed["room_id"], []).append(bed)
    rooms = [{"room": room, "beds": beds_by_room.get(room["id"], [])} for room in room_rows]

    # Status totals for the summary chips on this floor.
    floor_counts = {}
    for bed in bed_rows:
        floor_counts[bed["status"]] = floor_counts.get(bed["status"], 0) + 1

    # Active students without a bed (for the "allocate" dropdown in the modal).
    unallocated_students = query_all(
        """SELECT id, name, student_id, department, year_of_study FROM users
           WHERE role = 'student' AND is_active = 1
             AND id NOT IN (SELECT student_id FROM allocations WHERE status = 'active')
           ORDER BY name"""
    )
    selected_student = request.args.get("student", type=int)
    selected_student_row = next((s for s in unallocated_students if s["id"] == selected_student), None)

    return render_template(
        "rooms/map.html", blocks=blocks, block=block, floors=floors, floor=floor,
        rooms=rooms, floor_counts=floor_counts, unallocated_students=unallocated_students,
        selected_student=selected_student_row, manual_bed_statuses=MANUAL_BED_STATUSES,
    )


def back_to_map(bed):
    """After a bed action, reopen the map on the same block and floor."""
    return redirect(url_for("rooms.bed_map", block=bed["block"], floor=bed["floor"]))


# ------------------------------------------------------------------
# Bed actions (forms inside the map's modal)
# ------------------------------------------------------------------

@rooms_bp.route("/beds/<int:bed_id>/allocate", methods=["POST"])
@warden_required
def bed_allocate(bed_id):
    bed = get_bed(bed_id)
    if bed is None:
        abort(404)
    student_id = request.form.get("student_id", type=int)
    if not student_id:
        flash("Please choose a student to allocate.", "danger")
        return back_to_map(bed)

    db = get_db()
    try:
        allocate_bed(student_id, bed_id)
        create_notification(
            student_id, "Room allocated",
            f"You have been allocated {bed_label(bed)} (Block {bed['block']}, Floor {bed['floor']}).",
            "allocation", url_for("student.my_room"),
        )
        db.commit()
        student = query_one("SELECT name FROM users WHERE id = ?", (student_id,))
        flash(f"{student['name']} allocated to {bed_label(bed)}.", "success")
    except InvalidAllocationError as error:
        db.rollback()
        flash(str(error), "danger")
    except IntegrityError:
        # The partial UNIQUE index stopped a double allocation.
        db.rollback()
        flash("That bed or student was just allocated by someone else. Please refresh and try again.", "danger")
    return back_to_map(bed)


@rooms_bp.route("/beds/<int:bed_id>/vacate", methods=["POST"])
@warden_required
def bed_vacate(bed_id):
    bed = get_bed(bed_id)
    if bed is None:
        abort(404)
    occupant = query_one(
        """SELECT users.id, users.name FROM allocations JOIN users ON users.id = allocations.student_id
           WHERE allocations.bed_id = ? AND allocations.status = 'active'""",
        (bed_id,),
    )
    if occupant is None:
        flash("This bed is not occupied.", "warning")
        return back_to_map(bed)

    db = get_db()
    try:
        vacate_bed(occupant["id"])
        cancel_pending_room_requests(occupant["id"], "Cancelled because the student's bed was vacated.")
        create_notification(occupant["id"], "Bed vacated",
                            f"Your allocation for {bed_label(bed)} has ended. Contact the hostel office for details.",
                            "allocation", url_for("student.my_room"))
        db.commit()
        flash(f"{occupant['name']} was removed from {bed_label(bed)}. The bed is now available.", "success")
    except (InvalidAllocationError, *DatabaseError):   # * unpacks the tuple of database errors
        db.rollback()
        flash("Could not vacate the bed. Please try again.", "danger")
    return back_to_map(bed)


@rooms_bp.route("/beds/<int:bed_id>/status", methods=["POST"])
@warden_required
def bed_status(bed_id):
    """Manually mark a free bed as available, under maintenance or unavailable."""
    bed = get_bed(bed_id)
    if bed is None:
        abort(404)
    new_status = request.form.get("status", "")

    if new_status not in MANUAL_BED_STATUSES:
        flash("Invalid bed status.", "danger")
    elif bed["status"] in ("occupied", "reserved"):
        flash("Occupied or reserved beds cannot be changed here. Vacate the bed or process the request first.",
              "warning")
    elif new_status == "available" and bed["room_status"] != "active":
        flash("Activate the room before making its beds available.", "warning")
    else:
        execute("UPDATE beds SET status = ? WHERE id = ?", (new_status, bed_id))
        get_db().commit()
        flash(f"{bed_label(bed)} marked as {new_status}.", "success")
    return back_to_map(bed)
