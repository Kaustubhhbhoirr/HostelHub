# HostelHub — Architecture

## 0. Two ways HostelHub runs

The **same code** runs in two modes. Only the settings (environment variables) change.

```
LOCAL DEVELOPMENT                         PRODUCTION (DEPLOYMENT)

  Browser                                   Browser
     │  http://127.0.0.1:5000                  │  https://your-app.vercel.app
     ▼                                         ▼
  Flask  (python app.py)                    Vercel
     │                                         ├── CDN: public/static/* (CSS, JS, images)
     ├──▶ SQLite file                          ▼
     │    database/hostelhub.db             Flask app (app.py) as a Vercel Function
     │                                         │
     └──▶ Local folder                         ├──▶ Hosted PostgreSQL        (DATABASE_URL)
          uploads/complaints/                  │
                                               └──▶ Private Supabase Storage (SUPABASE_URL)
```

| | Local | Production |
|---|---|---|
| How it is chosen | no environment variables | `VERCEL=1` (set by Vercel) + `DATABASE_URL` + `SUPABASE_URL` |
| Database engine | SQLite (a single file, nothing to install) | PostgreSQL (a database server that keeps data permanently) |
| Complaint photos | `uploads/complaints/` | private object-storage bucket |
| Debug pages | only with `HOSTELHUB_DEBUG=1` | always off |
| Session cookie | normal | `Secure` (HTTPS only) |
| Setting up data | automatic on first run / `python seed.py` | once, by hand: `python seed.py` with `DATABASE_URL` set |

**Why is storage different?** A Vercel Function's file system is temporary and not shared between
copies of the function. A SQLite file or a photo saved there could vanish after the next deployment,
or after the function simply restarts. SQLite is perfect on a laptop; for a website that must
remember data, the database and the photos have to live in services built to keep them.

If a production setting is missing, `check_production_settings()` in `config.py` stops the app at
startup with a clear message. It never silently falls back to the demo secret key or a temporary SQLite file.

## 1. The big picture

HostelHub is a **server-rendered Flask application**. The server builds every page as HTML;
JavaScript only adds small conveniences (previews, confirmation dialogs, the bed modal).

```
┌────────────┐   GET / POST (+csrf_token)   ┌──────────────────────────┐
│  Browser   │ ───────────────────────────▶ │ Flask app (app.py)       │
│ HTML + CSS │                              │  before_request:         │
│ + small JS │                              │   1. load g.user from DB │
└────────────┘                              │   2. check CSRF on POST  │
      ▲                                     └────────────┬─────────────┘
      │ HTML page / redirect                             │ URL → blueprint route
      │                                     ┌────────────▼─────────────┐
      │                                     │ routes/*.py              │
      │                                     │  @student_required /     │
      │                                     │  @warden_required        │
      │                                     │  read + validate form    │
      │                                     └────────────┬─────────────┘
      │                                                  │ calls
      │                                     ┌────────────▼─────────────┐
      │                                     │ helpers.py (hostel rules)│
      │                                     │ database.py (SQL helpers)│
      │                                     └────────────┬─────────────┘
      │                                                  │ parameterised SQL
      │                                     ┌────────────▼─────────────┐
      │                                     │ SQLite or PostgreSQL     │
      │                                     └────────────┬─────────────┘
      │                                                  │ commit / rollback
      │                                     ┌────────────▼─────────────┐
      └──────────────────────────────────── │ flash() + redirect or    │
                                            │ render_template (Jinja)  │
                                            └──────────────────────────┘
```

## 2. Modules

| File | Responsibility |
|---|---|
| `app.py` | Creates the Flask app (the Vercel entrypoint), runs the production settings check, seeds the **local** SQLite database on the first run, registers the 8 blueprints, template filters (`date`, `timeago`, …), the context processor (bell count, sidebar badges) and error pages |
| `config.py` | `Config` class read from environment variables (secret key, `DATABASE_URL`, Supabase storage, upload limit, cookie settings), `check_production_settings()`, and every choice list |
| `database.py` | `connect()` for SQLite or PostgreSQL, `?` → `%s` conversion, `get_db()` (one connection per request in `g`), `query_all`, `query_one`, `execute`, `create_tables`, India-time `now_str` |
| `storage.py` | Complaint photos: `save_file`, `read_file`, `delete_file`, `file_exists`, using a local folder or a private Supabase bucket |
| `helpers.py` | Rules shared by several routes: notifications, `allocate_bed` / `vacate_bed` / `move_student`, reservations, image upload checks |
| `routes/auth.py` | Login, logout, loading the user, CSRF token, role decorators |
| `routes/account.py` | Notifications and profile (both roles) |
| `routes/student.py` | Student dashboard and My Room |
| `routes/warden.py` | Warden dashboard and student CRUD |
| `routes/rooms.py` | Room CRUD, allocation map, bed actions |
| `routes/complaints.py` | Complaints for both roles, protected image route |
| `routes/requests.py` | Room-change requests for both roles |
| `routes/college.py` | College maintenance requests |
| `templates/` | `base.html` layout, `_macros.html` reusable pieces, one folder per feature |
| `public/static/js/main.js` | Confirmation dialog, submit spinners, clickable rows |
| `public/static/js/room-map.js` | Fills the bed modal from `data-*` attributes; live search on the map |
| `public/static/js/complaint-form.js` | Image preview, drag and drop, quick form checks |
| `check_database.py` | Consistency checks (also run after every test) |

## 3. Role-based access flow

```
Request arrives
   │
   ├─ load_logged_in_user():  session["user_id"] → SELECT user FROM users → g.user
   │                          (not found or deactivated → session cleared)
   │
   ├─ check_csrf_token():     POST without the right token → 400
   │
   └─ route decorator
        @student_required:  no user → redirect /login ; role != 'student' → 403
        @warden_required:   no user → redirect /login ; role != 'warden'  → 403
        @login_required:    no user → redirect /login
           │
           └─ inside student routes, every query uses g.user["id"]
              → another student's record is simply not found (404)
```

The role is **never** read from the form, the URL, JavaScript or the cookie.

## 4. Transactions

Helpers such as `allocate_bed()` and `create_notification()` **never commit**.
The route performs all related steps and then calls `commit()` once.
If a rule is broken (`InvalidAllocationError`) or the database fails (`DatabaseError`, which covers
both SQLite and PostgreSQL errors), the route calls `rollback()`, so the database looks exactly as it did before.

## 5. How the allocation map gets its colours

1. `rooms.bed_map` runs one SQL query for all beds on the chosen block and floor, joining the active allocation (occupant) and the pending request (reserved for).
2. Jinja writes each bed as `<button class="bed bed-{{ bed.status }}" data-status=… data-student-name=…>`.
3. CSS turns `bed-occupied` red, `bed-available` green, and so on.
4. `room-map.js` only reads the `data-*` attributes to fill the modal. Allocate, vacate and status changes are ordinary POST forms, and the page is re-drawn from the database afterwards.

## 6. Image uploads

```
<input type=file> → POST multipart/form-data (max 4 MB) → helpers.save_complaint_image()
   checks: allowed extension · MIME starts with image/ · first bytes are a real PNG/JPEG/GIF/WEBP
   name:   uuid4().hex + extension (user's file name never used)
   saved:  storage.save_file()
             local      → uploads/complaints/<name>
             production → POST https://<project>.supabase.co/storage/v1/object/<bucket>/<name>
   DB:     complaints.image_path = "<name>"   (never the image bytes)

Viewing: <img src="/complaints/<id>/image">
   1. @login_required, then owner student or warden only (others → 404)
   2. storage.read_file()
             local      → read the file
             production → GET .../storage/v1/object/authenticated/<bucket>/<name> with the secret key
   3. Flask returns the bytes (Cache-Control: private)
```

The bucket is private and the secret key stays on the server, so the browser never receives a
storage link. Knowing a photo's address is not enough: the same owner/warden check runs in both modes.
