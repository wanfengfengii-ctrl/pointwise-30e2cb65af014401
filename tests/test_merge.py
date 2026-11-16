import random

from branchlake import ConflictError, Lake
from branchlake.samples import observation_lake
from tests.support import LakeCase


class MergeTests(LakeCase):
    def test_identity_ledger_both_directions(self):
        # Expected cells are a hand-written creation-event ledger, not reader output.
        expected = [
            {'sample': 0, 'legacy_code': 9, 'retired': 100, 'temp': 7, 'count': 3,
             'meta': {'site': 'A', 'quality': 99}},
            {'sample': 1, 'legacy_code': 10, 'retired': 100, 'temp': None, 'count': 3,
             'meta': {'site': 'L', 'quality': None}},
            {'sample': 2, 'legacy_code': 11, 'retired': 55, 'temp': 7, 'count': 88,
             'meta': {'site': 'R', 'quality': 99}},
        ]
        for target, source in (('left', 'right'), ('right', 'left')):
            with self.subTest(target=target), Lake() as lake:
                old, left, right = observation_lake(lake)
                history = {snapshot: lake.read(snapshot) for snapshot in (old, left, right)}
                merged = lake.merge(target, source)['snapshot']
                self.assertEqual(lake.read(merged), expected)
                again = lake.merge(target, source)['snapshot']
                self.assertEqual(lake.read(again), expected)
                for snapshot, rows in history.items():
                    self.assertEqual(lake.read(snapshot), rows)
                self.assertEqual(history[old], [
                    {'sample': 0, 'legacy': 9, 'retired': 44, 'meta': {'site': 'A'}}
                ])
                self.assertEqual(history[left][-1]['retired'], 45)
                self.assertEqual(history[right][0]['retired'], 100)

    def test_conflict_cases_preserve_target(self):
        cases = [
            ([{'op': 'add', 'path': 'new', 'type': 'int'}],
             [{'op': 'add', 'path': 'new', 'type': 'int'}]),
            ([{'op': 'rename', 'path': 'x', 'to': 'a'}],
             [{'op': 'rename', 'path': 'x', 'to': 'b'}]),
            ([{'op': 'rename', 'path': 'x', 'to': 'a'}],
             [{'op': 'drop', 'path': 'x'}, {'op': 'add', 'path': 'x', 'type': 'int'}]),
            ([{'op': 'drop', 'path': 'm'}],
             [{'op': 'add', 'path': 'm.y', 'type': 'int'}]),
            ([{'op': 'rename', 'path': 'm', 'to': 'n'}],
             [{'op': 'add', 'path': 'n', 'type': 'str'}]),
        ]
        for left_changes, right_changes in cases:
            with self.subTest(changes=(left_changes, right_changes)), Lake() as lake:
                root = lake.create([
                    {'path': 'x', 'type': 'int'}, {'path': 'm', 'type': 'struct'},
                    {'path': 'm.v', 'type': 'int'},
                ])['snapshot']
                lake.fork(root, 'right')
                lake.alter('main', left_changes)
                lake.alter('right', right_changes)
                before = lake.head()
                rows = lake.read(before)
                count = lake.catalog.db.execute('SELECT count(*) FROM snapshots').fetchone()[0]
                with self.assertRaises(ConflictError):
                    lake.merge('main', 'right')
                self.assertEqual(lake.head(), before)
                self.assertEqual(lake.read(before), rows)
                self.assertEqual(lake.catalog.db.execute(
                    'SELECT count(*) FROM snapshots').fetchone()[0], count)

    def test_parent_rename_and_independent_child(self):
        root = self.lake.create([
            {'path': 'm', 'type': 'struct'}, {'path': 'm.old', 'type': 'int'}
        ])['snapshot']
        self.lake.fork(root, 'right')
        self.lake.alter('main', [{'op': 'rename', 'path': 'm', 'to': 'info'}])
        self.lake.alter('right', [
            {'op': 'add', 'path': 'm.extra', 'type': 'int', 'default': 6}
        ])
        self.lake.append('right', 'r', [{'m': {'old': 4, 'extra': None}}])
        merged = self.lake.merge('main', 'right')['snapshot']
        self.assertEqual(self.lake.read(merged), [{'info': {'old': 4, 'extra': None}}])

    def test_crisscross_uses_consolidated_common_schema(self):
        root = self.lake.create([{'path': 'x', 'type': 'int'}])['snapshot']
        self.lake.fork(root, 'right')
        a = self.lake.alter('main', [{'op': 'rename', 'path': 'x', 'to': 'a'}])['snapshot']
        b = self.lake.alter('right', [
            {'op': 'add', 'path': 'y', 'type': 'int', 'default': 2}
        ])['snapshot']
        self.lake.fork(a, 'old_a')
        self.lake.fork(b, 'old_b')
        self.lake.merge('main', 'old_b')
        self.lake.merge('right', 'old_a')
        self.lake.alter('main', [{'op': 'rename', 'path': 'a', 'to': 'final'}])
        self.lake.append('right', 'r', [{'a': 3, 'y': 4}])
        snapshot = self.lake.merge('main', 'right')['snapshot']
        self.assertEqual(self.lake.read(snapshot), [{'final': 3, 'y': 4}])

    def test_seeded_branch_ledger(self):
        rng = random.Random(24017)
        for case in range(16):
            with self.subTest(case=case), Lake() as lake:
                root = lake.create([{'path': 'id', 'type': 'int'}])['snapshot']
                lake.fork(root, 'left')
                lake.fork(root, 'right')
                columns = list(range(6))
                rng.shuffle(columns)
                defaults, events = {}, []
                for branch, selected in (('left', columns[:3]), ('right', columns[3:])):
                    for number in selected:
                        field = 'c' + str(number)
                        defaults[field] = 10 + number
                        lake.alter(branch, [{'op': 'add', 'path': field,
                                             'type': 'int', 'default': 10 + number}])
                        row = {'id': len(events), field: None if number % 2 else 100 + number}
                        lake.append(branch, 'row-' + str(number), [row])
                        events.append(dict(row))
                merged = lake.merge('left', 'right')['snapshot']
                expected = [{**defaults, **event} for event in events]
                self.assertEqual(lake.read(merged), expected)
                self.assertEqual(lake.read(root), [])
