from branchlake import Lake
from tests.support import LakeCase


class SnapshotTests(LakeCase):
    def test_default_null_rename_recreate_and_history(self):
        self.lake.create([{'path': 'x', 'type': 'int', 'default': 1}])
        old = self.lake.append('main', 'old', [{'x': 9}, {'x': None}, {}])['snapshot']
        created = self.lake.alter('main', [
            {'op': 'rename', 'path': 'x', 'to': 'original'},
            {'op': 'add', 'path': 'x', 'type': 'int', 'default': 7},
        ])['snapshot']
        new = self.lake.append('main', 'new', [{'original': 11, 'x': None}])['snapshot']
        self.assertEqual(self.lake.read(created), [
            {'original': 9, 'x': 7}, {'original': None, 'x': 7}, {'original': 1, 'x': 7}
        ])
        rebuilt = self.lake.alter('main', [
            {'op': 'drop', 'path': 'x'},
            {'op': 'add', 'path': 'x', 'type': 'str', 'default': 'rebuilt'},
        ])['snapshot']
        self.assertEqual(self.lake.read(rebuilt), [
            {'original': 9, 'x': 'rebuilt'},
            {'original': None, 'x': 'rebuilt'},
            {'original': 1, 'x': 'rebuilt'},
            {'original': 11, 'x': 'rebuilt'},
        ])
        self.assertIsNone(self.lake.read(new)[-1]['x'])
        self.assertEqual(self.lake.read(old), [{'x': 9}, {'x': None}, {'x': 1}])

    def test_caller_cannot_change_fixed_default(self):
        default = {}
        definitions = [{'path': 'm', 'type': 'struct', 'default': default},
                       {'path': 'm.n', 'type': 'int', 'default': 6}]
        self.lake.create(definitions)
        default['alien'] = 'caller'
        definitions[1]['default'] = 90
        snapshot = self.lake.append('main', 'r', [{}])['snapshot']
        self.assertEqual(self.lake.read(snapshot), [{'m': {'n': 6}}])
