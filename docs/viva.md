# HostelHub — Viva Preparation

Short answers in plain language. Open the file mentioned in each answer and look at the code.

---

### 1. What is HostelHub?
A web portal for a college hostel. Students see their room, report maintenance problems and request room changes. The warden allocates beds on a visual map, handles complaints and requests, and escalates big repairs to the college.

### 2. Why Flask?
It is a small Python web framework: routes are just Python functions (`@app.route`). It gives us sessions, templates and request handling without forcing a complex structure. That suits a project whose whole team has to understand the code.

### 3. Why Firebase?
One Firebase project (`hostelhub-83310`, free Spark plan) gives us **Authentication** (email/password and Google) and **Cloud Firestore** (the database). The app on a laptop and on Vercel use the same project, so the data is live: a complaint submitted on localhost appears for the warden on the deployed site.

### 4. Why a small store interface instead of calling Firestore everywhere?
Routes call `store.get / find / insert / update / delete / commit / rollback` (`data/store.py`). All Firestore details (ids, batches, preconditions, guard documents) live in one file, `data/firestore_store.py`, so every route stays short and readable.

### 5. What is a blueprint?
A group of related routes in its own file (`routes/complaints.py`, `routes/rooms.py`, …), registered in `app.py`. It keeps the code organised instead of one huge file.

### 6. What is CRUD? Give examples.
Create, Read, Update, Delete:
- **Create:** `store.insert("users", …)` (add student), `store.insert("complaints", …)`
- **Read:** `store.find(…)` / `data/queries.py` on the dashboards, lists and map
- **Update:** `store.update("complaints", id, {"status": …})`, `store.update("beds", …)`
- **Delete:** a student with no history, draft college requests, clearing read notifications

### 7. What is a session?
A small signed cookie Flask stores in the browser. We keep **only `user_id`** in it (plus the CSRF token). It is signed with `SECRET_KEY`, so the user cannot change it without the signature breaking.

### 8. Where are passwords stored?
**Not in HostelHub.** Firebase Authentication stores and checks them. The login page signs in with the Firebase JavaScript SDK and sends our server only a signed ID token, which `routes/auth.py` verifies with the Firebase Admin SDK.

### 9. How does role-based authorization work?
1. Before every request, `load_logged_in_user()` reads the user **from Firestore** into `g.user`.
2. Warden routes have `@warden_required`, student routes `@student_required`. The decorator returns 403 if the role doesn't match.
3. The role never comes from the form, URL, JavaScript, cookie or the Firebase token. Hiding a menu link is not security; the server check is.

### 10. How does student privacy work?
Student queries always filter by the logged-in id (`g.user["id"]`). If a student types another complaint's URL, nothing that belongs to them is found, and we return **404** (so they can't even tell it exists). Roommate lists don't include phone or email.

### 11. Can someone inject commands into the database?
User text is only ever stored as a **field value** (`{"description": text}`); it is never turned into a query or command. Firestore queries in `data/firestore_store.py` compare fields with values, so text like `' OR 1=1 --` is just text.

### 12. What is CSRF and how is it handled?
Cross-Site Request Forgery: a malicious page makes your logged-in browser submit a HostelHub form (for example "approve request"). Our fix is a random token per session (`secrets.token_hex`), a hidden `csrf_token` field in every POST form, and a check in `check_csrf_token()` in `routes/auth.py` that rejects a POST with a missing or wrong token (400). `SameSite=Lax` cookies are only an extra browser defence.

### 13. How is double allocation prevented?
In **two layers**:
1. **Python:** `allocate_bed()` in `helpers.py` refuses if the bed isn't `available`, the room isn't active, or the student already has an active allocation.
2. **Firestore:** allocating must *create* the guard documents `bed-<id>` and `student-<id>` in `allocation_claims`. Firestore refuses to create a document that already exists, so if two wardens click at the same moment, only one succeeds and the other is rolled back.

### 14. What is a transaction? Why rollback?
A group of changes that must all happen or none of them. Our helper functions don't commit; the route calls `commit()` once at the end, which sends everything to Firestore as **one atomic batch**. If anything fails, `rollback()` throws the collected changes away, so the data never ends up half-updated. A change is also refused if someone else modified the same document since we read it, so two people can't overwrite each other.

### 15. What happens when a room change is approved?
In `routes/requests.py` → `warden_decide()`:
1. release the student's reserved bed if the warden chose a different bed
2. `move_student()`: end the old allocation, make the old bed available, create the new allocation, mark the new bed occupied
3. update the request to `Approved` with the assigned bed
4. create a notification for the student
5. `commit()`, or on any error `rollback()`

A test forces step 4 to fail and proves steps 1–3 are undone.

### 16. How does the room map get its colours?
`rooms.bed_map` reads the beds on the chosen block and floor from Firestore. Jinja writes each bed as a button with class `bed-{{ bed.status }}`, and CSS colours it. Nothing is hard-coded: after allocating, the page reloads and reads the new status. JavaScript only reads `data-*` attributes to fill the pop-up.

### 17. What does "reserved" (yellow) mean?
A student asked to move to that specific bed. We hold it so the warden can't give it to someone else while deciding. Approve → occupied; reject/cancel → available.

### 18. How does image upload work? Is it safe?
The form uses `enctype="multipart/form-data"`, and Flask gives us `request.files["image"]`. `save_complaint_image()` then:
- checks the extension (allow-list), the MIME type, and the **first bytes** of the file (a real PNG/JPEG/GIF/WEBP signature)
- saves it under a random `uuid4` name in `uploads/complaints/` (photos are optional; on Vercel uploads are switched off)
- stores only that name in the complaint document, never the image

Images are shown through `/complaints/<id>/image`, which sends the file only to the student who owns the complaint or a warden. Uploads are limited to 4 MB, below Vercel's 4.5 MB request limit.

### 19. How are notifications created?
`create_notification()` adds a notification document when a real event happens (complaint submitted, status changed, request approved, …), in the same commit as the event. The bell count is the number of the user's notifications with `is_read = 0`, read on each page load.

### 20. How are dashboard numbers calculated?
`data/queries.py` counts the documents (beds by status, open complaints, pending requests). Nothing is hard-coded. `percent()` returns 0 when there are no beds, so we never divide by zero.

### 21. How is complaint status validated?
`COMPLAINT_NEXT_STATUSES` in `config.py` is a dictionary of allowed moves. Resolved and Rejected map to an empty list, so they are final. Rejecting requires a remark.

### 22. Firestore has no foreign keys. How do references stay correct?
The routes check that the referenced bed, room or student exists before writing, and `check_database.py` looks for any record pointing to something that doesn't exist, and for values that aren't allowed (checks 12 and 13).

### 23. How did you test the project?
134 automated `unittest` tests with in-memory stand-ins for Firestore and Firebase Authentication, and a temporary uploads folder, so the tests never touch the live data. `check_database.py`'s 13 consistency checks run after every test. See `docs/testing.md`.

### 24. How do email/password and Google sign-in share one account?
Firebase keeps one account per email address and links both sign-in methods to it (same uid). HostelHub finds the user by email and stores that uid in `users.firebase_uid`, so a student who first used a password and later uses Google is the same HostelHub user. The accounts the warden creates are marked verified, so Firebase adds Google to them instead of replacing the password.

### 25. What are the current limitations?
Needs the internet (every page reads Firestore); no college login, email password reset or rate limiting; notifications appear on page reload, not in real time; 4 MB photo limit.

### 26. Future improvements?
A college-admin role, fee/mess management, PDF/Excel reports, email notifications, and complaint trend charts.

---

### 27. Why is the same Firebase project used locally and on Vercel?
So the demo is live: a student can submit a complaint on localhost, the warden can resolve it on the deployed site, and the student sees the new status. There is no local database and no fallback; without Firebase credentials the app refuses to start.

### 28. What are environment variables? Why use them?
Settings given to the program from outside the code: the `.env` file, the terminal, or Vercel's dashboard. Secrets (the session secret key and the Firebase service-account key) live there instead of in Git, so publishing the code does not publish the secrets. `config.py` reads them with `os.environ.get()`.

### 29. How is Flask deployed on Vercel?
Vercel detects `app.py` exporting a Flask object named `app`, installs `requirements.txt`, and runs the app as a Vercel Function. There is no `vercel.json`. CSS/JS/images in `public/static/` are served directly by Vercel's CDN. Each `git push` redeploys.

### 30. Why aren't photos in Firebase?
Firebase Cloud Storage needs the paid Blaze plan, and Firestore documents are meant for small records, not images. Photos are optional: on a laptop they are files in `uploads/complaints/`; on Vercel, whose disk is temporary, uploads are switched off instead of pretending the files are kept. Firestore stores only the file name, so a cloud image service can be added later in `storage.py` without changing the data.

### 31. How does the app stay safe from configuration mistakes?
On startup it stops if the Firebase service account is missing or broken, or if it belongs to a different Firebase project than the login page; in production also if the secret key is missing. Debug is always off in production and the cookie is `Secure`.

### 32. How does signing in work?
1. The login page signs in with the Firebase JavaScript SDK: email and password, or the Google popup.
2. Firebase gives the browser a signed **ID token**.
3. The page POSTs the token (with our CSRF token) to `/firebase-login`.
4. `authenticate_firebase()` verifies the token with the **Firebase Admin SDK** and a secret service-account key kept only on the server.
5. It checks the email ends with `@student.mes.ac.in` or `@mes.ac.in` and that the person **already has a user document** in Firestore whose role matches the email.
6. `login_user()` stores the user id in the session. The role comes from Firestore, never from the token.

### 33. Why can't anyone with an MES account log in?
Because step 5 requires a user document created by the hostel office (the warden adds students). Firebase proves *who* you are; HostelHub decides *whether you live in the hostel and what you may do*. That is also why a new `@mes.ac.in` address can't make itself a warden.

## Python concepts you can point to

| Concept | Where |
|---|---|
| functions, default arguments | `helpers.py` `allocate_bed(student_id, bed_id, allow_reserved=False)` |
| dictionaries, dict comprehension | `check_database.py` `{row["id"]: row for row in store.all("users")}` |
| lists, list comprehension | `check_database.py`, `data/queries.py` |
| tuples | choice lists in `config.py` |
| sets | `ALLOWED_IMAGE_EXTENSIONS`, `allowed_statuses` in `helpers.py` |
| string methods, f-strings | form cleaning `.strip().lower()`, messages |
| loops, `range`, `enumerate` | `data/queries.py`, `complaints.complaint_steps()` |
| user-defined exception | `InvalidAllocationError` in `helpers.py`, `ConflictError` in `data/store.py` |
| try / except / rollback | every POST route |
| decorators | `student_required`, `warden_required` in `auth.py` |
| classes, inheritance | `Store` → `FirestoreStore` in `data/` |
| file handling | image upload checks in `helpers.py` |
| modules / packages | `routes/` and `data/` packages, blueprints |
| unit testing, mocking | `tests/`, `mock.patch` in the rollback test |
