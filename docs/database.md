# HostelHub — Database

Schema (single source of truth): `database/schema.sql`. Rebuild: `python seed.py`. Check: `python check_database.py`.

## 0. Two database engines, one set of SQL

| | Local development | Production (Vercel) |
|---|---|---|
| Engine | **SQLite**: file `database/hostelhub.db` | **PostgreSQL**: hosted (e.g. Supabase) |
| Chosen when | `DATABASE_URL` is empty | `DATABASE_URL=postgresql://...` |
| Python driver | `sqlite3` (built into Python) | `psycopg` (in `requirements.txt`) |
| Created by | first run of `app.py`, or `python seed.py` | `python seed.py` with `DATABASE_URL` set (asks you to type `RESET`) |

**Why SQLite locally?** It is a single file, needs no installation or password, and resets in a second,
which is perfect for development, testing and viva demos on a laptop.

**Why PostgreSQL in production?** Vercel's file system is temporary, so a SQLite file there could lose
every complaint and allocation. A hosted PostgreSQL server keeps data permanently, handles many users
at once, and enforces the same constraints.

**How the same SQL works on both** (`database.py`):

| Difference | SQLite | PostgreSQL | How HostelHub handles it |
|---|---|---|---|
| Value placeholder | `?` | `%s` | all queries use `?`; `to_engine_sql()` converts for PostgreSQL |
| New row id | `cursor.lastrowid` | `INSERT ... RETURNING id` | `insert()` adds `RETURNING id` for PostgreSQL |
| Auto-increment id | `INTEGER PRIMARY KEY AUTOINCREMENT` | `SERIAL PRIMARY KEY` | `create_tables()` swaps the text when running `schema.sql` |
| Foreign keys | off unless `PRAGMA foreign_keys = ON` | always on | PRAGMA runs only for SQLite |
| `LIKE` search | ignores capital letters | case-sensitive | queries use `LOWER(column) LIKE ?` |
| Error classes | `sqlite3.Error` | `psycopg.Error` | routes catch `DatabaseError` / `IntegrityError`, which cover both |
| Row access | `sqlite3.Row` | `dict_row` | both allow `row["column"]` |
| Partial unique index | supported | supported | same SQL, no change needed |
| Dates | stored as text `YYYY-MM-DD HH:MM:SS` in India time | same | same |

Everything else (CHECK constraints, UNIQUE, foreign keys, `JOIN`, `GROUP BY`, `CASE`) is identical SQL.
All 75 automated tests pass on both engines (PostgreSQL 18 was used for testing).

Foreign keys are switched on for every SQLite connection (`PRAGMA foreign_keys = ON` in `database.connect()`),
because SQLite ignores them by default.

## 1. Tables

### users
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT NOT NULL | |
| email | TEXT NOT NULL **UNIQUE** | login name |
| password_hash | TEXT NOT NULL | Werkzeug hash, never the plain password |
| role | TEXT NOT NULL | CHECK `student` / `warden` |
| student_id | TEXT **UNIQUE** | college roll number (students) |
| phone, department | TEXT | |
| year_of_study | INTEGER | CHECK 1–4 |
| is_active | INTEGER NOT NULL | 1 active, 0 deactivated |
| firebase_uid | TEXT UNIQUE | reserved for future Google login |
| created_at | TEXT NOT NULL | `YYYY-MM-DD HH:MM:SS` |

### rooms
| Column | Notes |
|---|---|
| id PK | |
| block, floor, room_number | **UNIQUE (block, room_number)**; floor ≥ 0 |
| capacity | CHECK 1–6; equals the number of beds |
| status | CHECK `active` / `maintenance` / `inactive` |

### beds
| Column | Notes |
|---|---|
| id PK | |
| room_id | FK → rooms **ON DELETE CASCADE** |
| bed_number | **UNIQUE (room_id, bed_number)** |
| status | CHECK `available` / `occupied` / `reserved` / `maintenance` / `unavailable` |

### allocations
| Column | Notes |
|---|---|
| id PK | |
| student_id | FK → users (ON DELETE CASCADE) |
| bed_id | FK → beds |
| allocated_at, ended_at | ended_at set when the allocation ends |
| status | CHECK `active` / `ended` |

**Partial unique indexes** (the key to preventing double allocation):
```sql
CREATE UNIQUE INDEX idx_one_active_allocation_per_bed     ON allocations (bed_id)     WHERE status = 'active';
CREATE UNIQUE INDEX idx_one_active_allocation_per_student ON allocations (student_id) WHERE status = 'active';
```
Ended allocations are not covered, so the full history is kept.

### complaints
student_id FK → users, room_id FK → rooms, category, description, image_path (file name only),
priority CHECK Low/Medium/High, status CHECK Submitted/Acknowledged/In Progress/Resolved/Rejected,
warden_remarks, created_at, updated_at, resolved_at.

### room_change_requests
student_id FK → users; current_bed_id, requested_bed_id (optional), assigned_bed_id (set on approval), all FK → beds;
reason, details, status CHECK Pending/Approved/Rejected/Cancelled, warden_remarks, created_at, updated_at.

### college_maintenance_requests
warden_id FK → users, complaint_id FK → complaints (optional), request_type, asset, location,
quantity CHECK ≥ 1, description, priority, status CHECK Draft/Sent to College/Under Review/Approved/Rejected/Completed,
college_remarks, created_at, updated_at.

### notifications
user_id FK → users (ON DELETE CASCADE), title, message, type CHECK complaint/room_request/college/allocation/system,
link (internal path), is_read 0/1, created_at.

### Lookup indexes
`idx_complaints_student`, `idx_complaints_status`, `idx_requests_student`, `idx_notifications_user`.
These let SQLite find rows quickly for the most common `WHERE` clauses.

## 2. Relationships

```
rooms ──< beds ──< allocations >── users
  │                                  │
  └──< complaints >──────────────────┤
                                     ├──< room_change_requests >── beds (current / requested / assigned)
complaints ──< college_maintenance_requests >── users (warden)
                                     └──< notifications
```

## 3. Allocation rules (two layers)

| Rule | Python (`helpers.allocate_bed`) | Database |
|---|---|---|
| A bed can't have two active students | bed status must be `available` | unique index per bed |
| A student can't have two active beds | `get_active_allocation()` must be empty | unique index per student |
| Only real, active students | role and is_active checked | FK to users |
| Only beds in active rooms | room status checked | FK to beds |
| No maintenance/unavailable/reserved beds | status check (reserved only for its own request) | CHECK on status values |

### Bed status life cycle
```
available ──allocate──▶ occupied ──vacate──▶ available
available ──student requests it──▶ reserved ──approve──▶ occupied
                                    reserved ──reject / cancel / student vacated──▶ available
available ◀──warden──▶ maintenance / unavailable   (only when free)
```

### Room change approval (one transaction)
1. release the student's reservation if the warden picked a different bed
2. `UPDATE allocations SET status='ended'` (old) and `UPDATE beds SET status='available'` (old bed)
3. `INSERT INTO allocations … 'active'` (new) and `UPDATE beds SET status='occupied'` (new bed)
4. `UPDATE room_change_requests SET status='Approved', assigned_bed_id=…`
5. `INSERT INTO notifications …`
6. `commit()`. Any error → `rollback()` and nothing above is saved.

This behaves the same on PostgreSQL: a failed statement marks the whole transaction as failed,
and `rollback()` undoes every step. The automated rollback test passes on both engines.

## 4. Consistency checks (`check_database.py`)

Each is a query that must return no rows:
- a student or bed with more than one active allocation
- an occupied bed with no active allocation / an active allocation on a non-occupied bed
- an active allocation for a warden or a deactivated student
- a reserved bed without a pending request / a pending request whose bed isn't reserved
- a room whose bed count ≠ capacity; an inactive room with usable beds
- a resolved complaint without `resolved_at`; an ended allocation without `ended_at`
- plus, on SQLite only, `PRAGMA foreign_key_check` (PostgreSQL never allows broken references to exist)

The same checks run on PostgreSQL when `DATABASE_URL` is set.
