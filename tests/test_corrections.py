import tempfile
from pathlib import Path

from branchlake import ConflictError, Lake, NotFoundError, ValidationError
from correction_fixture import seed
from tests.reference import merge as reference_merge, patch, remove, state
from tests.support import LakeCase


def fixture_state(lake, refs):
    """Effective state of both fixture sources at the origin snapshot."""
    rows = {tuple(item['source']): item['row'] for item in lake.read_rows(refs['origin'])}
    return {key: state(**row) for key, row in rows.items()}


class ReadRowsTests(LakeCase):
    def test_addresses_order_and_copy(self):
        refs = seed(self.lake)
        expected = [
            {'source': refs['source'], 'row': {'temperature': 10, 'quality': 1}},
            {'source': refs['other_source'], 'row': {'temperature': 4, 'quality': 7}},
        ]
        self.assertEqual(self.lake.read_rows(refs['origin']), expected)
        self.assertEqual(self.lake.read(refs['origin']),
                         [item['row'] for item in expected])
        mutated = self.lake.read_rows(refs['origin'])
        mutated[0]['source'][1] = 99
        mutated[0]['row']['temperature'] = 0
        self.assertEqual(self.lake.read_rows(refs['origin']), expected)
        self.assertEqual(self.lake.read_rows(refs['initial']), [])

    def test_snapshot_validation_matches_read(self):
        seed(self.lake)
        for value in (0, -1, True, 'main', None):
            for reader in (self.lake.read, self.lake.read_rows):
                with self.subTest(value=value, reader=reader.__name__):
                    with self.assertRaises(ValidationError):
                        reader(value)
        with self.assertRaises(NotFoundError):
            self.lake.read_rows(99999)

    def test_addresses_are_stable_after_correction_and_merge(self):
        refs = seed(self.lake)
        src = refs['source']
        fixed = self.lake.correct('left', 'rm', [
            {'source': src, 'op': 'remove',
             'before': {'temperature': 10, 'quality': 1}}])['snapshot']
        self.assertEqual(self.lake.read_rows(fixed), [
            {'source': refs['other_source'],
             'row': {'temperature': 4, 'quality': 7}}])
        merged = self.lake.merge('left', 'right')['snapshot']
        self.assertEqual(self.lake.read_rows(merged), [
            {'source': refs['other_source'],
             'row': {'temperature': 4, 'quality': 7}}])
        self.assertEqual(self.lake.read_rows(refs['origin']), [
            {'source': refs['source'], 'row': {'temperature': 10, 'quality': 1}},
            {'source': refs['other_source'], 'row': {'temperature': 4, 'quality': 7}},
        ])


class CorrectTests(LakeCase):
    def test_joint_fixture_example(self):
        refs = seed(self.lake)
        src = refs['source']
        self.lake.correct('left', 'temperature-fix', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 10}, 'after': {'temperature': None}}])
        self.lake.correct('right', 'quality-fix', [{
            'source': src, 'op': 'patch',
            'before': {'quality': 1}, 'after': {'quality': 2}}])
        merged = self.lake.merge('left', 'right')['snapshot']
        self.assertEqual(self.lake.read_rows(merged), [
            {'source': src, 'row': {'temperature': None, 'quality': 2}},
            {'source': refs['other_source'],
             'row': {'temperature': 4, 'quality': 7}},
        ])
        self.assertEqual(self.lake.read(refs['origin']), [
            {'temperature': 10, 'quality': 1},
            {'temperature': 4, 'quality': 7},
        ])

    def test_request_replay_survives_schema_and_retraction(self):
        refs = seed(self.lake)
        src = refs['source']
        changes = [{'source': src, 'op': 'patch',
                    'before': {'temperature': 10}, 'after': {'temperature': 11}}]
        first = self.lake.correct('left', 'req', changes)
        self.assertEqual(set(first), {'snapshot', 'request'})
        self.lake.alter('left', [{'op': 'rename', 'path': 'temperature', 'to': 'temp'}])
        head = self.lake.head('left')
        self.assertEqual(self.lake.correct('left', 'req', changes), first)
        self.assertEqual(self.lake.head('left'), head)
        self.lake.correct('left', 'rm', [{
            'source': src, 'op': 'remove', 'before': {'temp': 11, 'quality': 1}}])
        self.assertEqual(self.lake.correct('left', 'req', changes), first)
        with self.assertRaises(ConflictError):
            self.lake.correct('left', 'req', [
                {'source': src, 'op': 'patch',
                 'before': {'temp': 11}, 'after': {'temp': 12}}])

    def test_replay_is_rejected_before_old_image_checks(self):
        refs = seed(self.lake)
        src = refs['source']
        changes = [{'source': src, 'op': 'remove',
                    'before': {'temperature': 10, 'quality': 1}}]
        first = self.lake.correct('left', 'once', changes)
        head = self.lake.head('left')
        # The stored before image no longer matches, yet replay still succeeds.
        self.assertEqual(self.lake.correct('left', 'once', changes), first)
        self.assertEqual(self.lake.head('left'), head)

    def test_invalid_tail_is_atomic_and_request_stays_free(self):
        refs = seed(self.lake)
        src, other = refs['source'], refs['other_source']
        head = self.lake.head('left')
        with self.assertRaises(ConflictError):
            self.lake.correct('left', 'bad', [
                {'source': src, 'op': 'patch',
                 'before': {'temperature': 10}, 'after': {'temperature': 11}},
                {'source': other, 'op': 'remove',
                 'before': {'temperature': 4, 'quality': 99}},
            ])
        self.assertEqual(self.lake.head('left'), head)
        self.assertEqual(self.lake.read_rows(head), self.lake.read_rows(refs['origin']))
        receipt = self.lake.correct('left', 'bad', [
            {'source': src, 'op': 'patch',
             'before': {'temperature': 10}, 'after': {'temperature': 11}}])
        self.assertEqual(self.lake.read_rows(receipt['snapshot'])[0]['row']['temperature'], 11)

    def test_parameter_validation(self):
        refs = seed(self.lake)
        src = refs['source']
        cursor = [{'source': src, 'op': 'remove',
                   'before': {'temperature': 10, 'quality': 1}}]
        cases = [
            ('left', '', cursor),
            ('left', 'x' * 129, cursor),
            ('left', 7, cursor),
            ('left', 'x', []),
            ('left', 'x', None),
            ('left', 'x', [{'source': src, 'op': 'patch',
                            'before': {}, 'after': {}}]),
            ('left', 'x', [{'source': src, 'op': 'patch',
                            'before': {'temperature': 1}, 'after': {}}]),
            ('left', 'x', [{'source': src, 'op': 'patch',
                            'before': {'temperature': 1}, 'after': {'quality': 1}}]),
            ('left', 'x', [{'source': src, 'op': 'patch',
                            'before': {'missing': 1}, 'after': {'missing': 1}}]),
            ('left', 'x', [{'source': src, 'op': 'patch',
                            'before': {'temperature': True}, 'after': {'temperature': 1}}]),
            ('left', 'x', [{'source': src, 'op': 'remove',
                            'before': {'temperature': 10}}]),
            ('left', 'x', [{'source': src, 'op': 'remove',
                            'before': {'temperature': 10, 'quality': 1, 'n': 1}}]),
            ('left', 'x', [{'source': src, 'op': 'remove',
                            'before': {'temperature': '10', 'quality': 1}}]),
            ('left', 'x', [{'source': src, 'op': 'remove',
                            'before': {'temperature': 10, 'quality': 1}, 'after': {}}]),
            ('left', 'x', [{'source': [True, 0], 'op': 'remove',
                            'before': {'temperature': 10, 'quality': 1}}]),
            ('left', 'x', [{'source': [1], 'op': 'remove',
                            'before': {'temperature': 10, 'quality': 1}}]),
            ('left', 'x', [{'source': [0, 0], 'op': 'remove',
                            'before': {'temperature': 10, 'quality': 1}}]),
            ('left', 'x', [{'source': [1, -1], 'op': 'remove',
                            'before': {'temperature': 10, 'quality': 1}}]),
            ('left', 'x', [{'source': src, 'op': 'remove',
                            'before': {'temperature': 10, 'quality': 1}},
                           {'source': src, 'op': 'patch',
                            'before': {'temperature': 10}, 'after': {'temperature': 2}}]),
            ('left', 'x', [{'source': src, 'op': 'unknown'}]),
            ('left', 'x', [{'source': src, 'op': 'remove',
                            'before': {'temperature': 10, 'quality': 1}, 'extra': 1}]),
        ]
        head = self.lake.head('left')
        for branch, request, changes in cases:
            with self.subTest(request=request, changes=changes):
                with self.assertRaises(ValidationError):
                    self.lake.correct(branch, request, changes)
                self.assertEqual(self.lake.head('left'), head)
        with self.assertRaises(NotFoundError):
            self.lake.correct('missing', 'x', cursor)

    def test_conflicts_and_retraction_lock(self):
        refs = seed(self.lake)
        src = refs['source']
        self.lake.fork(refs['origin'], 'other')
        foreign = self.lake.append('other', 'f', [
            {'temperature': 1, 'quality': 2}])
        self.lake.correct('left', 'rm', [
            {'source': src, 'op': 'remove',
             'before': {'temperature': 10, 'quality': 1}}])
        conflicts = [
            [{'source': [foreign['batch'], 0], 'op': 'remove',
              'before': {'temperature': 1, 'quality': 2}}],
            [{'source': [1, 9], 'op': 'remove',
              'before': {'temperature': 10, 'quality': 1}}],
            [{'source': src, 'op': 'patch',
              'before': {'temperature': 10}, 'after': {'temperature': 11}}],
            [{'source': refs['other_source'], 'op': 'patch',
              'before': {'temperature': 99}, 'after': {'temperature': 11}}],
            [{'source': refs['other_source'], 'op': 'remove',
              'before': {'temperature': 4, 'quality': 99}}],
        ]
        head = self.lake.head('left')
        for changes in conflicts:
            with self.subTest(changes=changes):
                with self.assertRaises(ConflictError):
                    self.lake.correct('left', 'c', changes)
                self.assertEqual(self.lake.head('left'), head)

    def test_numeric_and_object_equality(self):
        root = self.lake.create([
            {'path': 'f', 'type': 'float'},
            {'path': 'm', 'type': 'struct'},
            {'path': 'm.x', 'type': 'int'},
        ])['snapshot']
        self.lake.fork(root, 'left')
        self.lake.fork(root, 'right')
        batch = self.lake.append('left', 'a', [
            {'f': 1.0, 'm': {'x': 5}}])['batch']
        self.lake.merge('right', 'left')
        source = [batch, 0]
        receipt = self.lake.correct('right', 'p', [{
            'source': source, 'op': 'patch',
            'before': {'f': 1, 'm.x': 5}, 'after': {'f': 2, 'm.x': None}}])
        self.assertEqual(self.lake.read(receipt['snapshot']),
                         [{'f': 2.0, 'm': {'x': None}}])
        with self.assertRaises(ValidationError):
            self.lake.correct('right', 'q', [{
                'source': source, 'op': 'patch',
                'before': {'m': {}}, 'after': {'m': {}}}])

    def test_explicit_null_is_not_absent(self):
        refs = seed(self.lake)
        src = refs['source']
        self.lake.correct('left', 'n', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 10}, 'after': {'temperature': None}}])
        # Restoring the original value is not a change relative to the ancestor.
        receipt = self.lake.correct('left', 'back', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': None}, 'after': {'temperature': 10}}])
        self.assertEqual(self.lake.read_rows(receipt['snapshot'])[0]['row'],
                         {'temperature': 10, 'quality': 1})
        merged = self.lake.merge('left', 'right')['snapshot']
        self.assertEqual(self.lake.read(merged), self.lake.read(refs['origin']))

    def test_default_value_is_used_for_absent_cells(self):
        refs = seed(self.lake)
        src = refs['source']
        # The first fixture row stores {} so temperature/quality come from the
        # creation defaults 10 and 1.
        receipt = self.lake.correct('left', 'p', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 10}, 'after': {'temperature': 12}}])
        self.assertEqual(self.lake.read_rows(receipt['snapshot'])[0]['row'],
                         {'temperature': 12, 'quality': 1})

    def test_rename_keeps_value_and_recreate_does_not_inherit(self):
        refs = seed(self.lake)
        src = refs['source']
        self.lake.correct('left', 'n', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 10}, 'after': {'temperature': None}}])
        self.lake.alter('left', [
            {'op': 'rename', 'path': 'temperature', 'to': 'old_temperature'},
            {'op': 'add', 'path': 'temperature', 'type': 'int', 'default': 20},
        ])
        rows = self.lake.read_rows(self.lake.head('left'))
        self.assertEqual(rows[0]['row'],
                         {'old_temperature': None, 'temperature': 20, 'quality': 1})
        self.assertEqual(rows[1]['row'],
                         {'old_temperature': 4, 'temperature': 20, 'quality': 7})
        self.assertEqual(self.lake.read(refs['origin'])[0], {'temperature': 10, 'quality': 1})

    def test_fork_does_not_share_request_namespace(self):
        refs = seed(self.lake)
        src = refs['source']
        changes = [{'source': src, 'op': 'patch',
                    'before': {'temperature': 10}, 'after': {'temperature': 11}}]
        self.lake.correct('left', 'req', changes)
        child = self.lake.fork(refs['origin'], 'child')
        receipt = self.lake.correct('child', 'req', changes)
        self.assertNotEqual(receipt['snapshot'], child['snapshot'])
        self.assertEqual(self.lake.read_rows(receipt['snapshot'])[0]['row']['temperature'], 11)

    def test_append_namespace_is_independent(self):
        refs = seed(self.lake)
        src = refs['source']
        same = 'shared-name'
        self.lake.append('left', same, [{'temperature': 3, 'quality': 4}])
        receipt = self.lake.correct('left', same, [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 10}, 'after': {'temperature': 11}}])
        self.assertEqual(receipt['request'], same)
        self.assertEqual(self.lake.read_rows(receipt['snapshot'])[0]['row']['temperature'], 11)

    def test_correction_after_merge_and_chained_merges(self):
        refs = seed(self.lake)
        src = refs['source']
        self.lake.correct('left', 'l', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 10}, 'after': {'temperature': 11}}])
        self.lake.correct('right', 'r', [{
            'source': src, 'op': 'patch',
            'before': {'quality': 1}, 'after': {'quality': 2}}])
        first = self.lake.merge('left', 'right')['snapshot']
        receipt = self.lake.correct('left', 'after', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 11}, 'after': {'temperature': 13}}])
        second = self.lake.merge('left', 'right')['snapshot']
        self.assertEqual(self.lake.read_rows(second), [
            {'source': src, 'row': {'temperature': 13, 'quality': 2}},
            {'source': refs['other_source'],
             'row': {'temperature': 4, 'quality': 7}},
        ])
        # The already merged snapshot keeps its state, and repeating merges
        # neither duplicates rows nor changes their content.
        self.assertEqual(self.lake.read(first)[0], {'temperature': 11, 'quality': 2})
        repeat = self.lake.merge('left', 'right')['snapshot']
        self.assertEqual(self.lake.read_rows(repeat), self.lake.read_rows(second))
        self.assertEqual(self.lake.read_rows(self.lake.merge('left', 'left')['snapshot']),
                         self.lake.read_rows(receipt['snapshot']))

    def test_shared_third_branch_source_is_merged_once(self):
        root = self.lake.create([{'path': 'x', 'type': 'int', 'default': 0}])['snapshot']
        for branch in ('left', 'right', 'third'):
            self.lake.fork(root, branch)
        receipt = self.lake.append('third', 't', [{'x': 5}])
        source = [receipt['batch'], 0]
        self.lake.merge('left', 'third')
        self.lake.merge('right', 'third')
        self.lake.correct('left', 'p', [{
            'source': source, 'op': 'patch',
            'before': {'x': 5}, 'after': {'x': 6}}])
        merged = self.lake.merge('left', 'right')['snapshot']
        self.assertEqual(self.lake.read_rows(merged),
                         [{'source': source, 'row': {'x': 6}}])
        self.lake.correct('right', 'q', [{
            'source': source, 'op': 'patch',
            'before': {'x': 5}, 'after': {'x': 7}}])
        with self.assertRaises(ConflictError):
            self.lake.merge('left', 'right')

    def test_hidden_correction_survives_projection_removal(self):
        refs = seed(self.lake)
        src = refs['source']
        self.lake.correct('left', 'p', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 10}, 'after': {'temperature': 20}}])
        self.lake.alter('left', [{'op': 'drop', 'path': 'temperature'}])
        merged = self.lake.merge('left', 'right')['snapshot']
        self.assertEqual(self.lake.read_rows(merged)[0]['row'], {'quality': 1})
        self.lake.fork(refs['origin'], 'third')
        self.lake.correct('third', 'rm', [{
            'source': src, 'op': 'remove',
            'before': {'temperature': 10, 'quality': 1}}])
        self.lake.fork(merged, 'review')
        with self.assertRaises(ConflictError):
            self.lake.merge('review', 'third')
        before = self.lake.head('review')
        self.assertEqual(self.lake.read_rows(before), self.lake.read_rows(merged))
        self.assertEqual(before, merged)


class ReferenceMergeTests(LakeCase):
    def check_against_reference(self, target, source):
        with Lake() as lake:
            refs = seed(lake)
            src = refs['source']
            left_op = [{'source': src, 'op': 'patch',
                        'before': {'temperature': 10}, 'after': {'temperature': None}}]
            right_op = [{'source': src, 'op': 'patch',
                         'before': {'quality': 1}, 'after': {'quality': 2}}]
            lake.correct('left', 'l', left_op)
            lake.correct('right', 'r', right_op)
            origin = fixture_state(lake, refs)
            left_state = patch(origin, tuple(src), left_op[0]['before'],
                               left_op[0]['after'])
            right_state = patch(origin, tuple(src), right_op[0]['before'],
                                right_op[0]['after'])
            names = ['temperature', 'quality']
            expected_state = reference_merge(origin, left_state, right_state, names)
            expected = []
            for key in sorted(expected_state,
                              key=lambda item: tuple(int(x) for x in item)):
                entry = expected_state[key]
                if not entry['retracted']:
                    expected.append({'source': list(key), 'row': entry['cells']})
            merged = lake.merge(target, source)['snapshot']
            self.assertEqual(lake.read_rows(merged), expected)

    def test_compatible_cells_merge_both_directions(self):
        self.check_against_reference('left', 'right')
        self.check_against_reference('right', 'left')

    def test_retraction_with_untouched_side(self):
        refs = seed(self.lake)
        src = refs['source']
        self.lake.correct('left', 'rm', [{
            'source': src, 'op': 'remove',
            'before': {'temperature': 10, 'quality': 1}}])
        origin = fixture_state(self.lake, refs)
        left_state = remove(origin, tuple(src), {'temperature': 10, 'quality': 1})
        expected = reference_merge(origin, left_state, origin, ['temperature', 'quality'])
        expected_rows = [{'source': list(key), 'row': entry['cells']}
                         for key, entry in expected.items() if not entry['retracted']]
        merged = self.lake.merge('left', 'right')['snapshot']
        self.assertEqual(self.lake.read_rows(merged), expected_rows)
        self.assertEqual([item['source'] for item in expected_rows],
                         [refs['other_source']])

    def test_retraction_vs_modification_rejected(self):
        refs = seed(self.lake)
        src = refs['source']
        self.lake.correct('left', 'rm', [{
            'source': src, 'op': 'remove',
            'before': {'temperature': 10, 'quality': 1}}])
        self.lake.correct('right', 'q', [{
            'source': src, 'op': 'patch',
            'before': {'quality': 1}, 'after': {'quality': 2}}])
        head = self.lake.head('left')
        with self.assertRaises(ConflictError):
            self.lake.merge('left', 'right')
        self.assertEqual(self.lake.head('left'), head)

    def test_same_cell_different_values_rejected(self):
        refs = seed(self.lake)
        src = refs['source']
        for target, value in (('left', 11), ('right', 12)):
            self.lake.correct(target, 'p' + target[0], [{
                'source': src, 'op': 'patch',
                'before': {'temperature': 10}, 'after': {'temperature': value}}])
        head = self.lake.head('left')
        with self.assertRaises(ConflictError):
            self.lake.merge('left', 'right')
        self.assertEqual(self.lake.head('left'), head)

    def test_hidden_identity_still_participates(self):
        refs = seed(self.lake)
        src = refs['source']
        self.lake.correct('left', 'null-temp', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 10}, 'after': {'temperature': None}}])
        self.lake.alter('left', [{'op': 'drop', 'path': 'temperature'}])
        self.lake.correct('right', 'rm', [{
            'source': src, 'op': 'remove',
            'before': {'temperature': 10, 'quality': 1}}])
        with self.assertRaises(ConflictError):
            self.lake.merge('left', 'right')

    def test_deleted_identity_corrections_merge_silently(self):
        refs = seed(self.lake)
        src = refs['source']
        self.lake.correct('left', 'l', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 10}, 'after': {'temperature': 11}}])
        self.lake.correct('right', 'r', [{
            'source': src, 'op': 'patch',
            'before': {'quality': 1}, 'after': {'quality': 2}}])
        self.lake.alter('left', [{'op': 'drop', 'path': 'quality'}])
        merged = self.lake.merge('left', 'right')['snapshot']
        self.assertEqual(self.lake.read_rows(merged)[0]['row'], {'temperature': 11})
        self.assertEqual(self.lake.read_rows(merged)[1]['row'], {'temperature': 4})
        self.assertEqual(self.lake.read(merged)[0]['temperature'], 11)

    def test_equal_final_rows_do_not_skip_ancestor_conflict(self):
        refs = seed(self.lake)
        src = refs['source']
        a = self.lake.correct('left', 'A', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 10}, 'after': {'temperature': 11}}])['snapshot']
        b = self.lake.correct('right', 'B', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 10}, 'after': {'temperature': 12}}])['snapshot']
        self.lake.fork(a, 'saved_a')
        self.lake.fork(b, 'saved_b')
        self.lake.correct('left', 'L', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 11}, 'after': {'temperature': 12}}])
        c = self.lake.merge('left', 'saved_b')['snapshot']
        self.assertEqual(self.lake.read(c)[0], {'temperature': 12, 'quality': 1})
        self.lake.correct('right', 'R', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 12}, 'after': {'temperature': 11}}])
        d = self.lake.merge('right', 'saved_a')['snapshot']
        self.assertEqual(self.lake.read(d)[0], {'temperature': 11, 'quality': 1})
        self.lake.correct('right', 'E', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 11}, 'after': {'temperature': 12}}])
        self.assertEqual(self.lake.read(self.lake.head('left'))[0],
                         {'temperature': 12, 'quality': 1})
        self.assertEqual(self.lake.read(self.lake.head('right'))[0],
                         {'temperature': 12, 'quality': 1})
        left_head, right_head = self.lake.head('left'), self.lake.head('right')
        with self.assertRaises(ConflictError):
            self.lake.merge('left', 'right')
        self.assertEqual(self.lake.head('left'), left_head)
        self.assertEqual(self.lake.head('right'), right_head)


class PublicationTests(LakeCase):
    def test_correct_rolls_back_at_both_stages(self):
        for stage in ('snapshot_inserted', 'head_updated'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / 'lake.sqlite')
                with Lake(path) as lake:
                    refs = seed(lake)
                    src = refs['source']
                    changes = [{'source': src, 'op': 'patch',
                                'before': {'temperature': 10},
                                'after': {'temperature': 11}}]
                    head = lake.head('left')
                    tables = ('snapshots', 'branches', 'correction_receipts')
                    before = {table: lake.catalog.db.execute(
                        'SELECT * FROM ' + table + ' ORDER BY 1').fetchall()
                        for table in tables}

                    def interrupt(actual, snapshot):
                        if actual == stage:
                            raise OSError('publication interrupted')

                    lake.catalog.publish_hook = interrupt
                    with self.assertRaisesRegex(OSError, 'publication interrupted'):
                        lake.correct('left', 'p', changes)
                    for table in tables:
                        self.assertEqual(lake.catalog.db.execute(
                            'SELECT * FROM ' + table + ' ORDER BY 1').fetchall(),
                            before[table])
                    self.assertEqual(lake.head('left'), head)
                    lake.catalog.publish_hook = None
                    receipt = lake.correct('left', 'p', changes)
                    self.assertEqual(receipt['request'], 'p')
                with Lake(path) as lake:
                    self.assertEqual(lake.read_rows(lake.head('left'))[0]['row'],
                                     {'temperature': 11, 'quality': 1})
                    self.assertEqual(lake.correct('left', 'p', changes)['request'], 'p')

    def test_merge_failure_preserves_corrections_and_receipts(self):
        for stage in ('snapshot_inserted', 'head_updated'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / 'lake.sqlite')
                with Lake(path) as lake:
                    refs = seed(lake)
                    src = refs['source']
                    lake.correct('left', 'l', [{
                        'source': src, 'op': 'patch',
                        'before': {'temperature': 10}, 'after': {'temperature': 11}}])
                    lake.correct('right', 'r', [{
                        'source': src, 'op': 'patch',
                        'before': {'quality': 1}, 'after': {'quality': 2}}])
                    heads = (lake.head('left'), lake.head('right'))
                    snaps = lake.catalog.db.execute(
                        'SELECT count(*) FROM snapshots').fetchone()[0]
                    receipts = lake.catalog.db.execute(
                        'SELECT * FROM correction_receipts ORDER BY 1,2').fetchall()

                    def interrupt(actual, snapshot):
                        if actual == stage:
                            raise OSError('publication interrupted')

                    lake.catalog.publish_hook = interrupt
                    with self.assertRaises(OSError):
                        lake.merge('left', 'right')
                    lake.catalog.publish_hook = None
                    self.assertEqual((lake.head('left'), lake.head('right')), heads)
                    self.assertEqual(lake.catalog.db.execute(
                        'SELECT count(*) FROM snapshots').fetchone()[0], snaps)
                    self.assertEqual(lake.catalog.db.execute(
                        'SELECT * FROM correction_receipts ORDER BY 1,2').fetchall(),
                        receipts)
                    merged = lake.merge('left', 'right')['snapshot']
                    self.assertEqual(lake.read_rows(merged)[0]['row'],
                                     {'temperature': 11, 'quality': 2})
                with Lake(path) as lake:
                    self.assertEqual(lake.read_rows(merged)[0]['row'],
                                     {'temperature': 11, 'quality': 2})
                    stored = lake.catalog.db.execute(
                        'SELECT snapshot FROM correction_receipts '
                        'WHERE branch=? AND request=?', ('left', 'l')).fetchone()[0]
                    self.assertEqual(lake.correct('left', 'l', [{
                        'source': src, 'op': 'patch',
                        'before': {'temperature': 10},
                        'after': {'temperature': 11}}])['snapshot'], stored)

    def test_original_batches_and_snapshots_never_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'lake.sqlite')
            with Lake(path) as lake:
                refs = seed(lake)
                payloads = lake.catalog.db.execute(
                    'SELECT id,payload FROM batches ORDER BY id').fetchall()
                origin_rows = lake.read(refs['origin'])
                src = refs['source']
                lake.correct('left', 'p', [{
                    'source': src, 'op': 'patch',
                    'before': {'temperature': 10}, 'after': {'temperature': 20}}])
                lake.correct('left', 'rm', [{
                    'source': refs['other_source'], 'op': 'remove',
                    'before': {'temperature': 4, 'quality': 7}}])
                lake.merge('left', 'right')
                self.assertEqual(lake.catalog.db.execute(
                    'SELECT id,payload FROM batches ORDER BY id').fetchall(), payloads)
                self.assertEqual(lake.read(refs['origin']), origin_rows)
            with Lake(path) as lake:
                self.assertEqual(lake.catalog.db.execute(
                    'SELECT id,payload FROM batches ORDER BY id').fetchall(), payloads)
                self.assertEqual(lake.read(refs['origin']), origin_rows)
                self.assertEqual(lake.read_rows(lake.head('left')), [
                    {'source': src, 'row': {'temperature': 20, 'quality': 1}}])


class CrisscrossTests(LakeCase):
    """The frozen two-compatible-ancestor table, in both merge directions."""

    def test_consolidated_ancestor_keeps_both_corrections(self):
        for swap in (False, True):
            with self.subTest(swap=swap), Lake() as lake:
                refs = seed(lake)
                src = refs['source']
                a = lake.correct('left', 'A', [{
                    'source': src, 'op': 'patch',
                    'before': {'temperature': 10}, 'after': {'temperature': 11}}])['snapshot']
                b = lake.correct('right', 'B', [{
                    'source': src, 'op': 'patch',
                    'before': {'quality': 1}, 'after': {'quality': 2}}])['snapshot']
                lake.fork(a, 'saved_a')
                lake.fork(b, 'saved_b')
                c = lake.merge('left', 'saved_b')['snapshot']
                d = lake.merge('right', 'saved_a')['snapshot']
                e = lake.correct('left', 'E', [{
                    'source': src, 'op': 'patch',
                    'before': {'temperature': 11}, 'after': {'temperature': 12}}])['snapshot']
                f = lake.correct('right', 'F', [{
                    'source': src, 'op': 'patch',
                    'before': {'quality': 2}, 'after': {'quality': 3}}])['snapshot']
                g = lake.merge('right' if swap else 'left',
                               'left' if swap else 'right')['snapshot']
                expected = {
                    a: (11, 1), b: (10, 2), c: (11, 2),
                    d: (11, 2), e: (12, 2), f: (11, 3), g: (12, 3),
                }
                for snapshot, (temperature, quality) in expected.items():
                    self.assertEqual(lake.read(snapshot)[0],
                                     {'temperature': temperature, 'quality': quality})
                self.assertEqual(lake.read(refs['origin'])[0],
                                 {'temperature': 10, 'quality': 1})
