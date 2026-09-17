"""
database.py — Opening the database connection and running queries.

HostelHub supports TWO database engines with the SAME plain SQL:

    DATABASE_URL not set  ->  SQLite file database/hostelhub.db
                              (local development and the automated tests)
    DATABASE_URL set      ->  PostgreSQL (hosted database used on Vercel)

Why two? SQLite is a single file and needs no installation, which is perfect
on your laptop. On Vercel the file system is temporary, so a real database
server is needed to keep data permanently.

How a query works (the same idea for both engines):

    connection = connect(...)                  # open the database
    cursor = connection.cursor()               # cursor = object that runs SQL
    cursor.execute(sql, params)                # run one SQL statement
    cursor.fetchone() / cursor.fetchall()      # read the result rows
    connection.commit()                        # save INSERT/UPDATE/DELETE changes
    connection.close()                         # release the connection

All queries in HostelHub are written with SQLite-style ? placeholders.
PostgreSQL's driver (psycopg) expects %s instead, so to_engine_sql()
converts them just before running the query.

Flask gives every request its own "g" object. We keep the connection in
g.db so every function in the same request shares one connection, and we
close it automatically when the request ends.
"""

import sqlite3
from datetime import datetime

from flask import current_app, g

try:
    # psycopg is only needed when DATABASE_URL points to PostgreSQL.
    import psycopg
    from psycopg.rows import dict_row
except ImportError:          # local SQLite development still works without it
    psycopg = None

# Catch these in routes instead of sqlite3.Error, so errors from BOTH engines
# are handled. They are tuples of exception classes:
#     except DatabaseError:                              (catch any of them)
#     except (InvalidAllocationError, *DatabaseError):   (* adds them to another tuple)
if psycopg is not None:
    DatabaseError = (sqlite3.Error, psycopg.Error)
    IntegrityError = (sqlite3.IntegrityError, psycopg.IntegrityError)
else:
    DatabaseError = (sqlite3.Error,)
    IntegrityError = (sqlite3.IntegrityError,)


# ------------------------------------------------------------------
# Connections (usable inside Flask AND in scripts like seed.py)
# ------------------------------------------------------------------

def connect(database_url="", sqlite_path=None):
    """Open a connection to PostgreSQL (if database_url is given) or SQLite."""
    if database_url:
        if psycopg is None:
            raise RuntimeError("DATABASE_URL is set but psycopg is not installed. Run: pip install -r requirements.txt")
        # dict_row lets us read columns by name: row["name"].
        # prepare_threshold=None: hosted connection poolers (Supabase, Neon) do not
        # support server-side prepared statements, so we switch them off.
        return psycopg.connect(database_url, row_factory=dict_row, prepare_threshold=None)

    conn = sqlite3.connect(sqlite_path)
    # Row factory lets us read columns by name: row["name"] instead of row[1].
    conn.row_factory = sqlite3.Row
    # SQLite does NOT check foreign keys unless we switch it on per connection.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def is_postgres(conn):
    return psycopg is not None and isinstance(conn, psycopg.Connection)


def to_engine_sql(conn, sql):
    """Convert ? placeholders to %s for PostgreSQL. SQLite SQL is returned unchanged.

    A literal % must be written as %% for psycopg, so it is doubled first.
    """
    if is_postgres(conn):
        return sql.replace("%", "%%").replace("?", "%s")
    return sql


def run(conn, sql, params=()):
    """Run one SQL statement on any connection and return the cursor.

    Values are always passed separately in `params` (a tuple). The driver
    inserts them safely, which prevents SQL injection.
    """
    cursor = conn.cursor()
    cursor.execute(to_engine_sql(conn, sql), params)
    return cursor


def insert(conn, sql, params=()):
    """Run an INSERT and return the id of the new row.

    SQLite reports it as cursor.lastrowid; PostgreSQL needs "RETURNING id".
    """
    if is_postgres(conn):
        return run(conn, sql + " RETURNING id", params).fetchone()["id"]
    return run(conn, sql, params).lastrowid


def create_tables(conn, schema_file):
    """Run database/schema.sql, which drops and recreates every table.

    schema.sql is written for SQLite. PostgreSQL needs only two small changes:
      - no PRAGMA statement (PostgreSQL always enforces foreign keys)
      - SERIAL instead of INTEGER PRIMARY KEY AUTOINCREMENT for auto ids
    """
    with open(schema_file, "r", encoding="utf-8") as schema:
        sql = schema.read()
    if is_postgres(conn):
        sql = sql.replace("PRAGMA foreign_keys = ON;", "")
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        conn.execute(sql)          # psycopg runs several statements when there are no parameters
    else:
        conn.executescript(sql)
    conn.commit()


# ------------------------------------------------------------------
# Helpers used by the Flask routes (one connection per request)
# ------------------------------------------------------------------

def get_db():
    """Return the database connection for the current request (open it if needed)."""
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE_URL"], current_app.config["DATABASE"])
    return g.db


def close_db(error=None):
    """Called by Flask after every request to close the connection.

    Anything not committed is discarded, like a rollback.
    """
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query_all(sql, params=()):
    """Run a SELECT and return a list of rows."""
    cursor = run(get_db(), sql, params)
    rows = cursor.fetchall()
    cursor.close()
    return rows


def query_one(sql, params=()):
    """Run a SELECT and return the first row, or None if nothing matched."""
    cursor = run(get_db(), sql, params)
    row = cursor.fetchone()
    cursor.close()
    return row


def execute(sql, params=()):
    """Run an INSERT, UPDATE or DELETE. For INSERT, return the new row id.

    This does NOT commit. The route calls get_db().commit() once all related
    changes succeed, so either everything is saved or nothing is.
    """
    if sql.lstrip().upper().startswith("INSERT"):
        return insert(get_db(), sql, params)
    run(get_db(), sql, params).close()
    return None


def now_str():
    """Current local date-time as text, e.g. '2026-09-17 15:20:00'.

    SQLite has no real DATETIME type, so we store dates as sortable text in both engines.
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_app(app):
    """Register database clean-up with the Flask app."""
    app.teardown_appcontext(close_db)
