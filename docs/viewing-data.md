# HostelHub — Viewing the Data (Local SQLite)

HostelHub stores its data **locally in SQLite**. Firebase is used **only to check who is logging in**
("Continue with Google"); no hostel data is kept in Firebase.

| What | Where it lives locally |
|---|---|
| All hostel data (users, rooms, beds, allocations, complaints, requests, notifications) | `database/hostelhub.db`, one SQLite file |
| Complaint photos | `uploads/complaints/<random-name>.png`; the database stores only that file name in `complaints.image_path` |
| Google sign-in accounts | Firebase (Console → Authentication → Users). Our `users.firebase_uid` column remembers which Google account belongs to which HostelHub user |

All commands below are run **from the project folder with the virtual environment activated**
(`.venv\Scripts\Activate.ps1` on Windows).

---

## 1. Quickest way: `view_data.py` (read-only)

`view_data.py` opens the database **read-only**, so it can never change or delete data. Password hashes are hidden.

**Every table and its number of rows:**

```bash
python view_data.py
```

**The latest 10 rows of one table** (newest first):

```bash
python view_data.py complaints
```

**The latest 25 rows:**

```bash
python view_data.py users 25
```

Table names: `users`, `rooms`, `beds`, `allocations`, `complaints`, `room_change_requests`,
`college_maintenance_requests`, `notifications`.

**Run your own SELECT query:**

```bash
python view_data.py --sql "SELECT name, email, role FROM users WHERE role = 'warden'"
```

### Watch data being inserted, live

Open **two windows** side by side:

1. Terminal: start the app with `python app.py`
2. A second terminal: start the watcher:

```bash
python view_data.py --watch
```

3. Browser: open http://localhost:5000 and use the app (submit a complaint, allocate a bed, request a room change...).

Within about a second, the watcher prints every **new row** the app inserts, for example:

```
[16:31:12] NEW in complaints:
id  student_id  room_id  category  description                  image_path          priority  status     ...
14  2           5        Fan       Fan makes a grinding noise.  d2a0360e...png      High      Submitted  ...

[16:31:12] NEW in notifications:
...
```

Press **Ctrl+C** to stop. This makes a good live demo in the viva: *click in the browser → the new row appears.*

> The watcher shows **new** rows (INSERT). A change to an existing row (UPDATE), for example a complaint
> moving from *Submitted* to *In Progress*, is seen with `python view_data.py complaints`.

---

## 2. Python's built-in SQLite shell (nothing to install)

Python 3.12+ includes a small SQLite shell:

```bash
python -m sqlite3 database/hostelhub.db
```

Type SQL at the `sqlite>` prompt and press Enter. For example:

```sql
SELECT id, name, email, role FROM users LIMIT 5;
```

- Type `.quit` (or press Ctrl+Z then Enter on Windows) to leave.
- This shell **can change data** (INSERT/UPDATE/DELETE work), so use only `SELECT` unless you really mean it.
  If you break something, rebuild the demo with `python seed.py`.
- The "dot commands" of the full sqlite3 program (like `.tables`) are not available here. List the tables with:

```sql
SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name;
```

See how a table was created (its columns and constraints):

```sql
SELECT sql FROM sqlite_master WHERE name = 'allocations';
```

---

## 3. Graphical tools (optional)

Any SQLite viewer can open `database/hostelhub.db`:

| Tool | How |
|---|---|
| **VS Code** | Install the extension *SQLite Viewer* (by Florian Klampfer), then click `database/hostelhub.db` in the explorer. It is read-only and shows every table as a grid. |
| **DB Browser for SQLite** (free, sqlitebrowser.org) | *Open Database* → choose `database/hostelhub.db` → *Browse Data* tab. The *Execute SQL* tab runs queries. Use *File → Open Database Read Only* to avoid accidental edits. |
| **PyCharm** (if your edition has the *Database* tool window) | Drag `hostelhub.db` into the Database window and choose SQLite. |

Tips:
- Refresh the viewer (F5) after using the app to see new rows.
- Do **not** edit rows by hand while the app is running. Use the app, or rebuild with `python seed.py`.

---

## 4. Useful queries for the demo and report

Run each with `python view_data.py --sql "..."`, or paste it into `python -m sqlite3 database/hostelhub.db`.

**Which student is in which bed** (JOIN across four tables):

```sql
SELECT u.name, u.email, r.block || '-' || r.room_number AS room, b.bed_number, a.allocated_at
FROM allocations a
JOIN users u ON u.id = a.student_id
JOIN beds  b ON b.id = a.bed_id
JOIN rooms r ON r.id = b.room_id
WHERE a.status = 'active'
ORDER BY room, b.bed_number
LIMIT 10;
```

**Bed status counts** (these colour the allocation map):

```sql
SELECT status, COUNT(*) AS beds FROM beds GROUP BY status;
```

**Beds of one room** (compare with the map):

```sql
SELECT r.block || '-' || r.room_number AS room, b.bed_number, b.status
FROM beds b JOIN rooms r ON r.id = b.room_id
WHERE r.block = 'A' AND r.room_number = '201';
```

**Latest complaints with student and room:**

```sql
SELECT c.id, u.name, r.block || '-' || r.room_number AS room, c.category, c.priority, c.status, c.image_path
FROM complaints c
JOIN users u ON u.id = c.student_id
JOIN rooms r ON r.id = c.room_id
ORDER BY c.id DESC
LIMIT 5;
```

**Room change requests and the beds involved:**

```sql
SELECT id, student_id, current_bed_id, requested_bed_id, assigned_bed_id, status, warden_remarks
FROM room_change_requests ORDER BY id DESC;
```

**One student's notifications** (the bell icon):

```sql
SELECT title, message, is_read, created_at
FROM notifications
WHERE user_id = (SELECT id FROM users WHERE email = 'student@student.mes.ac.in')
ORDER BY id DESC;
```

**Who has signed in with Google** (linked Firebase account):

```sql
SELECT name, email, firebase_uid FROM users WHERE firebase_uid IS NOT NULL;
```

**Students still waiting for a bed:**

```sql
SELECT name, student_id FROM users
WHERE role = 'student' AND is_active = 1
  AND id NOT IN (SELECT student_id FROM allocations WHERE status = 'active');
```

**The indexes that block double allocation:**

```sql
SELECT name, sql FROM sqlite_master WHERE name LIKE 'idx_one_active%';
```

**Passwords are never stored in plain text** (only the start of each hash is shown):

```sql
SELECT email, substr(password_hash, 1, 25) AS hash_start FROM users LIMIT 3;
```

---

## 5. Which action changes which table

Useful for explaining CRUD in the report: do the action in the browser, then look at the table.

| Action in the app | SQL | Table(s) |
|---|---|---|
| Student submits a complaint (with photo) | INSERT | `complaints` (+ photo file in `uploads/complaints/`), `notifications` (student + warden) |
| Warden changes complaint status / remarks | UPDATE | `complaints` (`status`, `warden_remarks`, `updated_at`, `resolved_at`), INSERT `notifications` |
| Warden allocates a bed on the map | INSERT + UPDATE | new `allocations` row (`active`); `beds.status` → `occupied`; `notifications` |
| Warden vacates a bed | UPDATE | `allocations.status` → `ended`, `ended_at`; `beds.status` → `available` |
| Student requests a room change (choosing a bed) | INSERT + UPDATE | `room_change_requests` (`Pending`); `beds.status` → `reserved` (yellow) |
| Warden approves the room change | UPDATE + INSERT (one transaction) | old allocation ended, old bed `available`, new allocation, new bed `occupied`, request `Approved`, `notifications` |
| Warden adds / edits / deletes a student | INSERT / UPDATE / DELETE | `users` |
| Warden adds / edits a room | INSERT / UPDATE | `rooms`, `beds` |
| Warden creates / updates a college request | INSERT / UPDATE | `college_maintenance_requests` |
| First "Continue with Google" sign-in | UPDATE | `users.firebase_uid` |
| Marking notifications read / clearing them | UPDATE / DELETE | `notifications` |

---

## 6. Resetting and checking

**Rebuild the demo data** (deletes everything in `database/hostelhub.db`):

```bash
python seed.py
```

**Check that the data follows every hostel rule** (no double allocation, bed statuses match allocations, ...):

```bash
python check_database.py
```

Expected: `SQLite database is consistent: all 11 checks passed.`

Uploaded photos are not deleted by `seed.py`. To remove them, delete the files inside `uploads/complaints/`
but keep `.gitkeep`.
