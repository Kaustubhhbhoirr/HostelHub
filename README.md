# HostelHub

**College Hostel Room Allocation and Maintenance Portal**

A Flask web portal where students check their room, report maintenance problems and ask for room changes, and the hostel warden manages students, rooms, beds, complaints and repair requests to the college.

Academic project by a team of four second-year Computer Engineering students.

HostelHub has **one backend: Firebase** (project `hostelhub-83310`). The app on a laptop and the app on Vercel use the **same** Firebase project, so the data is live and shared: a complaint a student submits on localhost is immediately visible to the warden on the deployed site, and the other way round.

| | On a laptop | Deployed |
|---|---|---|
| Web server | `python app.py` | Vercel (zero-config Flask) |
| Sign-in | Firebase Authentication (email/password + Google) | same project |
| Data | Cloud Firestore | same project |
| Complaint photos (optional) | `uploads/complaints/` on the laptop | switched off |

There is no local database and no fallback: without Firebase credentials the app refuses to start.

**Complaint photos are optional.** Firebase Cloud Storage needs the paid Blaze plan, so it is not used.
On a laptop, photos are kept in the git-ignored `uploads/complaints/` folder; on Vercel, photo uploads
are switched off (its disk is temporary) and complaints are submitted without one. Firestore stores only
the photo's generated file name, so a cloud image service can be added later in `storage.py` without
changing the complaint data.

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
| Sign-in | Firebase Authentication: email/password and Google |
| Database | Cloud Firestore, through a small store interface (`data/store.py`). No ORM |
| Photo storage | Optional: local `uploads/complaints/` folder, or switched off (`storage.py`); served only through a checked route |
| Firebase access | Firebase Admin SDK for Python (`firebase-admin`) on the server; Firebase JS SDK on the login page |
| Frontend | HTML, Bootstrap 5.3, Bootstrap Icons, custom CSS, vanilla JavaScript |
| Security | Server-side role checks, session login, CSRF tokens, Firestore preconditions and guard documents |
| Hosting | Vercel (zero-configuration Flask) |
| Testing | Python `unittest`, Flask's test client and in-memory Firebase stand-ins |

`requirements.txt` contains **Flask**, **firebase-admin** and **python-dotenv**. Bootstrap, the icon font and the web font are stored in `public/static/vendor/`.

## 6. Architecture overview

```
LOCALHOST                                  DEPLOYED
Browser                                    Browser
  │                                          │  HTTPS
Flask (python app.py)                      Vercel ── CDN serves public/static/*
  │                                          │
  │                                        Flask (Vercel Function)
  │                                          │
  └──────────────┬───────────────────────────┘
                 ▼
       Firebase project hostelhub-83310
       ├── Firebase Authentication  (who is signing in)
       └── Cloud Firestore          (all hostel data)

Complaint photos (optional): uploads/complaints/ on the laptop; switched off on Vercel
```

Every request follows the same path:

```
form / button (+ CSRF token) → Flask route (login + ROLE check on the server)
  → Python rules (helpers.py) → store (data/firestore_store.py) collects the changes
  → commit(): one atomic Firestore batch, or rollback() → flash() + redirect / render_template
```

Details: [docs/architecture.md](docs/architecture.md).

## 7. Project structure

```
HostelHub/
├── app.py              Creates the app (Vercel entrypoint), starts Firebase, blueprints, filters, error pages
├── config.py           Settings from environment variables, the Firebase web config, choice lists, startup checks
├── firebase_accounts.py  Sign-in accounts in Firebase Authentication (created/updated by the warden)
├── data/               The database layer (Cloud Firestore)
│   ├── store.py        The small interface the routes use
│   ├── firestore_store.py  Firestore: numbered ids, atomic commits, no lost updates, guard documents
│   └── queries.py      Joins and totals
├── storage.py          Optional complaint photos: local folder or switched off (one place to add a cloud service)
├── helpers.py          Shared rules: allocation, notifications, upload validation
├── check_database.py   Checks the live Firestore data for inconsistencies (13 checks)
├── requirements.txt    Flask, firebase-admin, python-dotenv
├── .python-version     Python version used by Vercel
├── .env.example        Names of the environment variables (no values)
│
├── routes/             One blueprint per feature
│   ├── auth.py         Firebase sign-in, logout, CSRF check, @student_required / @warden_required
│   ├── account.py      notifications + profile (both roles)
│   ├── student.py      student dashboard, My Room
│   ├── warden.py       warden dashboard, student CRUD
│   ├── rooms.py        room CRUD, allocation map, bed actions
│   ├── complaints.py   complaints (both roles) + protected photo route
│   ├── requests.py     room-change requests (both roles)
│   └── college.py      college maintenance requests
├── templates/          base.html, _macros.html, and one folder per feature
├── public/static/      css/, js/, images/, vendor/ (served by Vercel's CDN; by Flask locally)
├── tests/              automated tests with in-memory Firestore, Storage and Auth stand-ins
└── docs/               architecture, database, deployment, testing, viva
```

## 8. Database (Cloud Firestore)

| Collection | Purpose |
|---|---|
| `users` | Students and wardens (`role`), email, profile, `firebase_uid`. **No passwords** |
| `rooms` | Block, floor, room number, capacity, status |
| `beds` | Beds of each room; the status drives the map colours |
| `allocations` | Which student has which bed; old documents are kept as history (`ended`) |
| `complaints` | Maintenance complaints; `image_path` holds the optional photo's generated file name, never the image |
| `room_change_requests` | Current, preferred and assigned bed, status |
| `college_maintenance_requests` | Escalations to the college |
| `notifications` | Per-user messages with read/unread state |
| `counters` | Next number for each collection (so ids stay 1, 2, 3 … and CMP-0007 keeps working) |
| `allocation_claims` | Guard documents `bed-<id>` / `student-<id>`: one active allocation per bed and per student |

Double allocation is impossible even when two wardens click at the same moment: an allocation must create the guard document for the bed, and Firestore lets only one request create it. Every other change is written only if the document has not been changed by someone else since it was read. Full details: [docs/database.md](docs/database.md).

## 9. Authentication

Credentials live **only in Firebase Authentication**; HostelHub stores no passwords.

1. The login page signs in with the Firebase JavaScript SDK, by **email and password** or with **Continue with Google**.
2. Firebase gives the browser a signed **ID token**. The page sends it, plus the CSRF token, to `POST /firebase-login`.
3. `authenticate_firebase()` in `routes/auth.py` verifies the token with the **Firebase Admin SDK** (genuine, unexpired, for our project).
4. The email must end with `@student.mes.ac.in` (student) or `@mes.ac.in` (warden), and the person must already have a **user document in Firestore**. Signing in does not create an account by itself, so nobody can make themselves a warden.
5. The role is taken from the Firestore user document and must match the email domain. The session stores only the user id; the role is read again on every request.

**One account per person.** Firebase keeps one account per email address and links the password and Google sign-in methods to it. The accounts the warden creates are marked as verified, so when a student who has a password later uses Google with the same college address, Firebase adds Google to the **same** account instead of replacing it. HostelHub stores that account's uid in `users.firebase_uid` and refuses a different Firebase account claiming the same address.

There is no demo or quick login.

## 10. Workflows

**Room / bed allocation:** on the map, click a green bed → choose a student waiting for a bed → *Allocate*. The allocation document is created and the bed becomes `occupied` in one atomic Firestore write. *Vacate* ends the allocation and frees the bed.

**Maintenance:** `Submitted` → `Acknowledged` → `In Progress` → `Resolved` (or `Rejected`, which needs remarks). Every update notifies the student.

**Room change:** the student requests (the chosen bed turns yellow `reserved`) → the warden approves or rejects. On approval, in **one transaction**: old allocation ended, old bed available, new allocation created, new bed occupied, request approved, student notified. Any failure → `rollback()`.

**College maintenance:** the warden drafts or sends a repair/replacement request (type, asset, location, quantity, priority, description) and updates its status as the college responds.

**Notifications** are created by real events: new complaint and new room request (wardens); status changes, remarks, escalation, approval/rejection, allocation and vacating (the student concerned).


---

## 11. Running it on your computer

Requires **Python 3.12 or newer** and access to the Firebase project `hostelhub-83310`.

**1. Get the code and install the requirements**

```bash
git clone https://github.com/Kaustubhhbhoirr/HostelHub.git
```

```bash
cd HostelHub
```

```bash
python -m venv .venv
```

Windows PowerShell: `.venv\Scripts\Activate.ps1`. macOS / Linux: `source .venv/bin/activate`.

```bash
pip install -r requirements.txt
```

**2. Give the app the Firebase service-account key.** In the Firebase console: *Project settings → Service accounts → Generate new private key*. Then either

- save the downloaded file as `secrets/hostelhub-83310-firebase-adminsdk.json` (the `secrets/` folder is
  git-ignored and listed in `.vercelignore`) and put its path in `.env` (a relative path is read from the
  project folder): `FIREBASE_CREDENTIALS_FILE=secrets/hostelhub-83310-firebase-adminsdk.json`
- or put the whole JSON on one line in `.env`: `FIREBASE_SERVICE_ACCOUNT='{"type": "service_account", ...}'`

Copy `.env.example` to `.env` first. The public web config is already in `config.py`; leave `FIREBASE_CLIENT_CONFIG` empty. The app refuses to start if the key and the web config belong to different Firebase projects, and warns if the project is not `hostelhub-83310`.

**3. Run the tests** (they use in-memory stand-ins and never touch the real project)

```bash
python -m unittest discover tests
```

**4. Start the app**

```bash
python app.py
```

Open **http://localhost:5000** (not `127.0.0.1`: only `localhost` is an authorised domain for Google sign-in). You are working on the live data.

**5. Check the live data** at any time (read-only):

```bash
python check_database.py
```

## 12. Environment variables

`.env.example` lists the names. Locally the values go in `.env` (git-ignored); on Vercel in *Project → Settings → Environment Variables*.

| Variable | Needed | Purpose |
|---|---|---|
| `FIREBASE_SERVICE_ACCOUNT` | **always** (or the next one) | **SECRET** service-account JSON: verifies sign-in tokens, reads/writes Firestore and Storage |
| `FIREBASE_CREDENTIALS_FILE` | alternative locally | Path of the service-account JSON file |
| `HOSTELHUB_SECRET_KEY` | **production** (≥ 32 random characters) | Signs the session cookie |
| `FIREBASE_CLIENT_CONFIG` | no | PUBLIC web config; defaults to `hostelhub-83310` in `config.py` |
| `COMPLAINT_IMAGE_STORAGE` | no | `local` (default on a laptop) or `none` (default on Vercel, where `local` is refused) |
| `HOSTELHUB_DEBUG` | no | `1` for Flask debug pages locally (ignored in production) |
| `HOSTELHUB_ENV` | no | `production` to try production rules locally (Vercel sets `VERCEL=1` itself) |

## 13. Deployment on Vercel

Full guide: **[docs/deployment.md](docs/deployment.md)**. In short: import the GitHub repository in Vercel (it detects Flask from `app.py`; no `vercel.json` needed), add `FIREBASE_SERVICE_ACCOUNT` and `HOSTELHUB_SECRET_KEY`, deploy, and add the Vercel domain to *Firebase → Authentication → Settings → Authorized domains*.

## 14. Security measures

| Risk | Measure |
|---|---|
| Stolen passwords | HostelHub stores none; Firebase Authentication handles them |
| Forged sign-in | Every ID token is verified with the Firebase Admin SDK on the server |
| Self-registration as warden | Only people with a Firestore user document can sign in; the role must match the email domain |
| Student opening warden pages | Server-side role decorators on every warden route (403) |
| Changing the role in the cookie | Session holds only the user id; the role is read from Firestore |
| Seeing other students' data | Student queries filter by the logged-in id; others' records return 404 |
| Two wardens at the same moment | Guard documents for allocations; every other write only if the document is unchanged since it was read |
| Forged form posts (CSRF) | Random per-session token in every POST, checked on the server (400 if wrong); `SameSite=Lax` cookie |
| Cookie theft on the network | `Secure` + `HttpOnly` session cookie in production |
| Malicious uploads | Extension allow-list + MIME type + real file signature check, random file names, 4 MB limit |
| Public photo URLs | Photos are never in a public folder; they are sent only to the owner student or a warden |
| Leaked service-account key | Only in environment variables / git-ignored files; never sent to the browser |

## 15. Known limitations

- **Needs the internet**: every page reads Firestore.
- **Photos only on the laptop**: the deployed site has no photo uploads until a cloud image service is added, and a photo uploaded on a laptop is not visible on the deployed site (the page says so). Photos are limited to 4 MB.
- **No college login, no password reset by email, no login rate-limiting.**
- **Notifications appear on the next page load**, not in real time.
- **Times are shown in Indian Standard Time** (UTC+5:30) for every user.

## 16. Future scope

- A college-admin role to respond to maintenance requests directly
- Hostel fee and mess management
- Export reports (occupancy, complaint turnaround) to PDF/Excel
- Email or push notifications
- Complaint analytics over time
