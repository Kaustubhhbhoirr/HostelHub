# HostelHub — Setup and Deployment (Firebase + Vercel)

HostelHub has one backend, the Firebase project **`hostelhub-83310`** (free Spark plan):

- **Firebase Authentication**: email/password and Google sign-in
- **Cloud Firestore**: all hostel data

**Complaint photos are optional.** Firebase Cloud Storage needs the paid Blaze plan, so it is not used.
On a laptop, photos are kept in the git-ignored `uploads/complaints/` folder; on Vercel, photo uploads
are switched off (its disk is temporary) and complaints are submitted without one. Firestore stores only
the photo's generated file name, so a cloud image service can be added later in `storage.py` without
changing the complaint data.

The app on a laptop (`python app.py`) and the app on **Vercel** use this same project, so the data is
live and shared between them. There is no local database and no fallback: without the Firebase
service-account key the app refuses to start.

> Never paste the service-account JSON into this file, the README, `.env.example` or a Git commit. It
> belongs in your local `.env` (git-ignored), a key file outside Git, and the Vercel dashboard only.

---

## 1. Firebase console (once)

1. **Authentication → Sign-in method:** enable **Email/Password** and **Google**.
2. **Authentication → Settings → User account linking:** keep **"Link accounts that use the same email"**
   (the default). This is what makes a student's password and Google sign-in one account.
3. **Firestore Database:** create it in production mode (for India, `asia-south1`).
   Storage is **not** needed; do not upgrade to Blaze.
4. **Project settings → Service accounts → Generate new private key.** This downloads the secret JSON.
   It gives full access to the project: keep it outside the repository (or in `secrets/`, which is
   git-ignored) and never share it.

**Security rules** can stay closed to browsers. HostelHub never reads Firestore from JavaScript;
everything goes through the server with the service account. Recommended rules:

```
// Firestore
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /{document=**} { allow read, write: if false; }
  }
}
```

The public web config (apiKey, authDomain, …) is already in `config.py`. It is designed to be public.

## 2. Local setup

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set **one** of:

```
FIREBASE_CREDENTIALS_FILE=secrets/hostelhub-83310-firebase-adminsdk.json
FIREBASE_SERVICE_ACCOUNT='{"type": "service_account", "project_id": "hostelhub-83310", ...}'
```

Leave `FIREBASE_CLIENT_CONFIG` empty (the `hostelhub-83310` default is used) and
`COMPLAINT_IMAGE_STORAGE` empty (photos go to `uploads/complaints/`). If `FIREBASE_CLIENT_CONFIG` is
set, it must belong to the same project as the key: the app refuses to start when they differ, and logs
a warning when the project is not `hostelhub-83310`.

```bash
python app.py
```

Open `http://localhost:5000`. `localhost` is an authorised domain by default; `127.0.0.1` is not.

## 3. Create the hostel data

The repository contains **no** sample data. The real users, rooms and beds are created once in Firebase
with a temporary script that is deleted afterwards. What that script must respect:

| Rule | Why |
|---|---|
| Write through `FirestoreStore` (`data/firestore_store.py`): `store.insert(...)` then `store.commit()` | `insert()` takes the id from the `counters` collection, so the app's own inserts later continue at the next number |
| Documents follow the fields the app uses (see [database.md](database.md)) | e.g. `users.is_active` is `1`/`0`, times are `"YYYY-MM-DD HH:MM:SS"` in India time (`data.now_str()`) |
| Each room gets exactly `capacity` beds | checked by `check_database.py` |
| For every **active** allocation: `store.claim(f"bed-{bed_id}")` and `store.claim(f"student-{student_id}")`, and the bed's status is `occupied` | the guard documents are what prevent double allocation |
| Every user document's `role` matches its email: `@student.mes.ac.in` → `student`, `@mes.ac.in` → `warden` | sign-in is refused otherwise |
| Create each person's sign-in account in Firebase Authentication, e.g. with `firebase_accounts.create_account(email, password, name)`, and store the returned uid as `firebase_uid` (or leave `firebase_uid` empty; it is filled on first sign-in) | the account is created with a verified email, so adding Google later links to it instead of replacing the password |

A person who should only use Google needs just the Firestore user document; no password account is
required. Afterwards, check the data:

```bash
python check_database.py
```

After that, students are added by the warden in the app (**Students → Add student**), which creates both
the Firestore document and the Firebase sign-in account.

## 4. Deploy on Vercel

1. Push the repository to GitHub. `.env`, `secrets/`, key files, `.venv/` and `bank.md` are git-ignored;
   check with `git status` that no key file is listed. `.vercelignore` also keeps them out of CLI deploys.
2. In [vercel.com](https://vercel.com): **Add New → Project** and import the repository. Vercel detects
   Flask from `app.py` by itself: no build command and no `vercel.json`. Static files in `public/` are
   served by its CDN.
3. **Settings → Environment Variables** (Production and Preview):

   | Name | Value |
   |---|---|
   | `FIREBASE_SERVICE_ACCOUNT` | the whole service-account JSON (paste it without surrounding quotes) |
   | `HOSTELHUB_SECRET_KEY` | output of `python -c "import secrets; print(secrets.token_hex(32))"` |

4. **Deploy.** If a variable is missing, the function stops on purpose and the log names it.
5. **Firebase console → Authentication → Settings → Authorized domains → Add domain:** add the Vercel
   domain (for example `hostelhub.vercel.app`). Without it, Google sign-in shows "This website address is
   not allowed in Firebase".

## 5. Check the live system

| # | Check | Expected |
|---|---|---|
| 1 | Open `/login` on Vercel and on localhost | Google button and email/password form, no demo buttons |
| 2 | Student signs in on localhost and submits a complaint (photo optional) | the complaint appears |
| 3 | Warden signs in on Vercel | the same complaint is listed (a laptop photo shows as "not available" there) |
| 4 | Warden resolves it with a remark | the student sees *Resolved* and the remark after reloading, on either site |
| 5 | Allocate a bed, then try to allocate the same bed from a second browser | the second attempt is refused |
| 6 | Open a photo URL while logged out | redirected to the login page |
| 7 | `python check_database.py` | all 13 checks pass |

## 6. Troubleshooting

| Symptom | Cause |
|---|---|
| `HostelHub configuration error: FIREBASE_SERVICE_ACCOUNT ... must be set` | no key in `.env` / Vercel |
| `could not start Firebase` | the JSON is broken (often a copy-paste problem with the `private_key` line breaks) |
| `the service account belongs to Firebase project 'X' but the web config is for 'Y'` | old key or old `FIREBASE_CLIENT_CONFIG` in `.env`; use the `hostelhub-83310` key and clear `FIREBASE_CLIENT_CONFIG` |
| `... is not registered in HostelHub` on sign-in | the person has no Firestore user document yet |
| `This account is not set up correctly` | the user document's `role` does not match the email domain |
| "Please try again" after a save | someone else changed the same record at the same moment; nothing was saved, retry |
