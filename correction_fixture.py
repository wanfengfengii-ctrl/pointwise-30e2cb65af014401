"""Finite source-address fixture using only the existing Branchlake API."""
import json

from branchlake import Lake

FIELDS = [
    {'path': 'temperature', 'type': 'int', 'default': 10},
    {'path': 'quality', 'type': 'int', 'default': 1},
]
BASE_ROWS = [{'temperature': 10, 'quality': 1},
             {'temperature': 4, 'quality': 7}]


def seed(lake):
    """Initialize an empty lake and fork the same two-row snapshot."""
    initial = lake.create(FIELDS)['snapshot']
    receipt = lake.append('main', 'observations', [
        {}, {'temperature': 4, 'quality': 7},
    ])
    origin = receipt['snapshot']
    lake.fork(origin, 'left')
    lake.fork(origin, 'right')
    return {
        'initial': initial,
        'origin': origin,
        'source': [receipt['batch'], 0],
        'other_source': [receipt['batch'], 1],
    }


def main():
    with Lake() as lake:
        refs = seed(lake)
        assert lake.read(refs['initial']) == []
        assert lake.read(refs['origin']) == BASE_ROWS
        assert lake.head('left') == lake.head('right') == refs['origin']
        # Exercise the existing shared-batch union without implementing corrections.
        merged = lake.merge('left', 'right')['snapshot']
        assert lake.read(merged) == BASE_ROWS
        assert lake.read(refs['origin']) == BASE_ROWS
        print(json.dumps({
            'refs': refs,
            'expected_read_rows_before_correction': [
                {'source': refs['source'], 'row': BASE_ROWS[0]},
                {'source': refs['other_source'], 'row': BASE_ROWS[1]},
            ],
            'existing_merge_snapshot': merged,
            'existing_merge_rows': lake.read(merged),
        }, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
