# HostelHub — Testing

```bash
python -m unittest discover tests
```

**Result (23 Sep 2026, Firebase Authentication + Firestore, optional local photos): 138 tests, OK.**

A one-off **live check** against `hostelhub-83310` (23 Sep 2026) also passed 11/11: real Firebase ID
tokens for a temporary student and warden, the student → Firestore → warden → Firestore → student
complaint workflow, role protection, notification and `check_database.py`; every temporary record and
sign-in account was deleted afterwards.

## How the tests work

HostelHub's only backend is Firebase, but automated tests must not touch the live project. Every test
therefore runs against in-memory stand-ins:

| Real service | Stand-in | File |
|---|---|---|
| Cloud Firestore | `FakeFirestore`: documents, equality queries, atomic batches with update-time preconditions, `create()` that refuses an existing document | `tests/fake_firestore.py` |
| Firebase Authentication (Admin SDK) | `FakeAuth` for account management; `verify_id_token()` replaced with the claims the test chooses | `tests/base.py` |

- Each test gets a fresh store filled with a miniature hostel from `tests/fixtures.py` (5 rooms, 7
  students, 1 warden, complaints, one pending room change). This is test material only; the
  application ships no data. Photos go to a temporary uploads folder per test.
- `self.login(email)` signs in exactly like the login page: it posts an ID token to `/firebase-login`,
  with Firebase's answer supplied by the test.
- Requests go through Flask's test client and include the CSRF token, like real forms.
- After every test `check_database.find_problems()` must report nothing, so every workflow also proves
  the data stayed consistent.
- `tests/query_helper.py` lets tests check stored documents with short SQL-style one-liners. It is test
  scaffolding; the application never uses SQL.

These stand-ins prove that HostelHub asks Firebase for the right things and handles the answers. They
do not test Firebase itself: check the live system with the list in
[deployment.md](deployment.md#5-check-the-live-system).

## What is covered

| File | Tests | Covered |
|---|---|---|
| `test_auth_security.py` | 22 | unregistered / non-college / empty sign-in refused, both dashboards, case-insensitive email, logout, no passwords stored, session holds only the user id, fake `role` in the session ignored, deactivated user logged out; CSRF; **every route** for visitors, students and wardens (redirect / 403 / 200 / 404); privacy of complaints, requests, roommates and notifications |
| `test_google_login.py` | 20 | Firebase sign-in rules: registered user signs in and the uid is linked; **password and Google give the same HostelHub account**; role comes from Firestore, not the token; unknown `@mes.ac.in` address does **not** become a warden; stored role must match the email domain; unverified Google email, invalid/expired/missing token, missing CSRF, deactivated account, address linked to another Firebase account, server without Firebase; no password or demo login on the server; login page shows only the public config |
| `test_allocation.py` | 15 | allocation rules (occupied / maintenance / unavailable / reserved bed, already-allocated or deactivated student, invalid ids), vacate, manual bed status rules, **guard documents block a second allocation on their own**, consistency check finds broken references and invalid values; room change approval, **forced failure rolls everything back**, rejection, re-processing, invalid approvals, request rules, vacating cancels a pending request |
| `test_complaints.py` | 14 | photo upload (random name, stored in the uploads folder, owner can view), no photo, photo protection, bad files rejected with nothing stored, missing photo, path traversal, validation, student without bed, notification recipients; full status workflow with remarks and notifications, invalid transitions, filters |
| `test_management.py` | 19 | student create/edit/delete **including the Firebase sign-in account** (password and email follow the edit, account deleted with the student, no orphan account if saving fails), invalid forms, history blocks delete; room CRUD and rules; college requests; notifications; dashboard numbers and an empty hostel; the repository ships no seed script or local database |
| `test_storage.py` | 9 | local photo is a file and Firestore keeps only its name, only owner and warden can view it, photo removed if the complaint cannot be saved, unwritable folder fails safely, unsafe names refused; **with photos switched off**: no photo field, the full student → warden → student complaint workflow works, a photo sent anyway is skipped and the complaint saved, a photo kept elsewhere shows "not available" |
| `test_firestore_store.py` | 12 | numbered ids, reads see the request's own changes, one atomic batch per commit, rollback; **a document changed by another request is not overwritten** (the whole batch is refused), the first read is the one that counts; **two requests cannot allocate the same bed**; claim / release rules |
| `test_deployment_config.py` | 27 | web config for `hostelhub-83310` with a complete 39-character API key and no Storage bucket, no reference to the old project, relative key path read from the project folder, key files kept out of Vercel; Firebase credentials required everywhere (no local fallback), no Storage bucket needed, photos `local` by default on a laptop and `none` on Vercel (`local` refused there), production secret key and debug rules, a service account from another Firebase project stops the app, Vercel layout (`app.py`, `public/static`, 4 MB upload limit, IST times), requirements (Flask, firebase-admin, python-dotenv only), `.env.example` names only, no Render/Supabase/PostgreSQL/SQLite/demo-login references, service-account key files are git-ignored |
