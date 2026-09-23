# HostelHub — Architecture

## 0. One backend: Firebase

HostelHub runs on a laptop (`python app.py`) and on Vercel with the **same code and the same Firebase
project** (`hostelhub-83310`). Both read and write the same live data.

```
LOCALHOST                                  DEPLOYED

  Browser                                    Browser
     │  http://localhost:5000                   │  https://<app>.vercel.app
     ▼                                          ▼
  Flask  (python app.py)                     Vercel ── CDN: public/static/*
     │                                          │
     │                                       Flask app (app.py) as a Vercel Function
     │                                          │
     └──────────────────┬───────────────────────┘
                        ▼   Firebase Admin SDK (service account, server only)
            Firebase project hostelhub-83310
            ├── Authentication  (email/password + Google; verifies ID tokens)
            └── Cloud Firestore (all hostel data)
```

Complaint photos are optional and are **not** in Firebase (Cloud Storage needs the paid Blaze plan).
`storage.py` chooses the backend with `COMPLAINT_IMAGE_STORAGE`: `local` keeps them in
`uploads/complaints/` (default on a laptop), `none` switches uploads off (default on Vercel, whose disk
is temporary; `local` is refused there). A cloud image service can be added later as one more backend
class; the complaint document keeps only the photo's generated name either way.

| | Localhost | Vercel |
|---|---|---|
| Firebase project | `hostelhub-83310` | `hostelhub-83310` |
| Credentials | `.env` (`FIREBASE_SERVICE_ACCOUNT` or `FIREBASE_CREDENTIALS_FILE`) | Vercel environment variables |
| Debug pages | only with `HOSTELHUB_DEBUG=1` | always off |
| Session cookie | normal | `Secure` (HTTPS only) |

There is no local database and no fallback. `check_production_settings()` in `config.py` and
`init_firebase()` in `routes/auth.py` stop the app at startup with a clear message if the service
account is missing or broken, if it belongs to a different project than the login page's web config,
or (in production) if the secret key is missing.

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
      │                                     │ data/ (Firestore store)  │
      │                                     └────────────┬─────────────┘
      │                                                  │ Admin SDK
      │                                     ┌────────────▼─────────────┐
      │                                     │ Cloud Firestore          │
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
| `app.py` | Creates the Flask app (the Vercel entrypoint), runs the settings check, starts Firebase, registers the 8 blueprints, template filters (`date`, `timeago`, …), the context processor (bell count, sidebar badges) and error pages |
| `config.py` | The public Firebase web config, `Config` read from environment variables (secret key, Firebase credentials, upload limit, cookie settings), `check_production_settings()`, and every choice list |
| `firebase_accounts.py` | Sign-in accounts in Firebase Authentication: create, update, enable/disable, delete (used by the warden's student pages) |
| `data/store.py` | The store interface the routes use: `get`, `find`, `insert`, `update`, `delete`, `claim`, `commit`, `rollback` |
| `data/firestore_store.py` | Cloud Firestore: numbered ids, one atomic batch per request, update-time preconditions, guard documents |
| `data/queries.py` | Joins and totals in Python |
| `data/__init__.py` | `get_store()` (one store per request in `g`), India-time `now_str()` |
| `storage.py` | Optional complaint photos behind one interface (`LocalFolderStorage`, `NoImageStorage`): `images_enabled`, `save_file`, `read_file`, `delete_file`, `file_exists` |
| `helpers.py` | Rules shared by several routes: notifications, `allocate_bed` / `vacate_bed` / `move_student`, reservations, image upload checks |
| `routes/auth.py` | Firebase sign-in (`/firebase-login`), logout, loading the user, CSRF token, role decorators |
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
   ├─ load_logged_in_user():  session["user_id"] → users/<id> in Firestore → g.user
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
If a rule is broken (`InvalidAllocationError`) or Firestore fails (`DatabaseError`), the route calls
`rollback()`, so the database looks exactly as it did before: the changes are not even sent until
`commit()`, which writes them as one atomic batch.

Two requests at the same moment cannot overwrite each other. Allocating a bed must create the guard
documents `bed-<id>` and `student-<id>` (only one request can create them), and every other change is
written only if the document is unchanged since it was first read in the request. If it was changed,
the batch is refused (`ConflictError`), nothing is saved, and the user is asked to try again.

## 5. How the allocation map gets its colours

1. `rooms.bed_map` reads all beds on the chosen block and floor (`data/queries.py`) together with the active allocation (occupant) and the pending request (reserved for).
2. Jinja writes each bed as `<button class="bed bed-{{ bed.status }}" data-status=… data-student-name=…>`.
3. CSS turns `bed-occupied` red, `bed-available` green, and so on.
4. `room-map.js` only reads the `data-*` attributes to fill the modal. Allocate, vacate and status changes are ordinary POST forms, and the page is re-drawn from the database afterwards.

## 6. Image uploads

```
<input type=file> → POST multipart/form-data (max 4 MB) → helpers.save_complaint_image()
   checks: allowed extension · MIME starts with image/ · first bytes are a real PNG/JPEG/GIF/WEBP
   name:   uuid4().hex + extension (user's file name never used)
   saved:  storage.save_file() → uploads/complaints/<name>   (uploads off on Vercel: complaint saved without photo)
   DB:     complaints.image_path = "<name>"   (never the image bytes)
           if saving the complaint fails, the uploaded photo is deleted again

Viewing: <img src="/complaints/<id>/image">
   1. @login_required, then owner student or warden only (others → 404)
   2. storage.read_file() → reads the file; missing here → 404 and the page says "no longer available"
   3. Flask returns the bytes (Cache-Control: private)
```

The uploads folder is not a public folder, so the browser never receives a direct file link. Knowing a photo's address is not enough: the owner/warden check always runs first.
