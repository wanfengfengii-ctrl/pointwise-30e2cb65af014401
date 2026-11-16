from . import corrections, ingest, merge as merging, reader, schema
from .catalog import Catalog
from .errors import ConflictError, LakeError, NotFoundError, ValidationError

__all__ = ['Lake', 'LakeError', 'ValidationError', 'NotFoundError', 'ConflictError']


class Lake:
    """One SQLite connection; use one instance per serial caller."""
    def __init__(self, path=':memory:', publish_hook=None):
        self.catalog = Catalog(path, publish_hook)

    def create(self, fields):
        with self.catalog.transaction():
            if self.catalog.db.execute('SELECT 1 FROM branches').fetchone():
                raise ConflictError('lake already initialized')
            fields = schema.create(self.catalog, fields)
            snapshot = self.catalog.publish('main', [], fields, [], create=True)
        return dict(branch='main', snapshot=snapshot)

    def fork(self, snapshot, branch):
        return self.catalog.fork(snapshot, branch)

    def head(self, branch='main'):
        return self.catalog.head(branch)

    def alter(self, branch, changes):
        with self.catalog.transaction():
            head = self.catalog.head(branch)
            previous = self.catalog.snapshot(head)
            fields = schema.alter(self.catalog, previous['schema'], changes)
            snapshot = self.catalog.publish(branch, [head], fields,
                                            previous['batches'],
                                            previous['corrections'])
        return dict(snapshot=snapshot)

    def append(self, branch, request, rows):
        return ingest.append(self.catalog, branch, request, rows)

    def correct(self, branch, request, changes):
        return corrections.correct(self.catalog, branch, request, changes)

    def merge(self, target, source):
        return merging.merge(self.catalog, target, source)

    def read(self, snapshot):
        return reader.read(self.catalog, snapshot)

    def read_rows(self, snapshot):
        return corrections.read_rows(self.catalog, snapshot)

    def close(self):
        self.catalog.close()

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()
