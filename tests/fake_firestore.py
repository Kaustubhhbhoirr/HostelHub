"""A very small in-memory stand-in for Firestore, used by the tests.

The real Cloud Firestore cannot be called from an automated test (it needs a
Firebase project and an internet connection), so this file imitates just the
parts of the client that data/firestore_store.py uses: documents, equality
queries, batched writes (all-or-nothing, with "only if unchanged since" checks),
create() that refuses an existing document, and a transaction that runs the
function directly. Each document has an update time, here a simple counter.

It is NOT a Firestore emulator; it only proves that HostelHub asks Firestore for
the right things and handles the answers correctly.
"""

from google.api_core import exceptions


class Snapshot:
    def __init__(self, doc_id, data, update_time=None):
        self.id = doc_id
        self._data = data
        self.update_time = update_time

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None

    @property
    def reference(self):
        return self._reference


class DocumentRef:
    def __init__(self, collection, doc_id):
        self.collection = collection
        self.id = doc_id

    def get(self, transaction=None):
        snapshot = Snapshot(self.id, self.collection.documents.get(self.id), self.update_time())
        snapshot._reference = self
        return snapshot

    def update_time(self):
        return self.collection.client.times.get((self.collection.name, self.id))

    def set(self, data):
        client = self.collection.client
        self.collection.documents[self.id] = dict(data)
        client.clock += 1
        client.times[(self.collection.name, self.id)] = client.clock
        client.writes.append(("set", self.collection.name, self.id))

    def create(self, data):
        if self.id in self.collection.documents:
            raise exceptions.AlreadyExists(f"{self.collection.name}/{self.id} exists")
        self.set(data)

    def delete(self):
        self.collection.documents.pop(self.id, None)
        self.collection.client.times.pop((self.collection.name, self.id), None)
        self.collection.client.writes.append(("delete", self.collection.name, self.id))


class Query:
    def __init__(self, collection, filters=(), limit=None):
        self.collection = collection
        self.filters = list(filters)
        self._limit = limit

    def where(self, filter=None):                      # noqa: A002 - Firestore's own name
        return Query(self.collection, self.filters + [(filter.field_path, filter.value)], self._limit)

    def limit(self, count):
        return Query(self.collection, self.filters, count)

    def stream(self):
        self.collection.client.reads += 1
        found = []
        for doc_id, data in list(self.collection.documents.items()):
            if all(data.get(field) == value for field, value in self.filters):
                reference = DocumentRef(self.collection, doc_id)
                snapshot = Snapshot(doc_id, data, reference.update_time())
                snapshot._reference = reference
                found.append(snapshot)
        return found[:self._limit] if self._limit else found


class Collection(Query):
    def __init__(self, client, name):
        self.client = client
        self.name = name
        self.documents = client.data.setdefault(name, {})
        super().__init__(self, (), None)

    def document(self, doc_id):
        return DocumentRef(self, str(doc_id))


class WriteOption:
    def __init__(self, last_update_time):
        self.last_update_time = last_update_time


class Batch:
    """Checks every condition first, then writes everything (like Firestore, all or nothing)."""

    def __init__(self, client):
        self.client = client
        self.operations = []

    def set(self, reference, data):
        self.operations.append(("set", reference, dict(data), None))

    def create(self, reference, data):
        self.operations.append(("create", reference, dict(data), None))

    def update(self, reference, data, option=None):
        self.operations.append(("update", reference, dict(data), option))

    def delete(self, reference, option=None):
        self.operations.append(("delete", reference, None, option))

    def commit(self):
        if len(self.operations) > 500:
            raise exceptions.InvalidArgument("too many writes in one batch")
        for kind, reference, _data, option in self.operations:
            exists = reference.id in reference.collection.documents
            if kind == "create" and exists:
                raise exceptions.AlreadyExists(f"{reference.collection.name}/{reference.id} exists")
            if kind == "update" and not exists:
                raise exceptions.NotFound(f"{reference.collection.name}/{reference.id} missing")
            if option is not None and reference.update_time() != option.last_update_time:
                raise exceptions.FailedPrecondition(f"{reference.collection.name}/{reference.id} changed")
        for kind, reference, data, _option in self.operations:
            if kind == "delete":
                reference.delete()
            elif kind == "update":
                reference.set({**reference.collection.documents[reference.id], **data})
            else:
                reference.set(data)
        self.client.batches += 1
        self.operations = []


class Transaction:
    """Runs the function straight away; enough to test the counter logic."""

    def __init__(self, client):
        self.client = client

    def run(self, function):
        return function(self)

    def set(self, reference, data):
        reference.set(data)


class FakeFirestore:
    def __init__(self):
        self.data = {}
        self.times = {}            # (collection, id) -> update time
        self.clock = 0
        self.writes = []
        self.batches = 0
        self.reads = 0

    def write_option(self, last_update_time):
        return WriteOption(last_update_time)

    def collection(self, name):
        return Collection(self, name)

    def batch(self):
        return Batch(self)

    def transaction(self):
        return Transaction(self)
