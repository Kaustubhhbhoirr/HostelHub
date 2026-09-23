"""
data/firestore_store.py — HostelHub's database: Cloud Firestore on Firebase.

Each of the eight collections becomes a Firestore collection, and each row
becomes one document whose id is the number used everywhere in HostelHub
(complaint CMP-0007 is document "7" of the complaints collection).

Firestore is a document database, so four things a relational database would
give us are arranged here by hand:

1. Numbers instead of random ids.
   A small "counters" document per collection hands out 1, 2, 3, ... inside a
   Firestore transaction, so two people adding a complaint at the same moment
   cannot get the same number.

2. All-or-nothing changes.
   Writes are not sent one by one. They are collected during the request and
   written together with a batch when the route calls commit(); rollback()
   simply throws the collected writes away. Reads during the same request
   already see the collected changes. A batch is atomic: all writes succeed or
   none do.

3. No lost updates.
   Every document read from Firestore carries its last update time. A changed
   document is written back only if it has NOT been changed by someone else since
   we read it (a Firestore precondition), and a new document only if its id is
   still free. Otherwise the whole batch is refused, the route rolls back and the
   user is asked to try again, so two wardens working at the same moment can
   never silently overwrite each other (for example a bed's status).

4. "Only one" rules (one active allocation per bed and per student).
   Firestore has no unique indexes, so claim() creates a tiny guard document
   named after the rule. Creating it fails if it already exists, so two people
   allocating the same bed at the same moment cannot both succeed.
"""

from data.store import COLLECTIONS, ConflictError, DuplicateError, Store, StoreError

COUNTERS = "counters"
CLAIMS = "allocation_claims"


def firestore_client():
    """The Firestore client of the already-initialised Firebase app."""
    try:
        import firebase_admin
        from firebase_admin import firestore
    except ImportError as error:                      # pragma: no cover - listed in requirements.txt
        raise StoreError("firebase-admin is not installed.") from error
    if not firebase_admin._apps:
        raise StoreError("Firebase is not configured (FIREBASE_SERVICE_ACCOUNT is missing).")
    return firestore.client()


def field_filter(field, value):
    from google.cloud.firestore_v1.base_query import FieldFilter
    return FieldFilter(field, "==", value)


def google_errors():
    from google.api_core import exceptions
    return exceptions


class FirestoreStore(Store):
    backend = "firestore"

    def __init__(self, client=None):
        self.db = client or firestore_client()
        self.pending = {}          # (collection, id) -> {"data": {...}} or {"delete": True}, see commit()
        self.versions = {}         # (collection, id) -> update time of the copy we read
        self.claims_taken = []     # created in this request; removed again on rollback
        self.claims_released = []  # removed from Firestore when the request commits

    # -- helpers -------------------------------------------------------
    @staticmethod
    def check_collection(collection):
        if collection not in COLLECTIONS:
            raise StoreError(f"Unknown collection: {collection}")

    def doc_ref(self, collection, doc_id):
        return self.db.collection(collection).document(str(doc_id))

    def pending_doc(self, collection, doc_id):
        return self.pending.get((collection, int(doc_id)))

    @staticmethod
    def matches(document, filters):
        return all(document.get(field) == value for field, value in filters.items())

    def next_id(self, collection):
        """Hand out the next number for this collection inside a Firestore transaction.

        The transaction matters: without it two people adding a complaint at the
        same moment could both read "14" and both become CMP-0014.
        """
        counter = self.db.collection(COUNTERS).document(collection)
        transaction = self.db.transaction()

        def take_number(transaction):
            snapshot = counter.get(transaction=transaction)
            number = (snapshot.to_dict() or {}).get("next", 1) if snapshot.exists else 1
            transaction.set(counter, {"next": number + 1})
            return number

        run_directly = getattr(transaction, "run", None)   # only the test stand-in has this
        if run_directly is not None:
            return int(run_directly(take_number))

        from firebase_admin import firestore
        return int(firestore.transactional(take_number)(transaction))

    # -- reading -------------------------------------------------------
    def get(self, collection, doc_id):
        self.check_collection(collection)
        if doc_id is None:
            return None
        try:
            doc_id = int(doc_id)
        except (TypeError, ValueError):
            return None
        change = self.pending_doc(collection, doc_id)
        if change is not None:
            return None if change.get("delete") else dict(change["data"])
        try:
            snapshot = self.doc_ref(collection, doc_id).get()
        except google_errors().GoogleAPICallError as error:
            raise StoreError(str(error)) from error
        if not snapshot.exists:
            return None
        self.remember_version(collection, snapshot)
        return self.with_id(snapshot)

    @staticmethod
    def with_id(snapshot):
        data = snapshot.to_dict() or {}
        data["id"] = int(data.get("id", snapshot.id))
        return data

    def remember_version(self, collection, snapshot):
        """Note when this document was last changed, for the check in commit().

        The FIRST copy read in this request counts, because that is the one the
        route based its decisions on (for example "this bed is available").
        """
        key = (collection, int(snapshot.id))
        if key not in self.versions and key not in self.pending:
            self.versions[key] = getattr(snapshot, "update_time", None)

    def find(self, collection, **filters):
        self.check_collection(collection)
        query = self.db.collection(collection)
        for field, value in filters.items():
            query = query.where(filter=field_filter(field, value))
        try:
            snapshots = list(query.stream())
        except google_errors().GoogleAPICallError as error:
            raise StoreError(str(error)) from error
        rows = {}
        for snapshot in snapshots:
            self.remember_version(collection, snapshot)
            rows[int(snapshot.id)] = self.with_id(snapshot)

        # Apply the changes this request has made but not committed yet.
        for (pending_collection, doc_id), change in self.pending.items():
            if pending_collection != collection:
                continue
            if change.get("delete"):
                rows.pop(doc_id, None)
            elif self.matches(change["data"], filters):
                rows[doc_id] = dict(change["data"])
            else:
                rows.pop(doc_id, None)
        return list(rows.values())

    # -- writing (kept until commit) -----------------------------------
    def insert(self, collection, data):
        self.check_collection(collection)
        doc_id = int(data.get("id") or self.next_id(collection))
        document = dict(data)
        document["id"] = doc_id
        self.pending[(collection, doc_id)] = {"data": document, "new": True}
        return doc_id

    def update(self, collection, doc_id, changes):
        self.check_collection(collection)
        if not changes:
            return
        current = self.get(collection, doc_id)
        if current is None:
            return
        current.update(changes)
        key = (collection, int(doc_id))
        # Keep "new" if the document was inserted earlier in this same request.
        self.pending[key] = {**self.pending.get(key, {}), "data": current}

    def delete(self, collection, doc_id):
        self.check_collection(collection)
        key = (collection, int(doc_id))
        if self.pending.get(key, {}).get("new"):
            del self.pending[key]            # inserted and deleted in the same request: nothing to write
        else:
            self.pending[key] = {"delete": True}

    # -- "only one" rules ----------------------------------------------
    def claim(self, key):
        if key in self.claims_released:
            self.claims_released.remove(key)     # released and taken again in the same request
            return
        try:
            self.db.collection(CLAIMS).document(key).create({"key": key})
        except google_errors().AlreadyExists as error:
            raise DuplicateError(f"{key} is already taken") from error
        except google_errors().GoogleAPICallError as error:
            raise StoreError(str(error)) from error
        self.claims_taken.append(key)

    def release(self, key):
        if key in self.claims_taken:
            self.claims_taken.remove(key)
            self.db.collection(CLAIMS).document(key).delete()
        elif key not in self.claims_released:
            self.claims_released.append(key)

    # -- transaction ---------------------------------------------------
    def commit(self):
        if not self.pending and not self.claims_released:
            self.claims_taken.clear()
            return
        batch = self.db.batch()
        for (collection, doc_id), change in self.pending.items():
            reference = self.doc_ref(collection, doc_id)
            version = self.versions.get((collection, doc_id))
            # Only if nobody else changed the document since we read it (see point 3 above).
            option = self.db.write_option(last_update_time=version) if version is not None else None
            if change.get("new"):
                batch.create(reference, change["data"])          # fails if the id is already used
            elif change.get("delete"):
                batch.delete(reference, option=option)
            elif option is not None:
                batch.update(reference, change["data"], option=option)
            else:
                batch.set(reference, change["data"])
        for key in self.claims_released:
            batch.delete(self.db.collection(CLAIMS).document(key))
        try:
            batch.commit()                 # one atomic write: everything or nothing
        except (google_errors().FailedPrecondition, google_errors().Aborted,
                google_errors().AlreadyExists) as error:
            self.rollback()
            raise ConflictError("Someone else changed this record at the same moment.") from error
        except google_errors().GoogleAPICallError as error:
            self.rollback()
            raise StoreError(str(error)) from error
        self.pending.clear()
        self.versions.clear()
        self.claims_released.clear()
        self.claims_taken.clear()

    def rollback(self):
        for key in self.claims_taken:
            try:
                self.db.collection(CLAIMS).document(key).delete()
            except google_errors().GoogleAPICallError:      # pragma: no cover - best effort
                pass
        self.pending.clear()
        self.versions.clear()
        self.claims_taken.clear()
        self.claims_released.clear()
