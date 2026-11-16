from branchlake import ConflictError, ValidationError
from tests.support import LakeCase


class IngestTests(LakeCase):
    def initialize(self):
        return self.lake.create([
            {'path': 'id', 'type': 'int'},
            {'path': 'meta', 'type': 'struct'},
            {'path': 'meta.note', 'type': 'str', 'default': 'absent'},
        ])['snapshot']

    def test_scalar_struct_and_order(self):
        old = self.initialize()
        rows = [{'id': 2, 'meta': {'note': None}}, {'id': 1}, {'id': 1, 'meta': {}}]
        receipt = self.lake.append('main', 'one', rows)
        self.assertEqual(self.lake.read(receipt['snapshot']), [
            {'id': 2, 'meta': {'note': None}},
            {'id': 1, 'meta': {'note': 'absent'}},
            {'id': 1, 'meta': {'note': 'absent'}},
        ])
        self.assertEqual(self.lake.read(old), [])
        result = self.lake.read(receipt['snapshot'])
        result[0]['meta']['note'] = 'changed by caller'
        self.assertIsNone(self.lake.read(receipt['snapshot'])[0]['meta']['note'])

    def test_request_replay_and_conflict(self):
        self.initialize()
        first = self.lake.append('main', 'one', [{'id': 1}])
        self.lake.alter('main', [{'op': 'rename', 'path': 'id', 'to': 'code'}])
        head = self.lake.head()
        self.assertEqual(self.lake.append('main', 'one', [{'id': 1}]), first)
        self.assertEqual(self.lake.head(), head)
        with self.assertRaises(ConflictError):
            self.lake.append('main', 'one', [{'id': 2}])
        self.assertEqual(self.lake.head(), head)
        self.lake.fork(first['snapshot'], 'other')
        receipt = self.lake.append('other', 'one', [{'id': 1}])
        self.assertNotEqual(receipt['batch'], first['batch'])
        self.assertEqual(len(self.lake.read(receipt['snapshot'])), 2)

    def test_invalid_tail_is_atomic(self):
        old = self.initialize()
        before = self.lake.catalog.db.total_changes
        with self.assertRaises(ValidationError):
            self.lake.append('main', 'bad', [{'id': 1}, {'id': True}])
        self.assertEqual(self.lake.head(), old)
        self.assertEqual(self.lake.read(old), [])
        self.assertEqual(self.lake.catalog.db.total_changes, before)
        receipt = self.lake.append('main', 'bad', [{'id': 7}])
        self.assertEqual(self.lake.read(receipt['snapshot'])[0]['id'], 7)

    def test_finite_types_and_unknown_fields(self):
        self.lake.create([
            {'path': 'n', 'type': 'float'},
            {'path': 'ok', 'type': 'bool'},
            {'path': 'meta', 'type': 'struct'},
        ])
        head = self.lake.head()
        for row in ({'n': float('nan')}, {'n': True}, {'ok': 1},
                    {'unknown': 1}, {'meta': None}, {'meta': {'bad': 1}}):
            with self.subTest(row=row), self.assertRaises(ValidationError):
                self.lake.append('main', 'bad', [row])
            self.assertEqual(self.lake.head(), head)
        huge = 2 ** 100
        result = self.lake.append('main', 'good', [{'n': huge, 'ok': False}])
        self.assertEqual(self.lake.read(result['snapshot']), [
            {'n': huge, 'ok': False, 'meta': {}}
        ])
