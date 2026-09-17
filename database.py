"""
database.py — Opening the SQLite connection and running queries.

How a query works with Python's built-in sqlite3 module:

    connection = sqlite3.connect("file.db")   # open the database file
    cursor = connection.cursor()              # cursor = object that runs SQL
    cursor.execute(sql, params)               # run one SQL statement
    cursor.fetchone() / cursor.fetchall()     # read the result rows
    connection.commit()                       # save INSERT/UPDATE/DELETE changes
    connection.close()                        # release the file

Flask gives every request its own "g" object. We keep the connection in
g.db so every function in the same request shares one connection, and we
close it automatically when the request ends.
"""

import sqlite3
from datetime import datetime

from flask import current_app, g


def get_db():
    """Return the database connection for the current request (open it if needed)."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        # Row factory lets us read columns by name: row["name"] instead of row[1].
        g.db.row_factory = sqlite3.Row
        # SQLite does NOT check foreign keys unless we switch it on per connection.
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(error=None):
    """Called by Flask after every request to close the connection."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query_all(sql, params=()):
    """Run a SELECT and return a list of rows.

    Values are always passed separately in `params` (a tuple). sqlite3
    replaces each ? safely, which prevents SQL injection.
    """
    cursor = get_db().cursor()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    return rows


def query_one(sql, params=()):
    """Run a SELECT and return the first row, or None if nothing matched."""
    cursor = get_db().cursor()
    cursor.execute(sql, params)
    row = cursor.fetchone()
    cursor.close()
    return row


def execute(sql, params=()):
    """Run an INSERT, UPDATE or DELETE and return the new row id (for INSERT).

    This does NOT commit. The route calls get_db().commit() once all related
    changes succeed, so either everything is saved or nothing is.
    """
    cursor = get_db().cursor()
    cursor.execute(sql, params)
    new_id = cursor.lastrowid
    cursor.close()
    return new_id


def now_str():
    """Current local date-time as text, e.g. '2026-09-17 15:20:00'.

    SQLite has no real DATETIME type, so we store dates as sortable text.
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_app(app):
    """Register database clean-up with the Flask app."""
    app.teardown_appcontext(close_db)
