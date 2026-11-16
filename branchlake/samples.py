"""A finite research-observation workflow shared by the demo and regressions."""


def observation_lake(lake):
    lake.create([
        {'path': 'sample', 'type': 'int'},
        {'path': 'legacy', 'type': 'int', 'default': 0},
        {'path': 'retired', 'type': 'int', 'default': -1},
        {'path': 'meta', 'type': 'struct'},
        {'path': 'meta.site', 'type': 'str', 'default': 'unknown'},
    ])
    old = lake.append('main', 'base', [
        {'sample': 0, 'legacy': 9, 'retired': 44, 'meta': {'site': 'A'}}
    ])['snapshot']
    lake.fork(old, 'left')
    lake.fork(old, 'right')
    lake.alter('left', [
        {'op': 'rename', 'path': 'legacy', 'to': 'legacy_code'},
        {'op': 'add', 'path': 'temp', 'type': 'float', 'default': 7},
        {'op': 'add', 'path': 'meta.quality', 'type': 'int', 'default': 99},
    ])
    lake.alter('right', [
        {'op': 'drop', 'path': 'retired'},
        {'op': 'add', 'path': 'retired', 'type': 'int', 'default': 100},
        {'op': 'add', 'path': 'count', 'type': 'int', 'default': 3},
    ])
    left = lake.append('left', 'left-observation', [
        {'sample': 1, 'legacy_code': 10, 'retired': 45,
         'temp': None, 'meta': {'site': 'L', 'quality': None}}
    ])['snapshot']
    right = lake.append('right', 'right-observation', [
        {'sample': 2, 'legacy': 11, 'retired': 55, 'count': 88, 'meta': {'site': 'R'}}
    ])['snapshot']
    return old, left, right
