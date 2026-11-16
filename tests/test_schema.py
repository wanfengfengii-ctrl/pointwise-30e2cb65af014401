from branchlake import ConflictError, NotFoundError, ValidationError
from tests.support import LakeCase


class SchemaTests(LakeCase):
    def test_duplicate_changes_are_atomic(self):
        original = self.lake.create([{'path': 'id', 'type': 'int'}])['snapshot']
        self.lake.fork(original, 'work')
        before = self.lake.catalog.db.execute('SELECT count(*) FROM field_events').fetchone()[0]
        with self.assertRaises(ValidationError):
            self.lake.alter('work', [
                {'op': 'add', 'path': 'extra', 'type': 'int'},
                {'op': 'add', 'path': 'extra', 'type': 'str'},
            ])
        self.assertEqual(self.lake.head('work'), original)
        self.assertEqual(self.lake.head(), original)
        self.assertEqual(self.lake.catalog.db.execute(
            'SELECT count(*) FROM field_events').fetchone()[0], before)

    def test_invalid_create_and_paths(self):
        for fields in (
            [{'path': 'x', 'type': 'int', 'default': True}],
            [{'path': 'a.b', 'type': 'int'}],
            [{'path': 'a', 'type': 'struct'}, {'path': 'a.b.c', 'type': 'int'}],
            [{'path': 'x', 'type': 'array'}],
            [{'path': 's', 'type': 'struct', 'default': None}],
            [{'path': 'n', 'type': 'float', 'default': float('inf')}],
        ):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                self.lake.create(fields)
        root = self.lake.create([])['snapshot']
        with self.assertRaises(ConflictError):
            self.lake.create([])
        with self.assertRaises(ValidationError):
            self.lake.fork(root, '../bad')
        with self.assertRaises(NotFoundError):
            self.lake.fork(99999, 'missing')
        self.lake.fork(root, 'ok')
        with self.assertRaises(ConflictError):
            self.lake.fork(root, 'ok')
        with self.assertRaises(NotFoundError):
            self.lake.read(99999)
        with self.assertRaises(ValidationError):
            self.lake.read(True)

    def test_struct_rename_drop_and_invalid_move(self):
        self.lake.create([
            {'path': 'm', 'type': 'struct'},
            {'path': 'm.x', 'type': 'int', 'default': 4},
            {'path': 'other', 'type': 'int'},
        ])
        old = self.lake.append('main', 'r', [{'m': {'x': 8}, 'other': 3}])['snapshot']
        renamed = self.lake.alter('main', [{'op': 'rename', 'path': 'm', 'to': 'info'}])['snapshot']
        self.assertEqual(self.lake.read(renamed), [{'info': {'x': 8}, 'other': 3}])
        for change in (
            {'op': 'rename', 'path': 'info', 'to': 'info.nested'},
            {'op': 'rename', 'path': 'other', 'to': 'info.x'},
            {'op': 'add', 'path': 'info.nested', 'type': 'struct'},
            {'op': 'drop', 'path': 'missing'},
            {'op': 'type', 'path': 'other', 'type': 'str'},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.lake.alter('main', [change])
            self.assertEqual(self.lake.head(), renamed)
        dropped = self.lake.alter('main', [{'op': 'drop', 'path': 'info'}])['snapshot']
        self.assertEqual(self.lake.read(dropped), [{'other': 3}])
        self.assertEqual(self.lake.read(old), [{'m': {'x': 8}, 'other': 3}])
