"""
tests/query_helper.py — Let the tests ask short SQL-style questions about the data.

The application talks to Firestore through data/store.py and never writes SQL.
The tests, however, only need to *look* at the stored documents ("how many beds
are occupied?", "what is the status of this request?"), and SQL-style one-liners
keep those checks short and readable.

This helper understands the small subset used in the tests:

    SELECT * | col, col | COUNT(*) FROM collection
        [JOIN other ON other.a = collection.b]
        [WHERE col = ? AND col != 'x' AND col IS NULL AND col IN (?, ?)]
        [ORDER BY col [DESC]] [LIMIT n]
    UPDATE collection SET col = ?, col = ? [WHERE ...]
    INSERT INTO collection (col, col) VALUES (?, ?)
    DELETE FROM collection [WHERE ...]

It is test scaffolding only: no application code uses it.
"""

import re

CONDITION = re.compile(r"""\s*(?:(?P<table>\w+)\.)?(?P<column>\w+)\s*
                           (?:(?P<op>=|!=|<>)\s*(?P<value>\?|'[^']*'|-?\d+)
                              |(?P<null>IS\s+NOT\s+NULL|IS\s+NULL)
                              |IN\s*\((?P<list>[^)]*)\))""", re.I | re.X)


def literal(text, params):
    """Turn '?' into the next parameter, or read a quoted string / number."""
    text = text.strip()
    if text == "?":
        return params.pop(0)
    if text.startswith("'"):
        return text.strip("'")
    return int(text)


def split_conditions(where):
    return [part for part in re.split(r"\s+AND\s+", where, flags=re.I) if part.strip()]


def matches(row, where, params):
    """True if the row satisfies every condition of the WHERE clause."""
    for part in split_conditions(where or ""):
        found = CONDITION.match(part.strip())
        assert found, f"unsupported condition: {part}"
        value = row.get(found["column"])
        if found["null"]:
            wanted_null = found["null"].upper().replace(" ", "") == "ISNULL"
            if (value is None) != wanted_null:
                return False
        elif found["list"] is not None:
            options = [literal(item, params) for item in found["list"].split(",")]
            if value not in options:
                return False
        else:
            expected = literal(found["value"], params)
            if found["op"] == "=" and value != expected:
                return False
            if found["op"] in ("!=", "<>") and value == expected:
                return False
    return True


def run(store, sql, params=()):
    """Run one statement. Returns a list of dictionaries for SELECT, else None."""
    sql = " ".join(sql.split())
    params = list(params)
    command = sql.split(" ", 1)[0].upper()
    if command == "SELECT":
        return select(store, sql, params)
    if command == "UPDATE":
        return update(store, sql, params)
    if command == "INSERT":
        return insert(store, sql, params)
    if command == "DELETE":
        return delete(store, sql, params)
    raise AssertionError(f"unsupported statement: {sql}")


def parse_tail(sql):
    """Split '... WHERE x ORDER BY y LIMIT n' into its parts."""
    where = order = limit = None
    found = re.search(r"\bLIMIT\s+(\d+)\s*$", sql, re.I)
    if found:
        limit = int(found[1])
        sql = sql[:found.start()]
    found = re.search(r"\bORDER\s+BY\s+(.+)$", sql, re.I)
    if found:
        order = found[1].strip()
        sql = sql[:found.start()]
    found = re.search(r"\bWHERE\s+(.+)$", sql, re.I)
    if found:
        where = found[1].strip()
        sql = sql[:found.start()]
    return sql.strip(), where, order, limit


def select(store, sql, params):
    head, where, order, limit = parse_tail(sql)
    found = re.match(r"SELECT\s+(?P<columns>.+?)\s+FROM\s+(?P<table>\w+)(?P<joins>(?:\s+JOIN\s+.+?)*)$",
                     head, re.I)
    assert found, f"unsupported query: {sql}"
    table = found["table"]
    rows = [dict(row) for row in store.all(table)]

    # JOIN: copy the other collection's fields into the row (tests use simple key joins).
    for join in re.finditer(r"JOIN\s+(?P<other>\w+)\s+ON\s+(?P<left>[\w.]+)\s*=\s*(?P<right>[\w.]+)", head, re.I):
        other = {row["id"]: row for row in store.all(join["other"])}
        left, right = join["left"].split("."), join["right"].split(".")
        key = right[1] if left[0] == join["other"] else left[1]
        joined = []
        for row in rows:
            match = other.get(row.get(key))
            if match:
                merged = {**match, **row}
                merged[f"{join['other']}_id"] = match["id"]
                joined.append(merged)
        rows = joined

    rows = [row for row in rows if matches(row, where, list(params))]

    if order:
        for part in reversed([item.strip() for item in order.split(",")]):
            name = part.split()[0].split(".")[-1]
            rows.sort(key=lambda row: (row.get(name) is None, row.get(name)),
                      reverse=part.upper().endswith("DESC"))
    if limit:
        rows = rows[:limit]

    columns = found["columns"].strip()
    if columns.upper().startswith("COUNT("):
        name = re.search(r"\bAS\s+(\w+)", columns, re.I)
        return [{name[1] if name else "count": len(rows)}]
    if columns in ("*",) or columns.endswith(".*"):
        return rows
    wanted = []
    for column in columns.split(","):
        column = column.strip().split(".")[-1]
        wanted.append(re.split(r"\s+AS\s+", column, flags=re.I)[0].strip())
    return [{name: row.get(name) for name in wanted} for row in rows]


def update(store, sql, params):
    head, where, _order, _limit = parse_tail(sql)
    found = re.match(r"UPDATE\s+(?P<table>\w+)\s+SET\s+(?P<sets>.+)$", head, re.I)
    assert found, f"unsupported update: {sql}"
    assignments = {}
    for part in found["sets"].split(","):
        column, value = part.split("=", 1)
        assignments[column.strip()] = literal(value, params)
    for row in store.all(found["table"]):
        if matches(row, where, list(params)):
            store.update(found["table"], row["id"], assignments)
    store.commit()


def insert(store, sql, params):
    found = re.match(r"INSERT\s+INTO\s+(?P<table>\w+)\s*\((?P<columns>[^)]*)\)\s*VALUES\s*\((?P<values>.*)\)$",
                     sql, re.I)
    assert found, f"unsupported insert: {sql}"
    columns = [name.strip() for name in found["columns"].split(",")]
    values = [literal(value, params) for value in found["values"].split(",")]
    document = dict(zip(columns, values))
    # The store keeps the same rules the application relies on.
    if found["table"] == "allocations" and document.get("status", "active") == "active":
        store.claim(f"bed-{document['bed_id']}")
        store.claim(f"student-{document['student_id']}")
    new_id = store.insert(found["table"], document)
    store.commit()
    return new_id


def delete(store, sql, params):
    head, where, _order, _limit = parse_tail(sql)
    found = re.match(r"DELETE\s+FROM\s+(?P<table>\w+)$", head, re.I)
    assert found, f"unsupported delete: {sql}"
    for row in store.all(found["table"]):
        if matches(row, where, list(params)):
            store.delete(found["table"], row["id"])
    store.commit()
