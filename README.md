# HostelHub

**College Hostel Room Allocation and Maintenance Portal**

A Flask + SQLite web portal where students check their room, report maintenance problems and ask for room changes, and the hostel warden manages students, rooms, beds, complaints and repair requests to the college.

Academic project by a team of four second-year Computer Engineering students.

---

## 1. Problem statement

Many college hostels still run on registers, WhatsApp messages and spreadsheets:

- Wardens can't quickly see which beds are free.
- A maintenance complaint ("the fan is broken") gets lost, and the student never hears back.
- Room changes are handled verbally, so two students can end up promised the same bed.
- Repairs that need college approval have no written trail.

## 2. Objectives

1. Show bed availability visually, straight from the database.
2. Let students report problems with a photo and track the status.
3. Handle room changes safely, so a bed can never be given to two students.
4. Keep role-based access: students see only their own data.
5. Keep the code simple enough for second-year students to explain in a viva.

## 3. Main users

| Role | What they do |
|---|---|
| **Student** | Views their room, reports complaints, requests room changes, reads notifications |
| **Warden** | Manages students, rooms and beds; allocates beds; resolves complaints; approves room changes; raises college requests |

There is no college-admin login yet. The warden records the college's replies.

## 4. Features

**Student**
- Dashboard with block, floor, room, bed, open complaints, pending requests and notifications
- *My Room*: a floor plan of their room with their own bed highlighted, plus roommates (name, department and year only)
- Report a complaint: category, priority, description, and an optional photo with a preview
- Complaint history, progress tracker and warden remarks
- Room-change request (optionally choosing a free bed), which can be tracked or cancelled
- Notifications (read/unread); profile with phone update and password change

**Warden**
- Dashboard: total students, rooms and beds; occupied and available beds; pending complaints and requests; occupancy %; per-block occupancy; maintenance overview; "needs attention" list; recent activity
- **Visual allocation map**: choose a block and floor, see beds coloured by their database status, click a bed to allocate, vacate or change its status, and search rooms or students on the floor
- Students: search, filter, add, edit, view details, deactivate/reactivate, delete (only when the student has no history)
- Rooms: search, filter, add, edit capacity or status, delete (only when the room has no history)
- Complaints: search and filter (status, category, priority, block), view the photo, update the status with remarks, escalate to the college
- Room-change requests: review, approve (the student is moved automatically) or reject with remarks
- College maintenance requests: draft → send → track status, record the college's remarks, print

**Bed colours on the map** (always read from `beds.status`)

| Colour | Status | Meaning |
|---|---|---|
| 🟢 Green | `available` | Free, can be allocated |
| 🔴 Red | `occupied` | A student is allocated |
| 🟡 Yellow | `reserved` | Held for a pending room-change request |
| 🔵 Blue | `maintenance` | Under repair |
| ⚪ Grey | `unavailable` | Closed / room inactive |

## 5. Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+ and Flask (routes, sessions, Jinja2 templates) |
| Database | SQLite through Python's built-in `sqlite3` module (plain SQL, no ORM) |
| Frontend | HTML, Bootstrap 5.3, Bootstrap Icons, custom CSS, vanilla JavaScript |
| Security | Werkzeug password hashing, session login, server-side role checks, CSRF tokens, parameterised SQL |
| Testing | Python `unittest` and Flask's test client |

**Flask is the only package in `requirements.txt`.** Bootstrap, the icons and the font load from CDNs, so the browser needs internet access.

## 6. Architecture overview

```
Browser (HTML form / button, small JavaScript helpers)
   │  GET or POST (+ CSRF token)
   ▼
Flask route  routes/*.py      ← checks login and ROLE on the server
   │
Python logic  helpers.py      ← validation and hostel rules (e.g. no double allocation)
   │
SQL query     database.py     ← parameterised queries with ? placeholders
   │
SQLite        database/hostelhub.db
   │
commit() or rollback()
   ▼
flash() message + redirect / render_template() → Jinja template → page
```

More detail: [docs/architecture.md](docs/architecture.md).

## 7. Project structure

```
HostelHub/
├── app.py              Creates the app, registers blueprints, template filters, error pages
├── config.py           Settings + choice lists (categories, statuses, …)
├── database.py         SQLite connection and query helpers
├── helpers.py          Shared rules: allocation, notifications, image uploads
├── seed.py             Rebuilds the demo database
├── check_database.py   Checks the data for inconsistencies
├── requirements.txt    Flask
├── README.md · bank.md (implementation notebook)
│
├── database/schema.sql 8 tables, constraints and indexes
├── routes/             One blueprint per feature
│   ├── auth.py         login/logout, CSRF check, @student_required / @warden_required
│   ├── account.py      notifications + profile (both roles)
│   ├── student.py      student dashboard, My Room
│   ├── warden.py       warden dashboard, student CRUD
│   ├── rooms.py        room CRUD, allocation map, bed actions
│   ├── complaints.py   complaints (both roles) + protected image route
│   ├── requests.py     room-change requests (both roles)
│   └── college.py      college maintenance requests
├── templates/          base.html, _macros.html, and one folder per feature
├── static/             css/style.css, js/main.js, js/room-map.js, js/complaint-form.js, images/
├── uploads/complaints/ uploaded photos (NOT public; served through a protected route)
├── tests/              69 automated tests (base.py + 4 test files)
└── docs/               architecture.md, database.md, testing.md, viva.md
```

## 8. Database tables and relationships

| Table | Purpose |
|---|---|
| `users` | Students and wardens (`role` column), hashed passwords |
| `rooms` | Block, floor, room number, capacity, status |
| `beds` | Beds of each room, with a status that drives the map colours |
| `allocations` | Which student has which bed; old rows are kept as history (`ended`) |
| `complaints` | Maintenance complaints (image stored as a file name) |
| `room_change_requests` | Current bed, preferred bed, assigned bed, status |
| `college_maintenance_requests` | Escalations to the college |
| `notifications` | Per-user messages with read/unread state |

```
rooms 1──* beds 1──* allocations *──1 users
users 1──* complaints *──1 rooms
users 1──* room_change_requests ──> beds (current, requested, assigned)
users 1──* college_maintenance_requests *──0..1 complaints
users 1──* notifications
```

Two **partial unique indexes** make double allocation impossible at database level:
one active allocation per bed, and one per student. Full details: [docs/database.md](docs/database.md).

## 9. Authentication approach

- Login uses email + password. Only a **hash** of the password is stored (`generate_password_hash`).
- After login, the session stores **only the user id**. On every request the user, **including the role**, is loaded again from the database.
- `@student_required` and `@warden_required` check the role on the server (403 if wrong). Hidden links in the UI are not the security.
- Deactivated users are logged out on their next request.
- **Firebase later:** only `authenticate_local()` in `routes/auth.py` needs replacing (verify the Google token, then find the approved `@mes.ac.in` user). Sessions, roles and pages stay the same. The `firebase_uid` column is already reserved.

## 10. Workflows

**Student:** log in → dashboard → My Room → report a complaint (with photo) → track the status and read remarks → request a room change → track or cancel it → read notifications.

**Warden:** log in → dashboard → allocation map → students / rooms → complaints → room requests → college requests → notifications.

**Room / bed allocation:** on the map, click a green bed → choose a student waiting for a bed → *Allocate*. In one transaction an allocation row is inserted and the bed becomes `occupied`. *Vacate* ends the allocation and makes the bed `available`. The map is re-drawn from the database after every action.

**Maintenance:** the student submits → `Submitted` → the warden sets `Acknowledged` → `In Progress` → `Resolved` (or `Rejected`, which needs remarks). Final statuses can't change. Each update notifies the student.

**Room change:** the student requests (the chosen bed becomes `reserved`) → the warden approves or rejects.
On approval, **all together in one transaction**: old allocation ended, old bed available, new allocation created, new bed occupied, request `Approved`, student notified. If any step fails, `rollback()` undoes everything. Rejecting or cancelling releases the reserved bed.

**College maintenance:** the warden creates a request (type, asset, location, quantity, priority, description) → *Save draft* or *Send to college* → the status is updated as the college responds (`Under Review`, `Approved`/`Rejected`, `Completed`) along with the college's remarks. Drafts can be edited or deleted; sent requests are kept. A complaint can be escalated directly, and the student is notified.

**Notifications** are created by real events:

| Event | Recipient |
|---|---|
| Complaint submitted | the student (confirmation) + every warden |
| Complaint status or remarks changed / escalated | that student |
| Room change requested | every warden |
| Room change approved / rejected | that student |
| Bed allocated / vacated | that student |

## 11. Installation and setup

Requires **Python 3.10 or newer**.

```bash
python -m venv .venv
```

Activate it. On Windows PowerShell:

```bash
.venv\Scripts\Activate.ps1
```

On macOS / Linux:

```bash
source .venv/bin/activate
```

Then install Flask:

```bash
pip install -r requirements.txt
```

## 12. Seeding the demo data

```bash
python seed.py
```

This **deletes all data** and rebuilds `database/hostelhub.db`. (`app.py` also runs it automatically if the database file doesn't exist.) Uploaded photos in `uploads/complaints/` are not deleted.

Demo data: 3 blocks (A, B, C), 32 rooms, 100 beds, 72 students (5 waiting for a bed), complaints in every status, 2 pending room-change requests (yellow beds), 4 college requests and sample notifications. All names are fictional.

To check the data at any time:

```bash
python check_database.py
```

## 13. Running the application

```bash
python app.py
```

Open **http://127.0.0.1:5000**. Debug mode is **off** by default. For development, set `HOSTELHUB_DEBUG=1` before starting.

## 14. Demo credentials

| Role | Email | Password |
|---|---|---|
| Student | `student@mes.ac.in` | `Student@123` |
| Warden | `warden@mes.ac.in` | `Warden@123` |

Every other seeded student (e.g. `rohan.patil@mes.ac.in`) also uses `Student@123`. These are fictional demo accounts; passwords are stored hashed. The login page's demo buttons only fill in the form, and **the role always comes from the database**.

## 15. Testing

```bash
python -m unittest discover tests -v
```

69 tests cover login, role checks on every route, CSRF, privacy, uploads, allocation edge cases, room-change rollback, CRUD, notifications, dashboard numbers and the seed data. Every test uses its own temporary database and ends with a data-consistency check. Manual test results are in [docs/testing.md](docs/testing.md).

## 16. Security measures

| Risk | Measure |
|---|---|
| Stolen passwords | Only salted hashes are stored (Werkzeug) |
| Student opening warden pages | Server-side role decorators on every warden route (403) |
| Changing the role in the cookie | The session holds only the user id; the role is read from the database |
| Seeing other students' data | Every student query filters by the logged-in id; other students' records return 404 |
| SQL injection | All values go through `?` placeholders |
| Forged form posts (CSRF) | A random per-session token in every POST form, checked on the server (400 if wrong). `SameSite=Lax` cookies add a second, browser-side defence |
| Malicious uploads | Extension allow-list + MIME type + real file signature check, random UUID file names, 5 MB limit |
| Public photo URLs | Photos live outside `static/` and are sent only to the owner student or a warden |
| Error details leaking | Friendly 400/403/404/413/500 pages; debug mode off by default |
| Open redirects | Notification links are only followed if they are internal paths |

## 17. Known limitations

- **Development server only.** A real deployment needs a WSGI server (Waitress/Gunicorn), HTTPS, and a secret `HOSTELHUB_SECRET_KEY`. The default key in `config.py` is for demos only.
- **No college login.** The warden types in the college's response.
- **No password reset by email** and **no login rate-limiting**.
- **Needs internet** for Bootstrap, the icons and the font (CDN).
- **Search treats `%` and `_` as wildcards** (SQL `LIKE`). This is harmless, but searching for a literal `%` matches everything.
- **Notifications appear on the next page load**, not in real time.
- **Deleting is restricted:** students and rooms with history can only be deactivated, which keeps the records intact.

## 18. Future scope

- Firebase Google sign-in limited to MES accounts
- A college-admin role to respond to maintenance requests directly
- Hostel fee and mess management
- Export reports (occupancy, complaint turnaround) to PDF/Excel
- Email or push notifications
- Complaint analytics over time (e.g. monthly trends)
