"""
data/ — HostelHub's database layer (Cloud Firestore).

    get_store()   the Firestore store for the current request
    now_str()     the current date and time in India, as stored text

Firestore is the only database. The same Firebase project is used when the app
runs on a laptop and when it runs on Vercel, so data entered locally is live for
everybody. There is no local database and no fallback: if Firebase cannot be
reached, the request fails instead of quietly writing somewhere else.

Routes and helpers only use the store interface in data/store.py and the joined
views in data/queries.py.
"""

from datetime import datetime, timedelta, timezone

from flask import current_app, g

from data.store import DuplicateError, Store, StoreError

# Routes catch this around every database write.
DatabaseError = (StoreError,)
IntegrityError = (DuplicateError,)


def create_store(settings=None):
    """Open a new Firestore store. `settings` is accepted for symmetry with Flask config."""
    from data.firestore_store import FirestoreStore
    return FirestoreStore()


def get_store():
    """The store for the current request (opened once, closed when the request ends)."""
    if "store" not in g:
        g.store = create_store(current_app.config)
    return g.store


def close_store(error=None):
    """Called by Flask after every request. Uncommitted changes are dropped."""
    store = g.pop("store", None)
    if store is not None:
        store.close()


def init_app(app):
    app.teardown_appcontext(close_store)


# ------------------------------------------------------------------
# Dates
# ------------------------------------------------------------------
# The hostel is in India. Cloud servers usually run on UTC time, so we always
# convert to Indian Standard Time (UTC + 5:30, no daylight saving).
INDIA_TIME = timezone(timedelta(hours=5, minutes=30), "IST")


def local_now():
    """Current date-time in India, without timezone info (matches the stored text)."""
    return datetime.now(INDIA_TIME).replace(tzinfo=None)


def now_str():
    """Current India date-time as sortable text, e.g. '2026-09-23 15:20:00'."""
    return local_now().strftime("%Y-%m-%d %H:%M:%S")
