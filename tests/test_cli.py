import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from branchlake import Lake, ValidationError
from branchlake.__main__ import dispatch


class CliTests(unittest.TestCase):
    def test_public_dispatch_rejects_extra_keys(self):
        with Lake() as lake:
            with self.assertRaises(ValidationError):
                dispatch(lake, {'op': 'create', 'fields': [], 'extra': 1})
            self.assertEqual(dispatch(lake, {'op': 'create', 'fields': []})['branch'], 'main')

    def test_json_lines_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'lake.sqlite')
            commands = [
                {'op': 'create', 'fields': [{'path': 'x', 'type': 'int'}]},
                {'op': 'append', 'branch': 'main', 'request': 'r', 'rows': [{'x': 5}]},
                {'op': 'read', 'snapshot': 2},
                {'op': 'read', 'snapshot': 999},
                {'op': 'head', 'branch': 'main'},
            ]
            proc = subprocess.run(
                [sys.executable, '-B', '-m', 'branchlake', '--db', path],
                input='\n'.join(json.dumps(command) for command in commands) + '\n',
                text=True, capture_output=True, cwd=Path(__file__).resolve().parents[1],
                timeout=15, check=False
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            outputs = [json.loads(line) for line in proc.stdout.splitlines()]
            self.assertEqual(outputs[2], {'ok': [{'x': 5}]})
            self.assertEqual(outputs[3]['error'], 'NotFoundError')
            self.assertEqual(outputs[4], {'ok': 2})
            with Lake(path) as lake:
                self.assertEqual(lake.read(lake.head()), [{'x': 5}])
