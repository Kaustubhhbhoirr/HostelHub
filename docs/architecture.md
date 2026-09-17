# HostelHub — Architecture

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
      │                                     │ SQLite hostelhub.db      │
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
| `app.py` | Creates the Flask app, seeds the database on the first run, registers the 8 blueprints, template filters (`date`, `timeago`, …), the context processor (bell count, sidebar badges) and error pages |
| `config.py` | `Config` class (paths, secret key, upload limit, cookie settings) and every choice list (categories, statuses, transitions) |
| `database.py` | `get_db()` (one connection per request in `g`), `query_all`, `query_one`, `execute`, `now_str` |
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
| `static/js/main.js` | Confirmation dialog, submit spinners, clickable rows |
| `static/js/room-map.js` | Fills the bed modal from `data-*` attributes; live search on the map |
| `static/js/complaint-form.js` | Image preview, drag and drop, quick form checks |
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
If a rule is broken (`InvalidAllocationError`) or SQLite fails (`sqlite3.Error`),
the route calls `rollback()`, so the database looks exactly as it did before.

## 5. How the allocation map gets its colours

1. `rooms.bed_map` runs one SQL query for all beds on the chosen block and floor, joining the active allocation (occupant) and the pending request (reserved for).
2. Jinja writes each bed as `<button class="bed bed-{{ bed.status }}" data-status=… data-student-name=…>`.
3. CSS turns `bed-occupied` red, `bed-available` green, and so on.
4. `room-map.js` only reads the `data-*` attributes to fill the modal. Allocate, vacate and status changes are ordinary POST forms, and the page is re-drawn from the database afterwards.

## 6. Image uploads

```
<input type=file> → POST multipart/form-data → save_complaint_image()
   checks: allowed extension · MIME starts with image/ · first bytes are a real PNG/JPEG/GIF/WEBP
   name:   uuid4().hex + extension (user's file name never used)
   saved:  uploads/complaints/<name>   (outside static/, not public)
   DB:     complaints.image_path = "<name>"
Viewing: /complaints/<id>/image → owner student or warden only → send_from_directory()
```
