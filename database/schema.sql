-- =============================================================
-- HostelHub database schema (SQLite)
-- Running this file deletes old tables and creates fresh ones.
-- =============================================================

PRAGMA foreign_keys = ON;

-- Drop child tables before parent tables (foreign keys depend on parents).
DROP TABLE IF EXISTS notifications;
DROP TABLE IF EXISTS college_maintenance_requests;
DROP TABLE IF EXISTS room_change_requests;
DROP TABLE IF EXISTS complaints;
DROP TABLE IF EXISTS allocations;
DROP TABLE IF EXISTS beds;
DROP TABLE IF EXISTS rooms;
DROP TABLE IF EXISTS users;


-- -------------------------------------------------------------
-- users: both students and wardens. "role" decides what they can do.
-- -------------------------------------------------------------
CREATE TABLE users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    email           TEXT    NOT NULL UNIQUE,
    password_hash   TEXT    NOT NULL,            -- never the plain password
    role            TEXT    NOT NULL CHECK (role IN ('student', 'warden')),
    student_id      TEXT    UNIQUE,              -- college roll number (students only)
    phone           TEXT,
    department      TEXT,
    year_of_study   INTEGER CHECK (year_of_study BETWEEN 1 AND 4),
    is_active       INTEGER NOT NULL DEFAULT 1,  -- 0 = deactivated account
    firebase_uid    TEXT    UNIQUE,              -- reserved for future Google login
    created_at      TEXT    NOT NULL
);


-- -------------------------------------------------------------
-- rooms: one row per physical room, e.g. Block A, room 201.
-- -------------------------------------------------------------
CREATE TABLE rooms (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    block        TEXT    NOT NULL,
    floor        INTEGER NOT NULL CHECK (floor >= 0),
    room_number  TEXT    NOT NULL,
    capacity     INTEGER NOT NULL CHECK (capacity BETWEEN 1 AND 6),
    status       TEXT    NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'maintenance', 'inactive')),
    UNIQUE (block, room_number)      -- no two "A-201" rooms
);


-- -------------------------------------------------------------
-- beds: each room has "capacity" beds. The bed status drives the
-- colours of the visual allocation map.
-- -------------------------------------------------------------
CREATE TABLE beds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id     INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    bed_number  INTEGER NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'available'
                CHECK (status IN ('available', 'occupied', 'reserved', 'maintenance', 'unavailable')),
    UNIQUE (room_id, bed_number)
);


-- -------------------------------------------------------------
-- allocations: which student sleeps in which bed, with history.
-- Old allocations are kept with status 'ended'.
-- -------------------------------------------------------------
CREATE TABLE allocations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bed_id        INTEGER NOT NULL REFERENCES beds(id),
    allocated_at  TEXT    NOT NULL,
    ended_at      TEXT,
    status        TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended'))
);

-- Database-level safety net against double allocation:
-- only ONE active allocation may exist per bed, and per student.
-- (These are "partial" unique indexes: they only apply WHERE status = 'active'.)
CREATE UNIQUE INDEX idx_one_active_allocation_per_bed
    ON allocations (bed_id) WHERE status = 'active';
CREATE UNIQUE INDEX idx_one_active_allocation_per_student
    ON allocations (student_id) WHERE status = 'active';


-- -------------------------------------------------------------
-- complaints: maintenance issues reported by students.
-- -------------------------------------------------------------
CREATE TABLE complaints (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES users(id),
    room_id         INTEGER NOT NULL REFERENCES rooms(id),
    category        TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    image_path      TEXT,                        -- generated file name in uploads/complaints/, e.g. 3f2a9c.jpg
    priority        TEXT    NOT NULL DEFAULT 'Medium' CHECK (priority IN ('Low', 'Medium', 'High')),
    status          TEXT    NOT NULL DEFAULT 'Submitted'
                    CHECK (status IN ('Submitted', 'Acknowledged', 'In Progress', 'Resolved', 'Rejected')),
    warden_remarks  TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    resolved_at     TEXT
);


-- -------------------------------------------------------------
-- room_change_requests: students ask, the warden decides.
-- -------------------------------------------------------------
CREATE TABLE room_change_requests (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id        INTEGER NOT NULL REFERENCES users(id),
    current_bed_id    INTEGER NOT NULL REFERENCES beds(id),
    requested_bed_id  INTEGER REFERENCES beds(id),   -- optional preference
    assigned_bed_id   INTEGER REFERENCES beds(id),   -- bed actually given on approval
    reason            TEXT    NOT NULL,
    details           TEXT,
    status            TEXT    NOT NULL DEFAULT 'Pending'
                      CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Cancelled')),
    warden_remarks    TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);


-- -------------------------------------------------------------
-- college_maintenance_requests: escalations from the warden to college.
-- -------------------------------------------------------------
CREATE TABLE college_maintenance_requests (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    warden_id        INTEGER NOT NULL REFERENCES users(id),
    complaint_id     INTEGER REFERENCES complaints(id),  -- set when escalated from a complaint
    request_type     TEXT    NOT NULL,
    asset            TEXT    NOT NULL,
    location         TEXT    NOT NULL,
    quantity         INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 1),
    description      TEXT    NOT NULL,
    priority         TEXT    NOT NULL DEFAULT 'Medium' CHECK (priority IN ('Low', 'Medium', 'High')),
    status           TEXT    NOT NULL DEFAULT 'Draft'
                     CHECK (status IN ('Draft', 'Sent to College', 'Under Review', 'Approved', 'Rejected', 'Completed')),
    college_remarks  TEXT,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);


-- Ordinary indexes on columns we search by very often (like a book index:
-- SQLite can jump to matching rows instead of scanning the whole table).
CREATE INDEX idx_complaints_student ON complaints (student_id);
CREATE INDEX idx_complaints_status ON complaints (status);
CREATE INDEX idx_requests_student ON room_change_requests (student_id);


-- -------------------------------------------------------------
-- notifications: simple inbox messages stored per user.
-- -------------------------------------------------------------
CREATE TABLE notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    type        TEXT    NOT NULL DEFAULT 'system'
                CHECK (type IN ('complaint', 'room_request', 'college', 'allocation', 'system')),
    link        TEXT,                              -- page to open, e.g. /student/complaints/4
    is_read     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

CREATE INDEX idx_notifications_user ON notifications (user_id, is_read);
