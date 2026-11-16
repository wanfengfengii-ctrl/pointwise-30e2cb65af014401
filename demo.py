import json
import tempfile
from pathlib import Path

from branchlake import ConflictError, Lake
from branchlake.samples import observation_lake
from correction_fixture import seed


def schema_demo(path):
    """The original two-branch schema evolution and merge."""
    with Lake(path) as lake:
        old, left, right = observation_lake(lake)
        historical = {snapshot: lake.read(snapshot) for snapshot in (old, left, right)}
        merged = lake.merge('left', 'right')['snapshot']
        rows = lake.read(merged)
        assert rows[0]['retired'] == 100 and rows[0]['legacy_code'] == 9
        assert rows[1]['temp'] is None and rows[1]['meta']['quality'] is None
        assert rows[2]['count'] == 88
        lake.fork(old, 'conflict_a')
        lake.fork(old, 'conflict_b')
        for branch in ('conflict_a', 'conflict_b'):
            lake.alter(branch, [{'op': 'add', 'path': 'new', 'type': 'int'}])
        before = lake.head('conflict_a')
        try:
            lake.merge('conflict_a', 'conflict_b')
        except ConflictError as exc:
            conflict = str(exc)
        else:
            raise AssertionError('independent fields at the same path must conflict')
        assert lake.head('conflict_a') == before
    with Lake(path) as lake:
        assert lake.read(merged) == rows
        for snapshot, expected in historical.items():
            assert lake.read(snapshot) == expected
    return dict(historical_snapshot=old, merged_snapshot=merged, rows=rows,
                rejected_conflict=conflict)


def correction_demo(path):
    """Real source corrections on two branches, their merge, and a rejection."""
    with Lake(path) as lake:
        refs = seed(lake)
        src = refs['source']
        origin_rows = lake.read(refs['origin'])
        left = lake.correct('left', 'temperature-fix', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 10}, 'after': {'temperature': None},
        }])['snapshot']
        right = lake.correct('right', 'quality-fix', [{
            'source': src, 'op': 'patch',
            'before': {'quality': 1}, 'after': {'quality': 2},
        }])['snapshot']
        merged = lake.merge('left', 'right')['snapshot']
        merged_rows = lake.read_rows(merged)
        assert merged_rows == [
            {'source': src, 'row': {'temperature': None, 'quality': 2}},
            {'source': refs['other_source'],
             'row': {'temperature': 4, 'quality': 7}},
        ], merged_rows
        assert lake.read(refs['origin']) == origin_rows
        # Replaying the same correction after the merge returns the old receipt.
        replay = lake.correct('left', 'temperature-fix', [{
            'source': src, 'op': 'patch',
            'before': {'temperature': 10}, 'after': {'temperature': None},
        }])
        assert replay['snapshot'] == left

        # A genuine conflict: the same cell corrected to different values.
        conflict_left = lake.correct('left', 'clash', [{
            'source': refs['other_source'], 'op': 'patch',
            'before': {'temperature': 4}, 'after': {'temperature': 5},
        }])['snapshot']
        lake.correct('right', 'clash', [{
            'source': refs['other_source'], 'op': 'patch',
            'before': {'temperature': 4}, 'after': {'temperature': 6},
        }])
        before_conflict = lake.head('left')
        try:
            lake.merge('left', 'right')
        except ConflictError as exc:
            rejected = str(exc)
        else:
            raise AssertionError('same-cell corrections must conflict')
        assert lake.head('left') == before_conflict
        rejected_rows = lake.read_rows(before_conflict)
        assert rejected_rows[1]['row'] == {'temperature': 5, 'quality': 7}
        assert rejected_rows[0]['row'] == {'temperature': None, 'quality': 2}
    with Lake(path) as lake:
        assert lake.read_rows(merged) == merged_rows
        assert lake.read(refs['origin']) == origin_rows
        assert lake.read_rows(before_conflict) == rejected_rows
        reopened_origin = lake.read(refs['origin'])
    return dict(
        correction_snapshot=left,
        merged_snapshot=merged,
        merged_rows=merged_rows,
        preserved_origin_rows=origin_rows,
        replay_snapshot=replay['snapshot'],
        rejected_conflict=rejected,
        rejected_branch_head=before_conflict,
        rejected_branch_rows=rejected_rows,
        origin_after_reopen=reopened_origin,
    )


def main():
    with tempfile.TemporaryDirectory(prefix='branchlake-demo-') as directory:
        base = Path(directory)
        schema = schema_demo(str(base / 'research.sqlite'))
        correction = correction_demo(str(base / 'corrections.sqlite'))
        print(json.dumps({'schema_merge': schema, 'correction_merge': correction},
                         indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == '__main__':
    main()
