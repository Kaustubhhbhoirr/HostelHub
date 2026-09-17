# HostelHub — Viva Preparation

Short answers in plain language. Open the file mentioned in each answer and look at the code.

---

### 1. What is HostelHub?
A web portal for a college hostel. Students see their room, report maintenance problems and request room changes. The warden allocates beds on a visual map, handles complaints and requests, and escalates big repairs to the college.

### 2. Why Flask?
It is a small Python web framework: routes are just Python functions (`@app.route`). It gives us sessions, templates and request handling without forcing a complex structure. That suits a project whose whole team has to understand the code.

### 3. Why SQLite?
SQLite keeps the whole database in one file and needs no server to install. It is part of Python (`import sqlite3`), and it still supports real SQL: foreign keys, UNIQUE constraints, indexes and transactions. It is what we use for local development and tests. The deployed website uses PostgreSQL instead (see question 27).

### 4. Why plain SQL instead of an ORM?
So we can see and explain every query in the viva. An ORM hides the SQL. Plain SQL also shows JOIN, GROUP BY, transactions and constraints directly.

### 5. What is a blueprint?
A group of related routes in its own file (`routes/complaints.py`, `routes/rooms.py`, …), registered in `app.py`. It keeps the code organised instead of one huge file.

### 6. What is CRUD? Give examples.
Create, Read, Update, Delete:
- **Create:** `INSERT INTO users` (add student), `INSERT INTO complaints`
- **Read:** `SELECT` on the dashboards, lists and map
- **Update:** `UPDATE complaints SET status…`, `UPDATE beds SET status…`
- **Delete:** `DELETE FROM users` (only a student with no history), `DELETE FROM college_maintenance_requests` (drafts), clearing read notifications

### 7. What is a session?
A small signed cookie Flask stores in the browser. We keep **only `user_id`** in it (plus the CSRF token). It is signed with `SECRET_KEY`, so the user cannot change it without the signature breaking.

### 8. Why hash passwords?
If the database is stolen, the attacker should not learn anyone's password. `generate_password_hash()` stores a salted one-way hash; at login `check_password_hash()` hashes the typed password and compares. See `routes/auth.py`.

### 9. How does role-based authorization work?
1. Before every request, `load_logged_in_user()` reads the user **from the database** into `g.user`.
2. Warden routes have `@warden_required`, student routes `@student_required`. The decorator returns 403 if the role doesn't match.
3. The role never comes from the form, URL, JavaScript or cookie. Hiding a menu link is not security; the server check is.

### 10. How does student privacy work?
Student queries always filter by the logged-in id: `WHERE student_id = ?` with `g.user["id"]`. If a student types another complaint's URL, the query finds nothing that belongs to them, and we return **404** (so they can't even tell it exists). Roommate queries don't select phone or email.

### 11. What is SQL injection and how do we prevent it?
Injection happens when user text becomes part of the SQL command, e.g. `' OR 1=1 --`. We always use `?` placeholders: `execute("SELECT * FROM users WHERE email = ?", (email,))`. sqlite3 sends the value separately, so it is treated as data, never as SQL.

### 12. What is CSRF and how is it handled?
Cross-Site Request Forgery: a malicious page makes your logged-in browser submit a HostelHub form (for example "approve request"). Our fix is a random token per session (`secrets.token_hex`), a hidden `csrf_token` field in every POST form, and a check in `check_csrf_token()` in `routes/auth.py` that rejects a POST with a missing or wrong token (400). `SameSite=Lax` cookies are only an extra browser defence, not the main protection.

### 13. How is double allocation prevented?
In **two layers**:
1. **Python:** `allocate_bed()` in `helpers.py` refuses if the bed isn't `available`, the room isn't active, or the student already has an active allocation.
2. **Database:** partial unique indexes allow only one `active` allocation per bed and per student. Even buggy code can't insert a second one; SQLite raises `IntegrityError`.

### 14. What is a transaction? Why rollback?
A transaction is a group of changes that must all happen or none of them. Our helper functions don't commit; the route calls `commit()` once at the end. If anything fails, `rollback()` undoes every change since the last commit, so the database never ends up half-updated.

### 15. What happens when a room change is approved?
In `routes/requests.py` → `warden_decide()`:
1. release the student's reserved bed if the warden chose a different bed
2. `move_student()`: end the old allocation, make the old bed available, create the new allocation, mark the new bed occupied
3. update the request to `Approved` with the assigned bed
4. create a notification for the student
5. `commit()`, or on any error `rollback()`

A test forces step 4 to fail and proves steps 1–3 are undone.

### 16. How does the room map get its colours?
`rooms.bed_map` runs a SQL query for the beds on the chosen block and floor. Jinja writes each bed as a button with class `bed-{{ bed.status }}`, and CSS colours it. Nothing is hard-coded: after allocating, the page reloads and reads the new status from the database. JavaScript only reads `data-*` attributes to fill the pop-up.

### 17. What does "reserved" (yellow) mean?
A student asked to move to that specific bed. We hold it so the warden can't give it to someone else while deciding. Approve → occupied; reject/cancel → available.

### 18. How does image upload work? Is it safe?
The form uses `enctype="multipart/form-data"`, and Flask gives us `request.files["image"]`. `save_complaint_image()` then:
- checks the extension (allow-list), the MIME type, and the **first bytes** of the file (a real PNG/JPEG/GIF/WEBP signature)
- saves the file under a random `uuid4` name through `storage.py`: locally in `uploads/complaints/` (not a public folder), on Vercel in a **private** Supabase Storage bucket
- stores only the file name in the database

Images are shown through `/complaints/<id>/image`, which sends the file only to the student who owns the complaint or a warden. Uploads are limited to 4 MB (`MAX_CONTENT_LENGTH`), below Vercel's 4.5 MB request limit.

### 19. How are notifications created?
`create_notification()` inserts a row when a real event happens (complaint submitted, status changed, request approved, …), inside the same transaction as the event. The bell count is `SELECT COUNT(*) … WHERE is_read = 0`, run on each page load.

### 20. How are dashboard numbers calculated?
With SQL: `COUNT(*)`, `GROUP BY status`, `SUM(CASE WHEN …)`. Nothing is hard-coded. `percent()` returns 0 when there are no beds, so we never divide by zero.

### 21. How is complaint status validated?
`COMPLAINT_NEXT_STATUSES` in `config.py` is a dictionary of allowed moves. Resolved and Rejected map to an empty list, so they are final. Rejecting requires a remark.

### 22. What is a foreign key? Example?
A column that must match a row in another table, e.g. `beds.room_id REFERENCES rooms(id)`. SQLite refuses a bed for a room that doesn't exist, and refuses to delete a student who still has complaints.

### 23. How did you test the project?
75 automated `unittest` tests, each on a fresh database (SQLite by default; the same tests also pass on PostgreSQL), plus manual browser workflows checked against the database, a production-mode HTTP test, a clean-install test and responsive measurements. `check_database.py` runs 11 consistency checks after every test. See `docs/testing.md`.

### 24. How would Firebase login be added?
Only `authenticate_local()` in `routes/auth.py` changes. The new version verifies the Google ID token from Firebase, checks the email ends with `@mes.ac.in`, and finds the user in our `users` table. `login_user()`, sessions, role checks and every page stay the same, and the role still comes from our database.

### 25. What are the current limitations?
It runs on the development server with a demo secret key; there's no college login, email password reset or rate limiting; Bootstrap needs internet (CDN); notifications appear on page reload, not in real time; `%` in search acts as a wildcard.

### 26. Future improvements?
Firebase Google sign-in, a college-admin role, fee/mess management, PDF/Excel reports, email notifications, and complaint trend charts.

---

### 27. Why SQLite locally but PostgreSQL on Vercel?
SQLite is a single file with nothing to install, which is ideal on a laptop. Vercel runs Flask as a function with a **temporary** file system, so a SQLite file there could be lost. A hosted PostgreSQL server keeps data permanently. The SQL is the same; `database.py` only converts `?` placeholders to `%s` and gets new ids with `RETURNING id`.

### 28. What are environment variables? Why use them?
Settings given to the program from outside the code: the terminal, or Vercel's dashboard. Secrets (the session secret key, database password, storage key) live there instead of in Git, so publishing the code does not publish the secrets. `config.py` reads them with `os.environ.get()`. Locally none are needed.

### 29. How is Flask deployed on Vercel?
Vercel detects `app.py` exporting a Flask object named `app`, installs `requirements.txt`, and runs the app as a Vercel Function. There is no `vercel.json`. CSS/JS/images in `public/static/` are served directly by Vercel's CDN. Each `git push` redeploys.

### 30. Why do uploaded photos need separate storage?
For the same reason as the database: files written inside a Vercel Function can disappear. Photos go to a **private** Supabase Storage bucket. The database stores only the file name. Our Flask route checks owner/warden first, then fetches the photo with a server-only key, so the photo never becomes public.

### 31. How does production stay safe from mistakes?
On startup, `check_production_settings()` stops the app if the secret key, `DATABASE_URL` or storage settings are missing, instead of running with the public demo key or a temporary SQLite file. Debug is always off, the cookie is `Secure`, and the hosted database is never seeded automatically (`python seed.py` asks you to type `RESET`).

## Python concepts you can point to

| Concept | Where |
|---|---|
| functions, default arguments | `database.py` `query_all(sql, params=())` |
| dictionaries, dict comprehension | `warden.py` `{row["status"]: row["total"] for row in rows}` |
| lists, list comprehension | `warden.py` `blocks = [...]` |
| tuples | choice lists in `config.py`, SQL params |
| sets | `ALLOWED_IMAGE_EXTENSIONS`, `allowed_statuses` in `helpers.py` |
| string methods, f-strings | form cleaning `.strip().lower()`, messages |
| loops, `range`, `zip`, `enumerate` | `seed.py`, `complaints.complaint_steps()` |
| user-defined exception | `InvalidAllocationError` in `helpers.py` |
| try / except / rollback | every POST route |
| decorators | `student_required`, `warden_required` in `auth.py` |
| classes | `Config` in `config.py` |
| file handling | `seed.py` reads `schema.sql`; image save/delete in `helpers.py` |
| modules / packages | `routes/` package, blueprints |
| unit testing, mocking | `tests/`, `mock.patch` in the rollback test |
