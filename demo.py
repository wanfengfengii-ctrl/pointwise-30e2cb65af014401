import json
import tempfile
from pathlib import Path

from branchlake import ConflictError, Lake
from branchlake.samples import observation_lake


def main():
    with tempfile.TemporaryDirectory(prefix='branchlake-demo-') as directory:
        path = str(Path(directory) / 'research.sqlite')
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
            print(json.dumps({'historical_snapshot': old, 'merged_snapshot': merged,
                              'rows': rows, 'rejected_conflict': conflict},
                             indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
