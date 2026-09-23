# HostelHub — Database (Cloud Firestore)

Cloud Firestore in the Firebase project `hostelhub-83310` is the only database. The app on a laptop
and on Vercel read and write the same collections. Check the data at any time with
`python check_database.py`.

The routes never call Firestore directly. They use the small store interface in `data/store.py`
(`get`, `find`, `insert`, `update`, `delete`, `claim`, `release`, `commit`, `rollback`), implemented by
`data/firestore_store.py`; joins and totals are in `data/queries.py`.

## 1. How the store keeps the data correct

| Need | How |
|---|---|
| Readable ids (CMP-0007, room 12) | Each document id is a number. The `counters` collection hands out the next one inside a Firestore **transaction**, so two requests never get the same id |
| All-or-nothing changes | Writes are collected during the request and sent as **one atomic batch** on `commit()`; `rollback()` throws them away. Reads in the same request already see the collected changes |
| No lost updates | Each document read carries its update time. A changed document is written only **if nobody else changed it since the first read** (Firestore precondition); a new one only if its id is still free. Otherwise the whole batch is refused (`ConflictError`), the route rolls back and asks the user to try again |
| One active allocation per bed / per student | Guard documents in `allocation_claims` named `bed-<id>` and `student-<id>`. Allocating must **create** them; Firestore refuses to create an existing document, so only one of two simultaneous allocations can succeed. Vacating deletes them on commit |
| No foreign keys / CHECK constraints | Checked in Python before writing, and afterwards by `check_database.py` (checks 12 and 13) |

## 2. Collections

Times are text `YYYY-MM-DD HH:MM:SS` in Indian Standard Time (`data.now_str()`). Flags are `1`/`0`.
Every document also stores its own `id`.

### users
| Field | Notes |
|---|---|
| name, email | email is lower-case and unique (checked when the warden saves) |
| role | `student` (email `@student.mes.ac.in`) or `warden` (email `@mes.ac.in`); must match the email |
| student_id | college roll number (students), unique |
| phone, department, year_of_study | year 1–4 |
| is_active | 1 active, 0 deactivated (deactivated users cannot sign in) |
| firebase_uid | the Firebase Authentication account; set on creation or first sign-in |
| created_at | |

There are **no passwords** in Firestore; they live in Firebase Authentication.

### rooms
block, floor (≥ 0), room_number (block + number unique), capacity (1–6, equals the number of beds),
status `active` / `maintenance` / `inactive`.

### beds
room_id → rooms, bed_number (unique within the room), status `available` / `occupied` / `reserved` /
`maintenance` / `unavailable`.

### allocations
student_id → users, bed_id → beds, allocated_at, ended_at, status `active` / `ended`. Ended allocations
are kept as history.

### complaints
student_id → users, room_id → rooms, category, description, **image_path** (optional: the generated
file name of the photo, e.g. `3f2a…c9.png`, kept by `storage.py`; never the image itself; `null` when
there is no photo), priority `Low` / `Medium` /
`High`, status `Submitted` / `Acknowledged` / `In Progress` / `Resolved` / `Rejected`, warden_remarks,
created_at, updated_at, resolved_at.

### room_change_requests
student_id → users; current_bed_id, requested_bed_id (optional), assigned_bed_id (set on approval), all
→ beds; reason, details, status `Pending` / `Approved` / `Rejected` / `Cancelled`, warden_remarks,
created_at, updated_at.

### college_maintenance_requests
warden_id → users, complaint_id → complaints (optional), request_type, asset, location, quantity (≥ 1),
description, priority, status `Draft` / `Sent to College` / `Under Review` / `Approved` / `Rejected` /
`Completed`, college_remarks, created_at, updated_at.

### notifications
user_id → users, title, message, type `complaint` / `room_request` / `college` / `allocation` /
`system`, link (internal path), is_read, created_at.

### counters, allocation_claims
Internal (see section 1). Do not edit by hand.

## 3. Relationships

```
rooms ──< beds ──< allocations >── users
  │                                  │
  └──< complaints >──────────────────┤
                                     ├──< room_change_requests >── beds (current / requested / assigned)
complaints ──< college_maintenance_requests >── users (warden)
                                     └──< notifications
```

## 4. Allocation rules

| Rule | Python (`helpers.allocate_bed`) | Firestore |
|---|---|---|
| A bed can't have two active students | bed status must be `available` | guard document `bed-<id>` |
| A student can't have two active beds | `get_active_allocation()` must be empty | guard document `student-<id>` |
| Only real, active students | role and is_active checked | checked by `check_database.py` |
| Only beds in active rooms | room status checked | checked by `check_database.py` |
| Bed status can't be changed under someone's feet | — | update-time precondition on commit |

### Bed status life cycle
```
available ──allocate──▶ occupied ──vacate──▶ available
available ──student requests it──▶ reserved ──approve──▶ occupied
                                    reserved ──reject / cancel / student vacated──▶ available
available ◀──warden──▶ maintenance / unavailable   (only when free)
```

### Room change approval (one atomic write)
1. release the student's reservation if the warden picked a different bed
2. old allocation → `ended`, old bed → `available`, guard documents released
3. guard documents for the new bed taken, new allocation `active`, new bed → `occupied`
4. request → `Approved` with `assigned_bed_id`
5. notification for the student
6. `commit()`: all of the above in one batch. Any error → `rollback()` and nothing is saved.

## 5. Consistency checks (`check_database.py`)

A correct database reports no problems:
1–2. a student or bed with more than one active allocation
3–4. an occupied bed with no active allocation / an active allocation on a non-occupied bed
5. an active allocation for a warden or a deactivated student
6–7. a reserved bed without a pending request / a pending request whose bed isn't reserved
8–9. a room whose bed count ≠ capacity; an inactive room with usable beds
10–11. a resolved complaint without `resolved_at`; an ended allocation without `ended_at`
12. a record pointing to a user, bed or room that does not exist
13. a record with a value that is not allowed (role, statuses)
