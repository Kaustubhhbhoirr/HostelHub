# HostelHub

**College Hostel Room Allocation and Maintenance Portal**

A Flask web portal where students check their room, report maintenance problems and ask for room changes, and the hostel warden manages students, rooms, beds, complaints and repair requests to the college.

Academic project by a team of four second-year Computer Engineering students.

HostelHub runs in **two modes** with the same code:

| | Local development | Deployment (Vercel) |
|---|---|---|
| Web server | `python app.py` on your computer | Vercel Functions (zero-config Flask) |
| Database | SQLite file `database/hostelhub.db` | Hosted PostgreSQL (e.g. Supabase) |
| Complaint photos | `uploads/complaints/` folder | Private Supabase Storage bucket |
| Setup needed | none (no environment variables) | environment variables, see [docs/deployment.md](docs/deployment.md) |

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
- Dashboard: total students, rooms and beds; occupied and available beds; pending complaints and requests; occupancy %; per-block occupancy; maintenance overview; "needs attention" list
- **Visual allocation map**: choose a block and floor, see beds coloured by their database status, click a bed to allocate, vacate or change its status, and search rooms or students on the floor
- Students: search, filter, add, edit, view, deactivate/reactivate, delete (only when the student has no history)
- Rooms: search, filter, add, edit capacity or status, delete (only when the room has no history)
- Complaints: search and filter, view the photo, update the status with remarks, escalate to the college
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
| Backend | Python 3.14 (3.12+ works) and Flask (routes, sessions, Jinja2 templates) |
| Database | Plain SQL; SQLite locally, PostgreSQL in deployment (`psycopg` driver). No ORM |
| Photo storage | Local folder locally; private Supabase Storage bucket in deployment (Python's built-in `urllib`) |
| Frontend | HTML, Bootstrap 5.3, Bootstrap Icons, custom CSS, vanilla JavaScript |
| Security | Werkzeug password hashing, session login, server-side role checks, CSRF tokens, parameterised SQL |
| Hosting | Vercel (zero-configuration Flask) |
| Testing | Python `unittest` and Flask's test client |

`requirements.txt` contains only **Flask** and **psycopg** (the PostgreSQL driver, which is unused locally). Bootstrap, the icons and the font load from CDNs.

## 6. Architecture overview

```
LOCAL                                     DEPLOYMENT
Browser                                   Browser
  │                                         │  HTTPS
Flask (python app.py)                     Vercel ── CDN serves public/static/*
  │                                         │
  ├── SQLite  database/hostelhub.db         Flask (Vercel Function)
  └── uploads/complaints/                   ├── hosted PostgreSQL   (DATABASE_URL)
                                            └── private Supabase bucket (SUPABASE_URL)
```

Every request follows the same path in both modes:

```
form / button (+ CSRF token) → Flask route (login + ROLE check on the server)
  → Python rules (helpers.py) → parameterised SQL (database.py)
  → commit() or rollback() → flash() + redirect / render_template → page
```

**Why storage differs:** on Vercel, each function has a *temporary* file system, so a SQLite file or an uploaded photo saved there would disappear. Deployment therefore keeps data in a database server and photos in object storage. Details: [docs/architecture.md](docs/architecture.md).

## 7. Project structure

```
HostelHub/
├── app.py              Creates the app (Vercel entrypoint), blueprints, filters, error pages
├── config.py           Settings from environment variables + choice lists + production checks
├── database.py         SQLite / PostgreSQL connection and query helpers (plain SQL)
├── storage.py          Complaint photo storage: local folder or private Supabase bucket
├── helpers.py          Shared rules: allocation, notifications, upload validation
├── seed.py             Rebuilds the demo database (SQLite, or PostgreSQL with confirmation)
├── check_database.py   Checks the data for inconsistencies (both databases)
├── requirements.txt    Flask, psycopg, firebase-admin, python-dotenv, gunicorn
├── render.yaml         Render Blueprint (web service, start command, environment variables)
├── .python-version     Python version used by Vercel
├── .env.example        Names of the environment variables (no values)
│
├── database/schema.sql 8 tables, constraints and indexes (single source of truth)
├── routes/             One blueprint per feature
│   ├── auth.py         login/logout, CSRF check, @student_required / @warden_required
│   ├── account.py      notifications + profile (both roles)
│   ├── student.py      student dashboard, My Room
│   ├── warden.py       warden dashboard, student CRUD
│   ├── rooms.py        room CRUD, allocation map, bed actions
│   ├── complaints.py   complaints (both roles) + protected photo route
│   ├── requests.py     room-change requests (both roles)
│   └── college.py      college maintenance requests
├── templates/          base.html, _macros.html, and one folder per feature
├── public/static/      css/, js/, images/ (served by Vercel's CDN; by Flask locally)
├── uploads/complaints/ local photos only (git-ignored, private)
├── tests/              110 automated tests (base.py + 7 test files)
└── docs/               architecture, database, deployment, testing, viva
```

## 8. Database

| Table | Purpose |
|---|---|
| `users` | Students and wardens (`role` column), hashed passwords |
| `rooms` | Block, floor, room number, capacity, status |
| `beds` | Beds of each room; the status drives the map colours |
| `allocations` | Which student has which bed; old rows are kept as history (`ended`) |
| `complaints` | Maintenance complaints (photo stored as a generated file name) |
| `room_change_requests` | Current, preferred and assigned bed, status |
| `college_maintenance_requests` | Escalations to the college |
| `notifications` | Per-user messages with read/unread state |

Two **partial unique indexes** (one active allocation per bed, one per student) make double allocation impossible at database level, in SQLite **and** PostgreSQL. Full details: [docs/database.md](docs/database.md).

## 9. Authentication approach

There are two ways to log in. Both end the same way: the session stores only the **user id**, and the **role is read from our own database** on every request.

1. **Continue with Google (Firebase Authentication)**, for real MES accounts.
   - The login page opens Google's sign-in popup with the Firebase JavaScript SDK.
   - Firebase gives the browser a signed **ID token**. The page sends it, plus the CSRF token, to `POST /firebase-login`.
   - `authenticate_google()` in `routes/auth.py` verifies the token with the **Firebase Admin SDK**. The token must be genuine, unexpired and for our Firebase project.
   - The email must be **verified** and end with `@student.mes.ac.in` (students) or `@mes.ac.in` (staff).
   - The email must already be **registered by the warden** in HostelHub. Google alone is not enough.
   - On first sign-in the Google account's id is saved in `users.firebase_uid`. A different Google account can never take over that user.
   - **Firebase is used only to check who is logging in.** All hostel data stays in HostelHub's own database (SQLite locally).
2. **Email + password**, for the demo accounts. Only a **hash** of the password is stored.

Other rules:
- Every **student** email must end with **`@student.mes.ac.in`** (the student form enforces it). The warden uses `@mes.ac.in`.
- `@student_required` / `@warden_required` check the role on the server (403 if wrong).

## 10. Workflows

**Room / bed allocation:** on the map, click a green bed → choose a student waiting for a bed → *Allocate*. The allocation row is inserted and the bed becomes `occupied` in one transaction. *Vacate* ends the allocation and frees the bed.

**Maintenance:** `Submitted` → `Acknowledged` → `In Progress` → `Resolved` (or `Rejected`, which needs remarks). Every update notifies the student.

**Room change:** the student requests (the chosen bed turns yellow `reserved`) → the warden approves or rejects. On approval, in **one transaction**: old allocation ended, old bed available, new allocation created, new bed occupied, request approved, student notified. Any failure → `rollback()`.

**College maintenance:** the warden drafts or sends a repair/replacement request (type, asset, location, quantity, priority, description) and updates its status as the college responds.

**Notifications** are created by real events: new complaint and new room request (wardens); status changes, remarks, escalation, approval/rejection, allocation and vacating (the student concerned).

---

## 11. Local development (SQLite)

Requires **Python 3.12 or newer**. No database server and no environment variables are needed.

**1. Get the code**

```bash
git clone https://github.com/Kaustubhhbhoirr/HostelHub.git
```

```bash
cd HostelHub
```

**2. Create and activate a virtual environment**

```bash
python -m venv .venv
```

Windows PowerShell:

```bash
.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
source .venv/bin/activate
```

**3. Install the requirements**

```bash
pip install -r requirements.txt
```

**4. Create the demo database** (this deletes and rebuilds `database/hostelhub.db`)

```bash
python seed.py
```

**5. Check the data**

```bash
python check_database.py
```

**6. Run the tests**

```bash
python -m unittest discover tests -v
```

**7. Start the app**

```bash
python app.py
```

Open **http://localhost:5000**. Debug mode is off unless you set `HOSTELHUB_DEBUG=1`.

### Logging in

| Account | How to log in |
|---|---|
| `kaustubhb25comp@student.mes.ac.in` (Kaustubh Bhoir, student, room A-102) | **Continue with Google**; this account has no usable password |
| `student@student.mes.ac.in` (demo student) | password `Student@123` |
| `warden@mes.ac.in` (demo warden) | password `Warden@123` |

Every other seeded student (e.g. `rohan.patil@student.mes.ac.in`) also uses `Student@123`. The demo names are fictional; passwords are stored hashed. Demo data: 3 blocks, 32 rooms, 100 beds, 73 students (72 demo + 1 Google account; 5 waiting for a bed), complaints in every status, 2 pending room changes, 4 college requests.

To let another team member use Google login, the warden adds them under **Students → Add student** with their `@student.mes.ac.in` email, or you add them to `GOOGLE_STUDENTS` in `seed.py`.

**Google login locally needs:**
- the two `FIREBASE_*` values in a `.env` file (see section 12)
- the app opened at **http://localhost:5000**. `127.0.0.1` is not an authorised domain in Firebase, so the Google popup would refuse it.

> **On a public deployment, change the warden password immediately** (Profile → Change password), because these demo passwords are published in this README.

## 12. Environment variables

Environment variables are settings given to the program from outside the code, so **secrets never go into Git**. `.env.example` lists the names; the real values go into Vercel's dashboard.

| Variable | Local | Deployment | Purpose |
|---|---|---|---|
| `HOSTELHUB_SECRET_KEY` | optional | **required** (≥ 32 random characters) | Signs the session cookie |
| `DATABASE_URL` | leave empty | **required** (`postgresql://…`) | Hosted PostgreSQL connection string |
| `SUPABASE_URL` | leave empty | **required** (`https://<project>.supabase.co`) | Photo storage service |
| `SUPABASE_SECRET_KEY` | leave empty | **required** | Server-only key for the private bucket |
| `SUPABASE_BUCKET` | – | optional (default `complaint-photos`) | Bucket name |
| `HOSTELHUB_DEBUG` | `1` to debug | ignored | Flask debug pages |
| `HOSTELHUB_ENV` | `production` to try production rules | – | Vercel sets `VERCEL=1` and Render sets `RENDER=true` automatically; both count as production |
| `FIREBASE_CLIENT_CONFIG` | for Google login | for Google login | PUBLIC Firebase web config (JSON in single quotes) |
| `FIREBASE_SERVICE_ACCOUNT` | for Google login | for Google login | **SECRET** service-account key (JSON in single quotes). Verifies Google tokens on the server; never commit or share it |
| `HOSTELHUB_TEST_DATABASE_URL` | optional | – | Run the tests against an **empty** PostgreSQL test database |

Locally, put these in a file named **`.env`** in the project folder. `app.py` loads it with `python-dotenv`. `.env` is git-ignored and must never be committed. Without the `FIREBASE_*` values the Google button is simply hidden and password login still works.

If production is missing a required value, **the app refuses to start** and names the problem in the Vercel logs, instead of silently using the demo secret or a temporary SQLite file.

## 13. GitHub and Vercel deployment

Short version (the full step-by-step guide with troubleshooting is in **[docs/deployment.md](docs/deployment.md)**):

1. Push the repository to GitHub (the database, `.env`, photos and virtual environment are git-ignored).
2. Create a **Supabase** project: copy the *Transaction pooler* connection string (`DATABASE_URL`), create a **private** Storage bucket `complaint-photos`, copy the project URL and a secret key.
3. From your computer, create the tables and demo data **once**:
   set `DATABASE_URL` in your terminal, run `python seed.py` and type `RESET` to confirm.
4. In **Vercel**: *Add New → Project → import the GitHub repository*. Vercel detects Flask from `app.py` and `requirements.txt`; no `vercel.json` is needed.
5. Add the environment variables from section 12, deploy, then log in and change the warden password.

The Flask app **never** seeds or resets the hosted database by itself.

**Prefer Render?** The repository also contains a Render Blueprint ([`render.yaml`](render.yaml)) that runs the same app with `gunicorn`, keeping Supabase and Firebase unchanged. See [Deploying on Render](docs/deployment.md#10-deploying-on-render) for the setup, the environment variables, the Firebase authorised domain and the free-plan sleep behaviour.

## 14. Security measures

| Risk | Measure |
|---|---|
| Stolen passwords | Only salted hashes are stored (Werkzeug) |
| Student opening warden pages | Server-side role decorators on every warden route (403) |
| Changing the role in the cookie | Session holds only the user id; the role is read from the database |
| Seeing other students' data | Student queries filter by the logged-in id; others' records return 404 |
| SQL injection | All values go through placeholders, in both SQLite and PostgreSQL |
| Forged form posts (CSRF) | Random per-session token in every POST form, checked on the server (400 if wrong); `SameSite=Lax` cookie as an extra defence |
| Cookie theft on the network | `Secure` + `HttpOnly` session cookie in production |
| Malicious uploads | Extension allow-list + MIME type + real file signature check, random file names, 4 MB limit |
| Public photo URLs | Photos are private (local folder / private bucket) and only sent to the owner student or a warden; the storage key never reaches the browser |
| Leaking errors or secrets | Friendly error pages; debug off in production; secrets only in environment variables; production refuses to start with the demo secret key |

## 15. Known limitations

- **Not yet tested on a live Vercel + Supabase deployment.** PostgreSQL support was tested against a local PostgreSQL 18 server (all 91 tests pass), and the Supabase Storage calls against a fake server that follows the documented API. Check the first real deployment with the list in [docs/deployment.md](docs/deployment.md).
- **Demo passwords are public**: change them after deploying.
- **4 MB photo limit**, because Vercel rejects requests larger than 4.5 MB.
- **No college login, no password reset by email, no login rate-limiting.**
- **Needs internet** for Bootstrap, the icons and the font (CDN).
- **Search treats `%` and `_` as wildcards** (SQL `LIKE`).
- **Notifications appear on the next page load**, not in real time.
- **Times are shown in Indian Standard Time** (UTC+5:30) for every user.

## 16. Future scope

- Firebase Google sign-in limited to MES accounts
- A college-admin role to respond to maintenance requests directly
- Hostel fee and mess management
- Export reports (occupancy, complaint turnaround) to PDF/Excel
- Email or push notifications
- Complaint analytics over time

## 17. Recent Updates

- **Student Email Validation:** Updated the Warden's "Add Student" form to strictly enforce the `@student.mes.ac.in` email domain for all new student accounts.
- **Environment Variables:** Integrated `python-dotenv` into `app.py` so that local `.env` files are automatically loaded when running the Flask server locally.
