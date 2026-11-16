import argparse
import json
import sys

from . import Lake, LakeError, ValidationError
from .catalog import encode

ARGUMENTS = {
    'create': ('fields',), 'fork': ('snapshot', 'branch'),
    'head': ('branch',), 'alter': ('branch', 'changes'),
    'append': ('branch', 'request', 'rows'), 'merge': ('target', 'source'),
    'read': ('snapshot',),
}


def dispatch(lake, command):
    if type(command) is not dict or not isinstance(command.get('op'), str):
        raise ValidationError('command requires op')
    operation = command['op']
    keys = ARGUMENTS.get(operation)
    if keys is None or set(command) != {'op', *keys}:
        raise ValidationError('invalid operation or command keys')
    return getattr(lake, operation)(**{key: command[key] for key in keys})


def reject_constant(value):
    raise ValidationError('non-finite JSON number: ' + value)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline branch lake: one JSON command per line')
    parser.add_argument('--db', required=True)
    args = parser.parse_args(argv)
    status = 0
    with Lake(args.db) as lake:
        for line in sys.stdin:
            try:
                command = json.loads(line, parse_constant=reject_constant)
                print(encode({'ok': dispatch(lake, command)}))
            except (LakeError, json.JSONDecodeError) as exc:
                print(encode({'error': type(exc).__name__, 'message': str(exc)}))
                status = 2
    return status


if __name__ == '__main__':
    raise SystemExit(main())
