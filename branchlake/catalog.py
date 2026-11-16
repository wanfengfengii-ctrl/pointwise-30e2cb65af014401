import json
import re
import sqlite3
from contextlib import contextmanager

from .errors import ConflictError, NotFoundError, ValidationError


def encode(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError('expected finite JSON data') from exc


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', value):
        raise ValidationError('invalid branch name')
    return value


class Catalog:
    def __init__(self, path, publish_hook=None):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.publish_hook = publish_hook
        self._field_cache = {}
        self._cell_cache = {}
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS field_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                default_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots(
                id INTEGER PRIMARY KEY AUTOINCREMENT, parents TEXT NOT NULL,
                schema_json TEXT NOT NULL, batches TEXT NOT NULL,
                corrections TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS branches(
                name TEXT PRIMARY KEY, snapshot INTEGER NOT NULL REFERENCES snapshots(id));
            CREATE TABLE IF NOT EXISTS batches(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                writer INTEGER NOT NULL REFERENCES snapshots(id), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts(
                branch TEXT NOT NULL, request TEXT NOT NULL, fingerprint TEXT NOT NULL,
                snapshot INTEGER NOT NULL REFERENCES snapshots(id),
                batch INTEGER NOT NULL REFERENCES batches(id),
                PRIMARY KEY(branch, request));
            CREATE TABLE IF NOT EXISTS correction_receipts(
                branch TEXT NOT NULL, request TEXT NOT NULL, fingerprint TEXT NOT NULL,
                snapshot INTEGER NOT NULL REFERENCES snapshots(id),
                PRIMARY KEY(branch, request));
        """)
        # Databases created before correction support lack the snapshot state column.
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(snapshots)')}
        if 'corrections' not in columns:
            self.db.execute(
                "ALTER TABLE snapshots ADD COLUMN corrections TEXT NOT NULL DEFAULT '{}'")

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def snapshot(self, snapshot):
        if type(snapshot) is not int or snapshot < 1:
            raise ValidationError('snapshot must be a positive integer')
        row = self.db.execute(
            'SELECT parents,schema_json,batches,corrections FROM snapshots WHERE id=?',
            (snapshot,)
        ).fetchone()
        if row is None:
            raise NotFoundError('unknown snapshot')
        return dict(id=snapshot, parents=json.loads(row[0]),
                    schema=json.loads(row[1]), batches=json.loads(row[2]),
                    corrections=json.loads(row[3]))

    def field_event(self, identity):
        """Fixed (kind, default) of a field creation identity."""
        cached = self._field_cache.get(identity)
        if cached is None:
            row = self.db.execute(
                'SELECT kind,default_json FROM field_events WHERE id=?', (int(identity),)
            ).fetchone()
            if row is None:
                raise NotFoundError('unknown field identity')
            cached = (row[0], json.loads(row[1]))
            self._field_cache[identity] = cached
        return cached

    def batch_cells(self, batch):
        """Decoded, validated payload of an immutable batch; cached per connection."""
        cached = self._cell_cache.get(batch)
        if cached is None:
            row = self.db.execute('SELECT payload FROM batches WHERE id=?', (batch,)).fetchone()
            if row is None:
                raise NotFoundError('unknown batch')
            cached = json.loads(row[0])
            self._cell_cache[batch] = cached
        return cached

    def head(self, branch):
        name(branch)
        row = self.db.execute('SELECT snapshot FROM branches WHERE name=?', (branch,)).fetchone()
        if row is None:
            raise NotFoundError('unknown branch')
        return row[0]

    def fork(self, snapshot, branch):
        name(branch)
        with self.transaction():
            self.snapshot(snapshot)
            if self.db.execute('SELECT 1 FROM branches WHERE name=?', (branch,)).fetchone():
                raise ConflictError('branch already exists')
            self.db.execute('INSERT INTO branches VALUES(?,?)', (branch, snapshot))
        return dict(branch=branch, snapshot=snapshot)

    def identity(self, kind, default):
        cursor = self.db.execute(
            'INSERT INTO field_events(kind,default_json) VALUES(?,?)', (kind, encode(default))
        )
        return str(cursor.lastrowid)

    def publish(self, branch, parents, schema, batches, corrections=None, create=False):
        cursor = self.db.execute(
            'INSERT INTO snapshots(parents,schema_json,batches,corrections) VALUES(?,?,?,?)',
            (encode(parents), encode(schema), encode(sorted(set(batches))),
             encode(corrections or {}))
        )
        snapshot = cursor.lastrowid
        if self.publish_hook:
            self.publish_hook('snapshot_inserted', snapshot)
        if create:
            self.db.execute('INSERT INTO branches VALUES(?,?)', (branch, snapshot))
        else:
            self.db.execute('UPDATE branches SET snapshot=? WHERE name=?', (snapshot, branch))
        if self.publish_hook:
            self.publish_hook('head_updated', snapshot)
        return snapshot

    def close(self):
        self.db.close()
