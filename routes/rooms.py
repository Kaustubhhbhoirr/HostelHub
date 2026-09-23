"""
routes/rooms.py — Room management (CRUD) and the visual bed allocation map.

Rooms:  room_list, room_new, room_edit, room_delete
Map:    bed_map (the visual bed layout)
Beds:   bed_allocate, bed_vacate, bed_status

Every colour on the map comes from beds.status in the database.
Nothing about occupancy is hard-coded in HTML or JavaScript.
"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from config import MANUAL_BED_STATUSES, ROOM_STATUSES
from data import DatabaseError, IntegrityError, get_store
from data.queries import (bed_map_rows, blocks as list_blocks, floors as list_floors, get_bed,
                          occupant_of_bed, room_list as list_rooms, rooms_on_floor, unallocated_students)
from helpers import (InvalidAllocationError, allocate_bed, bed_label, cancel_pending_room_requests,
                     create_notification, vacate_bed)
from routes.auth import warden_required

rooms_bp = Blueprint("rooms", __name__, url_prefix="/warden/rooms")


def get_room_or_404(room_id):
    room = get_store().get("rooms", room_id)
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
    if status not in ROOM_STATUSES:
        status = ""

    rooms = list_rooms(search=search, block=block, status=status)
    blocks = list_blocks()
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

        store = get_store()
        try:
            if room is None:
                create_room(data)
                flash(f"Room {data['block']}-{data['room_number']} created with {data['capacity']} beds.", "success")
            else:
                update_room(room, data)
                flash(f"Room {data['block']}-{data['room_number']} updated.", "success")
            store.commit()
            return redirect(url_for("rooms.room_list"))
        except ValueError as error:          # a business rule was broken (see update_room)
            store.rollback()
            flash(str(error), "danger")
        except IntegrityError:        # the block and room number are already taken
            store.rollback()
            flash(f"Room {data['block']}-{data['room_number']} already exists.", "danger")

    return render_template("rooms/form.html", room=room, values=values, room_statuses=ROOM_STATUSES)


def bed_status_for_room(room_status):
    """New beds in a closed room are unavailable; otherwise they start available."""
    return {"active": "available", "maintenance": "maintenance"}.get(room_status, "unavailable")


def create_room(data):
    store = get_store()
    if store.first("rooms", block=data["block"], room_number=data["room_number"]):
        raise IntegrityError[0](f"Room {data['block']}-{data['room_number']} already exists.")
    room_id = store.insert("rooms", {
        "block": data["block"], "floor": int(data["floor"]), "room_number": data["room_number"],
        "capacity": int(data["capacity"]), "status": data["status"],
    })
    # A room with capacity 4 gets beds numbered 1, 2, 3, 4.
    for bed_number in range(1, int(data["capacity"]) + 1):
        store.insert("beds", {"room_id": room_id, "bed_number": bed_number,
                              "status": bed_status_for_room(data["status"])})


def update_room(room, data):
    store = get_store()
    new_capacity = int(data["capacity"])
    beds = sorted(store.find("beds", room_id=room["id"]), key=lambda bed: bed["bed_number"])
    busy_beds = [bed for bed in beds if bed["status"] in ("occupied", "reserved")]

    # Closing a room or putting it under maintenance is only allowed when nobody lives there.
    if data["status"] != "active" and busy_beds:
        raise ValueError("Move the students out first: this room still has occupied or reserved beds.")

    clash = store.first("rooms", block=data["block"], room_number=data["room_number"])
    if clash and clash["id"] != room["id"]:
        raise IntegrityError[0](f"Room {data['block']}-{data['room_number']} already exists.")

    store.update("rooms", room["id"], {
        "block": data["block"], "floor": int(data["floor"]), "room_number": data["room_number"],
        "capacity": new_capacity, "status": data["status"],
    })

    # --- Capacity change ---
    if new_capacity > len(beds):
        for bed_number in range(len(beds) + 1, new_capacity + 1):
            store.insert("beds", {"room_id": room["id"], "bed_number": bed_number,
                                  "status": bed_status_for_room(data["status"])})
    elif new_capacity < len(beds):
        # Remove beds from the highest number down; they must be free and unused.
        beds_to_remove = beds[new_capacity:]
        if any(bed["status"] in ("occupied", "reserved") for bed in beds_to_remove):
            raise ValueError(f"Cannot reduce capacity to {new_capacity}: a bed that would be removed is in use.")
        for bed in beds_to_remove:
            if bed_has_history(store, bed["id"]):
                # Old allocations or requests still point to this bed, so it cannot be
                # removed. The route rolls back the whole edit.
                raise ValueError(f"Bed {bed['bed_number']} has allocation history and cannot be removed. "
                                 f"Mark it unavailable on the map instead of reducing capacity.")
            store.delete("beds", bed["id"])

    # --- Status change: keep the free beds in step with the room ---
    if data["status"] != room["status"]:
        new_bed_status = bed_status_for_room(data["status"])
        for bed in store.find("beds", room_id=room["id"]):
            if bed["bed_number"] <= new_capacity and bed["status"] in ("available", "maintenance", "unavailable"):
                store.update("beds", bed["id"], {"status": new_bed_status})


def bed_has_history(store, bed_id):
    """True if any allocation or room-change request still points at this bed."""
    if store.find("allocations", bed_id=bed_id):
        return True
    for field in ("current_bed_id", "requested_bed_id", "assigned_bed_id"):
        if store.find("room_change_requests", **{field: bed_id}):
            return True
    return False


def room_has_history(store, room_id):
    """True if complaints or bed history still reference this room."""
    if store.find("complaints", room_id=room_id):
        return True
    return any(bed_has_history(store, bed["id"]) for bed in store.find("beds", room_id=room_id))


# ------------------------------------------------------------------
# Rooms — DELETE
# ------------------------------------------------------------------

@rooms_bp.route("/<int:room_id>/delete", methods=["POST"])
@warden_required
def room_delete(room_id):
    room = get_room_or_404(room_id)
    label = f"{room['block']}-{room['room_number']}"
    store = get_store()
    try:
        if room_has_history(store, room_id):
            raise IntegrityError[0]("room has history")
        # The beds of the room go with it.
        for bed in store.find("beds", room_id=room_id):
            store.delete("beds", bed["id"])
        store.delete("rooms", room_id)
        store.commit()
        flash(f"Room {label} deleted.", "success")
    except IntegrityError:
        # Allocations or complaints still reference this room's beds.
        store.rollback()
        flash(f"Room {label} has allocation or complaint history, so it cannot be deleted. "
              f"Set its status to inactive instead.", "warning")
    return redirect(url_for("rooms.room_list"))


# ------------------------------------------------------------------
# Visual allocation map
# ------------------------------------------------------------------

@rooms_bp.route("/map")
@warden_required
def bed_map():
    blocks = list_blocks()
    if not blocks:
        return render_template("rooms/map.html", blocks=[], rooms=[])

    block = request.args.get("block", blocks[0])
    if block not in blocks:
        block = blocks[0]
    floors = list_floors(block)
    floor = request.args.get("floor", type=int)
    if floor not in floors:
        floor = floors[0]

    room_rows = rooms_on_floor(block, floor)
    # All beds on this floor, including the occupant and any student waiting for a bed.
    bed_rows = bed_map_rows(block, floor)

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
    waiting_students = unallocated_students()
    selected_student = request.args.get("student", type=int)
    selected_student_row = next((s for s in waiting_students if s["id"] == selected_student), None)

    return render_template(
        "rooms/map.html", blocks=blocks, block=block, floors=floors, floor=floor,
        rooms=rooms, floor_counts=floor_counts, unallocated_students=waiting_students,
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

    store = get_store()
    try:
        allocate_bed(student_id, bed_id)
        create_notification(
            student_id, "Room allocated",
            f"You have been allocated {bed_label(bed)} (Block {bed['block']}, Floor {bed['floor']}).",
            "allocation", url_for("student.my_room"),
        )
        store.commit()
        student = store.get("users", student_id)
        flash(f"{student['name']} allocated to {bed_label(bed)}.", "success")
    except InvalidAllocationError as error:
        store.rollback()
        flash(str(error), "danger")
    except IntegrityError:
        # The store's "only one allocation" rule stopped a double allocation.
        store.rollback()
        flash("That bed or student was just allocated by someone else. Please refresh and try again.", "danger")
    return back_to_map(bed)


@rooms_bp.route("/beds/<int:bed_id>/vacate", methods=["POST"])
@warden_required
def bed_vacate(bed_id):
    bed = get_bed(bed_id)
    if bed is None:
        abort(404)
    occupant = occupant_of_bed(bed_id)
    if occupant is None:
        flash("This bed is not occupied.", "warning")
        return back_to_map(bed)

    store = get_store()
    try:
        vacate_bed(occupant["id"])
        cancel_pending_room_requests(occupant["id"], "Cancelled because the student's bed was vacated.")
        create_notification(occupant["id"], "Bed vacated",
                            f"Your allocation for {bed_label(bed)} has ended. Contact the hostel office for details.",
                            "allocation", url_for("student.my_room"))
        store.commit()
        flash(f"{occupant['name']} was removed from {bed_label(bed)}. The bed is now available.", "success")
    except (InvalidAllocationError, *DatabaseError):   # * unpacks the tuple of database errors
        store.rollback()
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
        store = get_store()
        store.update("beds", bed_id, {"status": new_status})
        store.commit()
        flash(f"{bed_label(bed)} marked as {new_status}.", "success")
    return back_to_map(bed)
