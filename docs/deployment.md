# HostelHub — Deployment Guide (GitHub + Vercel + Supabase)

This guide deploys HostelHub with:

- **Vercel** to run the Flask app (zero-configuration Flask support)
- **Supabase PostgreSQL** as the permanent database
- **Supabase Storage** (a *private* bucket) for complaint photos

> **Deploying on Render instead of Vercel?** Do steps 1, 3 and 4 below (GitHub, Supabase database, private bucket), then follow [section 10, Deploying on Render](#10-deploying-on-render).

> Never paste real passwords, keys or connection strings into this file, the README, `.env.example` or a Git commit. They only go into your terminal and the Vercel or Render dashboard.

Why not SQLite and a local folder on Vercel? Vercel runs Flask as a Function whose file system is **temporary**: anything written there can disappear at any time. The database and the photos therefore live in services that keep data permanently.

---

## 1. Prepare the GitHub repository

The repository already ignores everything that must not be published: `.venv/`, `*.db`, `.env`, uploaded photos (`uploads/complaints/*`) and `bank.md`.

Before pushing, check what Git will publish:

```bash
git status
```

```bash
git ls-files
```

There must be no `.db` file, no `.env` file and no photos in that list. Then push:

```bash
git push -u origin main
```

## 2. Required environment variables

| Name | Example shape (not real) | Where to get it |
|---|---|---|
| `HOSTELHUB_SECRET_KEY` | 64 hex characters | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | `postgresql://postgres.abcd:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres` | Supabase → **Connect** → *Transaction pooler* |
| `SUPABASE_URL` | `https://abcd.supabase.co` | Supabase → Project Settings → *Data API* / project URL |
| `SUPABASE_SECRET_KEY` | `sb_secret_...` (or the legacy `service_role` key) | Supabase → Project Settings → *API Keys* |
| `SUPABASE_BUCKET` | `complaint-photos` | the bucket you create in step 4 (optional; this is the default) |

`SUPABASE_SECRET_KEY` bypasses all Supabase security rules. It must **only** be stored in Vercel's environment variables, never in HTML, JavaScript or Git. HostelHub only uses it on the server.

If your database password contains special characters such as `@`, `#`, `/` or `:`, they must be URL-encoded inside `DATABASE_URL` (for example `@` becomes `%40`), or choose a password without them.

## 3. Create the database (Supabase)

1. Create a free account at supabase.com and a **new project**. Save the database password somewhere safe.
2. Choose a region close to your users (for India, *Mumbai*).
3. Open **Connect** and copy the **Transaction pooler** connection string (port `6543`). Replace `[YOUR-PASSWORD]` with your database password. This is your `DATABASE_URL`.
   *(The transaction pooler suits serverless apps that open short connections. HostelHub already disables prepared statements, which the pooler does not support.)*
4. Create the tables and demo data **once, from your own computer**, inside the activated virtual environment.

   Windows PowerShell:

   ```bash
   $env:DATABASE_URL = "paste-your-connection-string-here"
   ```

   macOS / Linux:

   ```bash
   export DATABASE_URL="paste-your-connection-string-here"
   ```

   Then create the tables and demo data (type `RESET` when asked):

   ```bash
   python seed.py
   ```

   Check that the data is consistent:

   ```bash
   python check_database.py
   ```

   Expected: `PostgreSQL database is consistent: all 11 checks passed.`

5. Close that terminal (or clear the variable) so later local commands use SQLite again.

⚠️ `python seed.py` **deletes everything** in the database it points to. Only run it against production to set it up the first time, or to deliberately reset a demo. The deployed app never runs it.

## 4. Create the private photo bucket (Supabase Storage)

1. In Supabase open **Storage → New bucket**.
2. Name it `complaint-photos`.
3. Leave **Public bucket turned OFF**. The bucket must stay private.
4. Optional but recommended: set the bucket's allowed MIME types to `image/png, image/jpeg, image/gif, image/webp` and the file size limit to `4 MB`.
5. Copy the **project URL** (`SUPABASE_URL`) and a **secret key** (`SUPABASE_SECRET_KEY`).

No storage policies are needed. HostelHub's server uses the secret key, and the browser never talks to Supabase directly. Every photo is sent through `/complaints/<id>/image`, which first checks that the viewer is the complaint's student or a warden.

## 5. Create the Vercel project

1. Sign in at vercel.com with your GitHub account.
2. **Add New → Project** and **Import** the `HostelHub` repository.
3. Vercel detects **Flask** from `app.py` (which exports `app`) and `requirements.txt`. Leave the framework preset, build command and output settings at their defaults. **No `vercel.json` is needed.**
4. Before deploying, open **Environment Variables** and add the five variables from step 2 (for *Production*, and *Preview* if you use preview deployments).
5. Recommended: in **Settings → Functions**, set the function region close to your Supabase region (for example Mumbai `bom1`) so database queries are fast.

What Vercel uses from the repository:
- `app.py` → the Flask app (a single Vercel Function)
- `requirements.txt` → Flask and psycopg
- `.python-version` → Python 3.14
- `public/static/` → CSS, JavaScript and images served from Vercel's CDN at `/static/...`
- `templates/`, `database/schema.sql` and the Python files → bundled with the function

## 6. Deploy

Click **Deploy**. After the first deployment, every `git push` to `main` deploys automatically.

If the deployment starts but every page shows an error, open **Deployment → Logs**. HostelHub stops on purpose with a clear message when a required variable is missing, for example:

```
RuntimeError: HostelHub production configuration error: DATABASE_URL must point to a hosted PostgreSQL database.
```

## 7. Test the production site

Work through this list on the live URL:

1. `/login` loads with the HostelHub styling (CSS from `/static/css/style.css`).
2. Log in as the **warden**, then immediately go to **Profile → Change password** (the demo password is public).
3. Warden dashboard numbers match the demo data (100 beds, 73 students).
4. **Allocation map**: switch blocks and floors, click a green bed, allocate a waiting student (the bed turns red), then vacate it (it turns green).
5. Log in as the **student** (change this password too), report a complaint **with a photo**, and open the complaint: the photo appears.
6. Copy the photo address (`/complaints/<id>/image`) and open it in a private or incognito window: you must be sent to the login page, not shown the photo.
7. In Supabase **Storage → complaint-photos**, the file exists. The bucket still shows as **private**.
8. Request a room change as the student (the chosen bed turns yellow on the map), approve it as the warden, and check the student's new room and notification.
9. Redeploy (or wait a day) and confirm the complaint, the photo and the room change are still there. This proves the storage is persistent.
10. Visit a random URL such as `/does-not-exist`: the HostelHub 404 page appears, not a stack trace.

## 8. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Every page returns an error; logs show `HostelHub production configuration error` | A required variable is missing or wrong. Read the message, fix it in Vercel → Settings → Environment Variables, then **redeploy** (variables only apply to new deployments). |
| `password authentication failed` or `could not translate host name` | `DATABASE_URL` is wrong: check the password (URL-encode special characters) and that it is the *Transaction pooler* string. |
| `relation "users" does not exist` | The tables were never created. Run `python seed.py` with `DATABASE_URL` set (step 3). |
| `prepared statement ... already exists` | Should not happen (HostelHub disables prepared statements). Make sure you deployed the latest code. |
| Photo upload says "could not be saved"; logs show `HTTP Error 400/401/403` | Check `SUPABASE_URL`, `SUPABASE_SECRET_KEY` and that a bucket named exactly `SUPABASE_BUCKET` exists. If a new `sb_secret_...` key is rejected, try the legacy `service_role` key from the same *API Keys* page. |
| Upload of a large photo fails with 413 | Photos must be under 4 MB (Vercel limits request bodies to 4.5 MB). |
| Login works but you are logged out immediately | The site must be opened over **https://** (the session cookie is `Secure` in production). Also make sure `HOSTELHUB_SECRET_KEY` does not change between deployments. |
| Page has no styling | `public/static/` was not deployed. Check it exists in the GitHub repository. |
| Times look 5½ hours off | Old code. HostelHub stores Indian Standard Time; redeploy the latest commit. |

## 9. What is and is not verified

| Item | Status |
|---|---|
| Local SQLite mode | Verified: 110 automated tests, seed, consistency check, browser checks |
| PostgreSQL support | Verified against a local PostgreSQL 18 server: all 91 tests, seed and consistency check |
| Production mode (`VERCEL=1`) with PostgreSQL | Verified locally with a real HTTP server: 24 end-to-end checks, Secure cookie, debug off |
| Supabase Storage calls | Verified against a fake server that follows the documented API, **not** against real Supabase |
| Vercel Flask configuration | Follows the current Vercel Flask documentation (zero-config `app.py`, `public/` for static files); **not deployed yet** |
| Real Vercel + Supabase deployment | **Not done yet**: follow steps 3–7 and run the checklist in step 7 |
| Render configuration (`render.yaml`, `RENDER=true` production check) | The production check is covered by automated tests. `gunicorn` itself was **not** run: it does not work on Windows (needs `fcntl`), so only the WSGI app was served and checked locally. **Not deployed yet**: follow section 10 |

---

## 10. Deploying on Render

HostelHub can run on [Render](https://render.com) instead of Vercel. Supabase (database + private photo bucket) and Firebase (Google login) stay exactly as they are; only the place where Flask runs changes. The repository contains a Render Blueprint, [`render.yaml`](../render.yaml), that describes the whole web service.

Why Supabase is still needed: a Render web service also has a **temporary** file system, so SQLite files and uploaded photos would disappear on every deploy or restart.

### 10.1 Create the service from the Blueprint

1. Complete steps 1, 3 and 4 above (repository on GitHub, Supabase tables created once, private `complaint-photos` bucket).
2. In the Render dashboard choose **New → Blueprint** and connect the GitHub repository.
3. Render reads `render.yaml` and shows one web service, `hostelhub` (Python, free plan, Singapore region, branch `main`). It asks for a value for each variable marked `sync: false`. Fill them in as described below, then click **Apply**.
4. Render runs `pip install -r requirements.txt` and starts `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`. It treats the deploy as healthy once `/login` answers.

Later `git push` to `main` redeploys automatically.

### 10.2 Environment variables

| Variable | Who provides it | Value |
|---|---|---|
| `PYTHON_VERSION` | `render.yaml` | `3.12.8` |
| `HOSTELHUB_ENV` | `render.yaml` | `production` |
| `HOSTELHUB_SECRET_KEY` | Render generates it | random value, kept between deploys |
| `SUPABASE_BUCKET` | `render.yaml` | `complaint-photos` |
| `FIREBASE_CLIENT_CONFIG` | **you paste it** | from your local `.env` (see the quote warning below) |
| `FIREBASE_SERVICE_ACCOUNT` | **you paste it** | from your local `.env` (see the quote warning below) |
| `DATABASE_URL` | **you paste it** | Supabase *pooler* connection string (see below) |
| `SUPABASE_URL` | **you paste it** | Supabase project URL, `https://<project>.supabase.co` |
| `SUPABASE_SECRET_KEY` | **you paste it** | Supabase → Project Settings → *API Keys* |

Render also sets `RENDER=true` on its own. HostelHub treats that like production, so on Render the app **refuses to start** (and names the missing variable in the logs) instead of silently running in development mode with the demo secret key or a temporary SQLite file.

The local `.env` normally holds only the two Firebase values, because local development uses SQLite and local photo storage. `DATABASE_URL`, `SUPABASE_URL` and `SUPABASE_SECRET_KEY` come from the Supabase dashboard (step 2 above).

**Paste the Firebase JSON without the surrounding single quotes.** In `.env` (and `.env.example`) the JSON is wrapped in `'...'` so the `.env` parser reads it as one value. Render's environment-variable box takes the text literally, so the quotes would become part of the value and the JSON would fail to parse. Paste only the object, from the opening `{` to the closing `}`:

```
{"type": "service_account", "project_id": "...", ...}
```

Do the same for `FIREBASE_CLIENT_CONFIG`. If Google login fails and the Render logs say `Failed to initialize Firebase Admin SDK`, a stray quote or a broken line break in the pasted JSON is the usual cause.

**Use the Supabase pooler connection string for `DATABASE_URL`.** In Supabase open **Connect** and copy the *Transaction pooler* string. Its host looks like `aws-0-<region>.pooler.supabase.com` and its user like `postgres.<project-ref>`. Do **not** use the *Direct connection* host `db.<project-ref>.supabase.co`: it is IPv6-only, and Render cannot reach it, so the app would fail with `could not translate host name` or a connection timeout.

### 10.3 Firebase: authorise the Render domain

Google sign-in only works on domains that Firebase knows. In the Firebase console open **Authentication → Settings → Authorized domains → Add domain** and add your Render address, for example `hostelhub.onrender.com` (copy the exact host from the top of the service page in Render; it can have a random suffix if the name was taken). Without it the Google popup fails with `auth/unauthorized-domain`.

### 10.4 Free plan: the service goes to sleep

On Render's free plan the service is **spun down after about 15 minutes without traffic**. The first request after that wakes it up and can take up to a minute; the page then loads normally. This is normal behaviour, not an error. Paid plans stay awake. Free services also have a monthly limit of running hours, so an app that is never idle can be suspended until the next month.

### 10.5 Render troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Deploy fails or restarts; logs show `HostelHub production configuration error` | A required variable is empty. Read the message, set the variable in **Render → Environment**, and save (Render redeploys). |
| Logs show `could not translate host name` / connection timeout | `DATABASE_URL` uses the IPv6-only `db.<ref>.supabase.co` host. Switch to the pooler string (10.2). |
| `Failed to initialize Firebase Admin SDK` in the logs | `FIREBASE_SERVICE_ACCOUNT` was pasted with surrounding quotes or a changed line break (10.2). |
| Google popup: `auth/unauthorized-domain` | The `onrender.com` domain is missing from Firebase Authorized domains (10.3). |
| `relation "users" does not exist` | The Supabase tables were never created. Do step 3 once from your own computer; Render never seeds the database. |
| First page load is very slow | The free instance was asleep (10.4). |

Everything from sections 7 and 8 (the production test list and the general troubleshooting) also applies on Render.
