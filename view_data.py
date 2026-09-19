"""
view_data.py — Look inside the local SQLite database (READ-ONLY).

Examples (run from the project folder, with the virtual environment active):

    python view_data.py                     # every table and how many rows it has
    python view_data.py complaints          # latest 10 complaints
    python view_data.py users 25            # latest 25 users
    python view_data.py --sql "SELECT name, email FROM users WHERE role = 'warden'"
    python view_data.py --watch             # print every NEW row as the app inserts it

The database is opened in read-only mode, so this script can never change data.
Password hashes are hidden in the output.
"""

import sqlite3
import sys
import time
from pathlib import Path

DATABASE = Path(__file__).resolve().parent / "database" / "hostelhub.db"
TABLES = ("users", "rooms", "beds", "allocations", "complaints",
          "room_change_requests", "college_maintenance_requests", "notifications")
HIDDEN_COLUMNS = {"password_hash"}
MAX_WIDTH = 38          # long text (descriptions, messages) is shortened in the table


def connect_read_only():
    if not DATABASE.exists():
        sys.exit(f"No database yet at {DATABASE}. Run:  python seed.py")
    # "mode=ro" = read-only: SELECT works, INSERT/UPDATE/DELETE are refused.
    conn = sqlite3.connect(f"{DATABASE.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def shorten(value):
    text = "" if value is None else str(value).replace("\n", " ")
    return text if len(text) <= MAX_WIDTH else text[:MAX_WIDTH - 1] + "…"


def print_rows(rows):
    """Print sqlite3.Row objects as a simple text table."""
    if not rows:
        print("(no rows)")
        return
    columns = list(rows[0].keys())
    cells = [[("(hidden)" if column in HIDDEN_COLUMNS else shorten(row[column])) for column in columns]
             for row in rows]
    widths = [max(len(column), *(len(line[i]) for line in cells)) for i, column in enumerate(columns)]
    print("  ".join(column.ljust(width) for column, width in zip(columns, widths)))
    print("  ".join("-" * width for width in widths))
    for line in cells:
        print("  ".join(cell.ljust(width) for cell, width in zip(line, widths)))


def show_summary(conn):
    print(f"Database: {DATABASE}\n")
    for table in TABLES:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<30} {count:>5} rows")
    print("\nShow a table:  python view_data.py <table> [how many rows]")


def show_table(conn, table, limit):
    if table not in TABLES:
        sys.exit(f"Unknown table '{table}'. Choose one of: {', '.join(TABLES)}")
    # The table name is checked against TABLES above, so it is safe to put in the SQL text.
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    print(f"Latest {len(rows)} row(s) of {table} (newest first):\n")
    print_rows(rows)


def run_query(conn, sql):
    try:
        print_rows(conn.execute(sql).fetchall())
    except sqlite3.OperationalError as error:
        # Also shown for INSERT/UPDATE/DELETE: "attempt to write a readonly database".
        sys.exit(f"SQL error: {error}")


def watch(conn):
    """Poll every table and print rows whose id is higher than the last one we saw."""
    last_ids = {table: conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0] for table in TABLES}
    print("Watching for NEW rows... use the app in the browser. Press Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(1)
            for table in TABLES:
                rows = conn.execute(f"SELECT * FROM {table} WHERE id > ? ORDER BY id",
                                    (last_ids[table],)).fetchall()
                if rows:
                    print(f"[{time.strftime('%H:%M:%S')}] NEW in {table}:")
                    print_rows(rows)
                    print()
                    last_ids[table] = rows[-1]["id"]
    except KeyboardInterrupt:
        print("Stopped watching.")


def main(args):
    conn = connect_read_only()
    if not args:
        show_summary(conn)
    elif args[0] == "--watch":
        watch(conn)
    elif args[0] == "--sql" and len(args) > 1:
        run_query(conn, args[1])
    else:
        limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
        show_table(conn, args[0], limit)
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
