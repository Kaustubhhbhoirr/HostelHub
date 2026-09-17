# HostelHub — Testing

All results below were recorded during the final audit on **17 Sep 2026** (Python 3.14, Flask 3.1.3).

## 1. Automated tests

```bash
python -m unittest discover tests -v
```

**Result: 69 tests, OK.** The suite was run several times, including twice in a row after the final code change.

How the tests work:
- `tests/base.py` builds a **fresh temporary database** for every test with `seed_database()`. The demo database is never touched.
- Requests are sent with Flask's test client and **include the CSRF token**, like real forms.
- After every test, `check_database.find_problems()` must return no problems. So every workflow also proves the data stayed consistent.

| File | Test class | What is covered |
|---|---|---|
| `test_auth_security.py` | AuthenticationTests (7) | invalid/empty/unknown login, both dashboards, case-insensitive email, logout, hashed passwords, session holds only the user id, a fake `role` in the session is ignored, deactivated user is kicked out |
| | CsrfTests (3) | POST without token → 400, wrong token → 400 with nothing changed, every POST form on key pages contains the token |
| | RouteInventoryTests (6) | **every route** as a logged-out visitor (→ /login), a student on every warden GET/POST route (→ 403), a warden on student routes (→ 403), every list page for its role (→ 200), missing records (→ 404), custom error pages without tracebacks |
| | PrivacyTests (5) | other student's complaint → 404, own lists only, can't cancel others' requests, roommates' phone numbers never shown, others' notifications → 404 |
| `test_complaints.py` | ComplaintUploadTests (9) | valid PNG (random file name, file on disk, owner can view), no image, image protection (visitor, other student, old static URL, warden), `.exe` / fake `.png` / `.svg` rejected with no file saved, missing file on disk, path traversal in the stored name, validation, student without a bed, the right notification recipients |
| | ComplaintWorkflowTests (5) | Submitted → Acknowledged → In Progress → Resolved with remarks and notifications, remark-only update, invalid transitions and statuses, reject needs remarks, student can't update status, filters and odd search text |
| `test_allocation.py` | AllocationTests (8) | normal allocation (map colour + dashboard number change), occupied / maintenance / unavailable / reserved bed, already-allocated student, invalid / warden / blank student id, invalid bed (404), deactivated student, vacate and vacate again, manual bed status rules, student blocked from bed actions, **database unique indexes on their own**, foreign keys and CHECK constraints |
| | RoomChangeTests (7) | successful approval (yellow bed shown, old bed freed, new bed occupied, one active allocation, notification), **forced failure during approval rolls everything back**, rejection, already-processed request, invalid approvals (another student's reserved bed, occupied, maintenance, missing, blank, unknown action), student request rules and cancel, vacating cancels pending request |
| `test_management.py` | StudentCrudTests (4) | create/edit/delete, 10 invalid forms (domain, duplicates, phone, year…), history blocks delete → deactivate frees bed → reactivate, search (case, partial, special characters, no results) |
| | RoomCrudTests (3) | create/duplicate/capacity up and down/inactive/delete, occupied room can't close or be deleted, capacity reduction blocked by bed history, invalid room forms, room search (`A-201`, `a201`, `201`), map block/floor navigation and invalid values |
| | CollegeRequestTests (4) | draft → edit → send → status → can't go back to Draft → can't delete sent, delete draft, escalation notifies the student, invalid forms |
| | NotificationTests (2) | open marks read and redirects, mark all read, clear read (only own), external links not followed |
| | DashboardTests (4) | `percent()` never divides by zero, every warden number matches SQL counts, all warden pages work with an **empty hostel**, student pages without a bed |
| | SeedDataTests (1) | 3 blocks, 32 rooms, 100 beds, 72 students, 5 waiting, 2 pending requests, all complaint and bed statuses present |

## 2. Route inventory (46 routes)

Role key: **P** public · **L** any logged-in user · **S** student only · **W** warden only.
Every row was requested by the automated route tests (logged-out → redirect to /login; wrong role → 403).

| Method | URL | Role | Purpose / main SQL | Template or result |
|---|---|---|---|---|
| GET | `/` | P | redirect to login or dashboard | redirect |
| GET, POST | `/login` | P | SELECT user, check hash | auth/login.html |
| POST | `/logout` | P | clear session | redirect |
| GET | `/notifications` | L | SELECT own notifications | account/notifications.html |
| GET | `/notifications/<id>/open` | L | UPDATE is_read (own only) | redirect |
| POST | `/notifications/read-all` | L | UPDATE is_read | redirect |
| POST | `/notifications/clear-read` | L | DELETE read notifications | redirect |
| GET | `/profile` | L | SELECT allocation | account/profile.html |
| POST | `/profile/phone` | L | UPDATE users.phone | redirect |
| POST | `/profile/password` | L | UPDATE password_hash | redirect |
| GET | `/complaints/<id>/image` | L (owner or warden) | SELECT image_path, send file | image / 404 |
| GET | `/student/dashboard` | S | COUNT complaints/requests, SELECT allocation | student/dashboard.html |
| GET | `/student/room` | S | SELECT beds + roommates | student/room.html |
| GET | `/student/complaints` | S | SELECT own complaints | complaints/student_list.html |
| GET, POST | `/student/complaints/new` | S | INSERT complaint + notifications | complaints/new.html |
| GET | `/student/complaints/<id>` | S (owner) | SELECT complaint | complaints/detail.html |
| GET | `/student/room-requests` | S | SELECT own requests | requests/student_list.html |
| GET, POST | `/student/room-requests/new` | S | INSERT request, UPDATE bed reserved | requests/new.html |
| POST | `/student/room-requests/<id>/cancel` | S (owner) | UPDATE request, release bed | redirect |
| GET | `/warden/dashboard` | W | many COUNT/GROUP BY queries | warden/dashboard.html |
| GET | `/warden/students` | W | SELECT with search/filters | warden/students.html |
| GET, POST | `/warden/students/new` | W | INSERT user | warden/student_form.html |
| GET | `/warden/students/<id>` | W | SELECT student, history, complaints | warden/student_detail.html |
| GET, POST | `/warden/students/<id>/edit` | W | UPDATE user | warden/student_form.html |
| POST | `/warden/students/<id>/toggle-active` | W | vacate + cancel requests + UPDATE is_active | redirect |
| POST | `/warden/students/<id>/delete` | W | DELETE user (FK may refuse) | redirect |
| GET | `/warden/rooms/` | W | SELECT rooms + bed counts | rooms/list.html |
| GET, POST | `/warden/rooms/new` | W | INSERT room + beds | rooms/form.html |
| GET, POST | `/warden/rooms/<id>/edit` | W | UPDATE room, add/remove beds | rooms/form.html |
| POST | `/warden/rooms/<id>/delete` | W | DELETE room (FK may refuse) | redirect |
| GET | `/warden/rooms/map` | W | SELECT beds + occupants for block/floor | rooms/map.html |
| POST | `/warden/rooms/beds/<id>/allocate` | W | INSERT allocation, UPDATE bed | redirect to map |
| POST | `/warden/rooms/beds/<id>/vacate` | W | UPDATE allocation + bed | redirect to map |
| POST | `/warden/rooms/beds/<id>/status` | W | UPDATE bed status (free beds only) | redirect to map |
| GET | `/warden/complaints` | W | SELECT with filters | complaints/warden_list.html |
| GET | `/warden/complaints/<id>` | W | SELECT complaint + escalation | complaints/detail.html |
| POST | `/warden/complaints/<id>/update` | W | UPDATE status/remarks + notification | redirect |
| GET | `/warden/room-requests` | W | SELECT by status | requests/warden_list.html |
| GET | `/warden/room-requests/<id>` | W | SELECT request + free beds | requests/warden_detail.html |
| POST | `/warden/room-requests/<id>/decide` | W | approve (transaction) or reject | redirect |
| GET | `/warden/college-requests/` | W | SELECT by status | college/list.html |
| GET, POST | `/warden/college-requests/new` | W | INSERT request | college/form.html |
| GET | `/warden/college-requests/<id>` | W | SELECT request | college/detail.html |
| GET, POST | `/warden/college-requests/<id>/edit` | W | UPDATE draft | college/form.html |
| POST | `/warden/college-requests/<id>/status` | W | UPDATE status + remarks | redirect |
| POST | `/warden/college-requests/<id>/delete` | W | DELETE draft only | redirect |

## 3. Manual end-to-end workflows (real browser, demo database)

Performed in the Claude desktop app's built-in browser against `python app.py`, using the real forms (CSRF tokens included). The database was checked with SQL after each workflow.

| # | Workflow | Steps | Result |
|---|---|---|---|
| A | Student complaint | login → dashboard (A-201, Bed 2) → New complaint → Fan, High, description → attached a PNG (preview appeared) → submit | ✅ CMP-0014 created, photo loaded from `/complaints/14/image`, file saved with random name, student + warden notified. `/static/uploads/...` → 404; logged-out image request → redirect to login |
| B | Warden complaint + bed | login → complaints (new one listed first) → open → In Progress + remark → map B/1 → click green bed → allocate Chinmay Menon → click red bed → Vacate → confirmation dialog → confirm | ✅ status/remarks saved, student notified, bed turned red then green immediately, allocation ended, both notifications sent |
| C | Room change | student requests B-102 Bed 3 → warden map shows it yellow, "held for Aarav Sharma" → request page (bed pre-selected) → Approve → confirmation → confirm | ✅ old A-201 Bed 2 available, B-102 Bed 3 occupied, allocation history correct, request Approved with assigned bed, student notified |
| D | College request | New request (Replacement, Ceiling Fan, Block B Room 102, qty 2, High) → Send to college → confirm → status Under Review + college remarks | ✅ saved and listed, detail fields correct, "Draft" no longer offered once sent |

After the workflows: `python check_database.py` → **all checks passed**.

Other manual checks:
- Forced a server error with debug off → friendly 500 page, no traceback or internal details in the response (traceback only in the server log).
- **Clean install:** copied the project without `.venv` or the database → new venv → `pip install -r requirements.txt` (installed only Flask and its dependencies) → `python seed.py` → `python check_database.py` → started the server → GET /login (200, CSRF field present) → POST login → reached /warden/dashboard.

## 4. Responsive / UI checks

Measured with JavaScript in the built-in browser at **1366×768** and **390×844**: horizontal overflow, elements sticking out of the page, clipped buttons, and inputs without labels.

| Pages | 1366×768 | 390×844 |
|---|---|---|
| Warden pages: dashboard, students, student detail/form, rooms, room form, map (block A, and block C floor 2 at desktop only), complaints, complaint detail, requests, request detail, college list/detail/form, notifications, profile (17 at desktop, 16 at mobile) | no overflow, nothing clipped | no overflow, nothing clipped |
| All 9 student pages (dashboard, room, complaints, new complaint, complaint detail, requests, new request, notifications, profile) | no overflow, nothing clipped | no overflow, nothing clipped |
| Map on mobile | — | room cards full width (343px), bed buttons ≥ 139×81px, bed modal fits (8px margin each side), allocate button full width |
| Mobile menu | — | hamburger visible, drawer opens (`show`) with all 9 warden links and closes |

Fixed during these checks: missing accessible labels on the student search, complaint search, map search and complaint description.

**Limitation (honest note):** the built-in browser pane was hidden during the audit, so **screenshots could not be captured** and CSS animations did not run (the drawer's slide animation was paused; with animations off, its final position is correct). Layout was verified by measurement, not by eye. **Do one visual pass on a real laptop and phone before the demo.**
