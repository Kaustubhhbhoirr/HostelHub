"""
data/store.py — The small database interface HostelHub uses everywhere.

HostelHub keeps its data in eight Firestore collections (users, rooms, beds,
allocations, complaints, room_change_requests, college_maintenance_requests,
notifications).

The application never calls Firestore directly. It uses:

    store.get(collection, doc_id)              one document, or None
    store.find(collection, **filters)          list of documents (equality filters)
    store.all(collection)                      every document
    store.insert(collection, data)             returns the new id
    store.update(collection, doc_id, changes)
    store.delete(collection, doc_id)
    store.claim(key) / store.release(key)      "only one" rules (see below)
    store.commit() / store.rollback()

Reads and joins over several collections live in data/queries.py, so there is one
version of every hostel rule.

"Claims" keep the two most important rules: a bed and a student may each have only
one ACTIVE allocation. Firestore has no unique indexes, so the store creates a
small guard document whose id is the rule itself. Creating a document that already
exists fails, which makes a double allocation impossible even when two wardens
click at the same moment.
"""

COLLECTIONS = ("users", "rooms", "beds", "allocations", "complaints",
               "room_change_requests", "college_maintenance_requests", "notifications")


class StoreError(Exception):
    """The database could not be read or written."""


class DuplicateError(StoreError):
    """A uniqueness rule was broken (duplicate email, or a bed allocated twice)."""


class ConflictError(StoreError):
    """Another request changed the same document at the same moment; nothing was saved."""


class Store:
    """The operations the store provides (implemented by data/firestore_store.py)."""

    backend = "?"

    # -- reading -------------------------------------------------------
    def get(self, collection, doc_id):
        raise NotImplementedError

    def find(self, collection, **filters):
        raise NotImplementedError

    def all(self, collection):
        return self.find(collection)

    def first(self, collection, **filters):
        rows = self.find(collection, **filters)
        return rows[0] if rows else None

    def count(self, collection, **filters):
        return len(self.find(collection, **filters))

    # -- writing (not saved until commit) ------------------------------
    def insert(self, collection, data):
        raise NotImplementedError

    def update(self, collection, doc_id, changes):
        raise NotImplementedError

    def delete(self, collection, doc_id):
        raise NotImplementedError

    # -- "only one" rules ----------------------------------------------
    def claim(self, key):
        """Take the single slot named `key`; raises DuplicateError if it is taken."""
        raise NotImplementedError

    def release(self, key):
        raise NotImplementedError

    # -- transaction ---------------------------------------------------
    def commit(self):
        raise NotImplementedError

    def rollback(self):
        raise NotImplementedError

    def close(self):
        pass


def sort_rows(rows, *keys, reverse=False):
    """Sort documents by one or more fields, keeping None values last."""
    def sort_key(row):
        return tuple((row.get(key) is None, row.get(key)) for key in keys)
    return sorted(rows, key=sort_key, reverse=reverse)
