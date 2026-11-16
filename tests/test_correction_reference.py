"""Randomized cross-checks against an independent explicit-state reference.

The reference (:mod:`tests.reference`) keeps the effective cells of every source
in plain dicts and never imports the production correction, reader or merge
modules.  These tests drive the same corrections and merges through both and
compare projected rows, conflict decisions and rollback effects.
"""

import random
import tempfile
import unittest
from pathlib import Path

from branchlake import ConflictError, Lake
from tests.reference import merge as reference_merge, patch, project, remove, state

FIELDS = [
    {'path': 'temperature', 'type': 'int', 'default': 10},
    {'path': 'quality', 'type': 'int', 'default': 1},
]
ROWS = [{}, {'temperature': 4, 'quality': 7}, {'temperature': 20}]
NAMES = ['temperature', 'quality']
# Effective cells of ROWS, written by hand from the public creation defaults.
INITIAL_CELLS = [
    {'temperature': 10, 'quality': 1},
    {'temperature': 4, 'quality': 7},
    {'temperature': 20, 'quality': 1},
]


def initial_states(batch):
    return {(batch, index): state(**cells)
            for index, cells in enumerate(INITIAL_CELLS)}


class Fixture:
    def __init__(self, lake):
        self.lake = lake
        lake.create(FIELDS)
        receipt = lake.append('main', 'rows', ROWS)
        self.origin = receipt['snapshot']
        self.batch = receipt['batch']
        lake.fork(self.origin, 'left')
        lake.fork(self.origin, 'right')
        self.states = initial_states(self.batch)

    def source(self, index):
        return [self.batch, index]

    def key(self, index):
        return (self.batch, index)

    def cells(self, index):
        return dict(self.states[self.key(index)]['cells'])

    def correct(self, branch, request, changes, states):
        """Apply production correction and the same steps to ``states``."""
        updated = states
        for change in changes:
            key = tuple(change['source'])
            if change['op'] == 'remove':
                updated = remove(updated, key, change['before'])
            else:
                updated = patch(updated, key, change['before'], change['after'])
        receipt = self.lake.correct(branch, request, changes)
        return receipt, updated


class RandomCrossCheckTests(unittest.TestCase):
    def test_random_branch_corrections_match_reference(self):
        rng = random.Random(90210)
        for case in range(40):
            with self.subTest(case=case), Lake() as lake:
                fixture = Fixture(lake)
                base = fixture.states
                left, right = dict(base), dict(base)
                for branch in ('left', 'right'):
                    states = left if branch == 'left' else right
                    live = [index for index in range(len(ROWS))
                            if not states[fixture.key(index)]['retracted']]
                    for number in range(rng.randint(0, 3)):
                        if not live:
                            break
                        index = rng.choice(live)
                        if rng.random() < 0.2:
                            before = dict(states[fixture.key(index)]['cells'])
                            _, states = fixture.correct(branch, '%s-rm-%d' % (branch, number),
                                                        [{'source': fixture.source(index),
                                                          'op': 'remove',
                                                          'before': before}], states)
                            live.remove(index)
                        else:
                            field = rng.choice(NAMES)
                            value = rng.choice([None, 0, 2, 5, 10, 11, 12])
                            before = {field: states[fixture.key(index)]['cells'][field]}
                            _, states = fixture.correct(branch, '%s-p-%d' % (branch, number),
                                                        [{'source': fixture.source(index),
                                                          'op': 'patch',
                                                          'before': before,
                                                          'after': {field: value}}], states)
                    if branch == 'left':
                        left = states
                    else:
                        right = states
                    head = lake.head(branch)
                    self.assertEqual(lake.read(head),
                                     [item['row'] for item in lake.read_rows(head)])
                target, source = rng.choice([('left', 'right'), ('right', 'left')])
                try:
                    expected = reference_merge(base, left, right, NAMES)
                except ValueError:
                    head = lake.head('left')
                    snapshots = lake.catalog.db.execute(
                        'SELECT count(*) FROM snapshots').fetchone()[0]
                    receipts = lake.catalog.db.execute(
                        'SELECT * FROM correction_receipts ORDER BY 1,2').fetchall()
                    with self.assertRaises(ConflictError):
                        lake.merge(target, source)
                    self.assertEqual(lake.head('left'), head)
                    self.assertEqual(lake.catalog.db.execute(
                        'SELECT count(*) FROM snapshots').fetchone()[0], snapshots)
                    self.assertEqual(lake.catalog.db.execute(
                        'SELECT * FROM correction_receipts ORDER BY 1,2').fetchall(),
                        receipts)
                else:
                    merged = lake.merge(target, source)['snapshot']
                    self.assertEqual(lake.read_rows(merged), project(expected))
                    # Both directions must agree for the same two heads.
                    other = 'left' if target == 'right' else 'right'
                    second = lake.merge(other, target)['snapshot']
                    self.assertEqual(lake.read_rows(second), project(expected))


class CrisscrossCrossCheckTests(unittest.TestCase):
    def test_frozen_crisscross_matches_reference_snapshots(self):
        for swap in (False, True):
            with self.subTest(swap=swap), Lake() as lake:
                fixture = Fixture(lake)
                base = fixture.states
                src = fixture.source(0)
                parents = {fixture.origin: []}
                recorded = {fixture.origin: base}

                def record(snapshot, states):
                    recorded[snapshot] = states
                    parents[snapshot] = list(dict.fromkeys(
                        lake.catalog.snapshot(snapshot)['parents']))

                def ancestors(snapshot):
                    pending, found = [snapshot], set()
                    while pending:
                        current = pending.pop()
                        if current not in found:
                            found.add(current)
                            pending.extend(parents[current])
                    return found

                def virtual(left, right):
                    common = ancestors(left) & ancestors(right)
                    maximal = set(common)
                    for candidate in common:
                        maximal.difference_update(ancestors(candidate) - {candidate})
                    candidates = sorted(maximal)
                    states = recorded[candidates[0]]
                    accumulated = set(ancestors(candidates[0]))
                    for candidate in candidates[1:]:
                        ancestry = ancestors(candidate)
                        earlier = virtual_set(accumulated, ancestry)
                        states = reference_merge(earlier, states,
                                                 recorded[candidate], NAMES)
                        accumulated.update(ancestry)
                    return states

                def virtual_set(left, right):
                    common = left & right
                    maximal = set(common)
                    for candidate in common:
                        maximal.difference_update(ancestors(candidate) - {candidate})
                    candidates = sorted(maximal)
                    states = recorded[candidates[0]]
                    accumulated = set(ancestors(candidates[0]))
                    for candidate in candidates[1:]:
                        ancestry = ancestors(candidate)
                        earlier = virtual_set(accumulated, ancestry)
                        states = reference_merge(earlier, states,
                                                 recorded[candidate], NAMES)
                        accumulated.update(ancestry)
                    return states

                def correct(branch, request, field, before, after, states):
                    _, updated = fixture.correct(branch, request, [{
                        'source': src, 'op': 'patch',
                        'before': {field: before}, 'after': {field: after}}], states)
                    return updated

                a_states = correct('left', 'A', 'temperature', 10, 11, base)
                a = lake.head('left')
                record(a, a_states)
                b_states = correct('right', 'B', 'quality', 1, 2, base)
                b = lake.head('right')
                record(b, b_states)
                lake.fork(a, 'saved_a')
                lake.fork(b, 'saved_b')
                self.assertEqual(lake.head('saved_a'), a)
                self.assertEqual(lake.head('saved_b'), b)

                c = lake.merge('left', 'saved_b')['snapshot']
                c_states = reference_merge(virtual(a, b), a_states, b_states, NAMES)
                record(c, c_states)
                self.assertEqual(lake.read_rows(c), project(c_states))
                self.assertEqual(lake.read(c)[0], {'temperature': 11, 'quality': 2})
                d = lake.merge('right', 'saved_a')['snapshot']
                d_states = reference_merge(virtual(b, a), b_states, a_states, NAMES)
                record(d, d_states)
                self.assertEqual(lake.read_rows(d), project(d_states))
                self.assertEqual(lake.read(d)[0], {'temperature': 11, 'quality': 2})

                e_states = correct('left', 'E', 'temperature', 11, 12, c_states)
                e = lake.head('left')
                record(e, e_states)
                f_states = correct('right', 'F', 'quality', 2, 3, d_states)
                f = lake.head('right')
                record(f, f_states)

                # Neither A nor B alone may serve as the common ancestor.
                virtual_ab = virtual(e, f)
                self.assertEqual(virtual_ab, reference_merge(base, a_states,
                                                             b_states, NAMES))
                expected = reference_merge(virtual_ab, e_states, f_states, NAMES)
                target, source = ('right', 'left') if swap else ('left', 'right')
                g = lake.merge(target, source)['snapshot']
                self.assertEqual(lake.read_rows(g), project(expected))
                self.assertEqual(lake.read(g)[0], {'temperature': 12, 'quality': 3})

                history = {a: a_states, b: b_states, c: c_states, d: d_states,
                           e: e_states, f: f_states, g: expected}
                for snapshot, states in history.items():
                    self.assertEqual(lake.read_rows(snapshot), project(states))
                self.assertEqual(lake.read(fixture.origin), [
                    {'temperature': 10, 'quality': 1},
                    {'temperature': 4, 'quality': 7},
                    {'temperature': 20, 'quality': 1},
                ])


class RollbackCrossCheckTests(unittest.TestCase):
    def test_failed_merge_is_invisible_to_reference_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'lake.sqlite')
            with Lake(path) as lake:
                fixture = Fixture(lake)
                base = fixture.states
                src = fixture.source(0)
                left_states = fixture.correct('left', 'l', [{
                    'source': src, 'op': 'patch',
                    'before': {'temperature': 10}, 'after': {'temperature': 11}}],
                    base)[1]
                left = lake.head('left')
                right_states = fixture.correct('right', 'r', [{
                    'source': src, 'op': 'patch',
                    'before': {'temperature': 10}, 'after': {'temperature': 12}}],
                    base)[1]
                with self.assertRaises(ValueError):
                    reference_merge(base, left_states, right_states, NAMES)
                snapshot = {
                    'head': lake.head('left'),
                    'snapshots': lake.catalog.db.execute(
                        'SELECT * FROM snapshots ORDER BY 1').fetchall(),
                    'receipts': lake.catalog.db.execute(
                        'SELECT * FROM correction_receipts ORDER BY 1,2').fetchall(),
                    'rows': lake.read_rows(left),
                }
                with self.assertRaises(ConflictError):
                    lake.merge('left', 'right')
                self.assertEqual(lake.head('left'), snapshot['head'])
                self.assertEqual(lake.catalog.db.execute(
                    'SELECT * FROM snapshots ORDER BY 1').fetchall(),
                    snapshot['snapshots'])
                self.assertEqual(lake.catalog.db.execute(
                    'SELECT * FROM correction_receipts ORDER BY 1,2').fetchall(),
                    snapshot['receipts'])
                self.assertEqual(lake.read_rows(left), snapshot['rows'])
            with Lake(path) as lake:
                self.assertEqual(lake.read_rows(lake.head('left')), snapshot['rows'])
                self.assertEqual(lake.catalog.db.execute(
                    'SELECT * FROM snapshots ORDER BY 1').fetchall(),
                    snapshot['snapshots'])


if __name__ == '__main__':
    unittest.main()
