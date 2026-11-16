import sqlite3
import tempfile
import unittest
from pathlib import Path

from branchlake import Lake
from branchlake.samples import observation_lake


class PublicationTests(unittest.TestCase):
    def test_merge_failure_reopen(self):
        for stage in ('snapshot_inserted', 'head_updated'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / 'lake.sqlite')
                with Lake(path) as lake:
                    old, left, right = observation_lake(lake)
                    historical = {sid: lake.read(sid) for sid in (old, left, right)}
                    count = lake.catalog.db.execute('SELECT count(*) FROM snapshots').fetchone()[0]
                    visited = []

                    def interrupt(actual_stage, snapshot):
                        visited.append(actual_stage)
                        if actual_stage == stage:
                            raise OSError('publication interrupted')

                    lake.catalog.publish_hook = interrupt
                    with self.assertRaisesRegex(OSError, 'publication interrupted'):
                        lake.merge('left', 'right')
                    self.assertIn(stage, visited)
                    self.assertEqual(lake.head('left'), left)
                with Lake(path) as lake:
                    self.assertEqual(lake.head('left'), left)
                    self.assertEqual(lake.catalog.db.execute(
                        'SELECT count(*) FROM snapshots').fetchone()[0], count)
                    for sid, rows in historical.items():
                        self.assertEqual(lake.read(sid), rows)
                    merged = lake.merge('left', 'right')['snapshot']
                    self.assertEqual(len(lake.read(merged)), 3)
                    self.assertIsNone(lake.read(merged)[1]['temp'])

    def test_receipt_failure_rolls_back_batch_and_head(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'lake.sqlite')
            with Lake(path) as lake:
                old = lake.create([{'path': 'x', 'type': 'int'}])['snapshot']
                lake.catalog.db.execute("""
                    CREATE TRIGGER reject_receipt BEFORE INSERT ON receipts
                    BEGIN SELECT RAISE(ABORT, 'receipt unavailable'); END
                """)
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt unavailable'):
                    lake.append('main', 'r', [{'x': 4}])
                self.assertEqual(lake.head(), old)
                self.assertEqual(lake.catalog.db.execute('SELECT count(*) FROM batches').fetchone()[0], 0)
            with Lake(path) as lake:
                self.assertEqual(lake.head(), old)
                self.assertEqual(lake.read(old), [])
                lake.catalog.db.execute('DROP TRIGGER reject_receipt')
                receipt = lake.append('main', 'r', [{'x': 4}])
                self.assertEqual(lake.read(receipt['snapshot']), [{'x': 4}])

    def test_two_connections_observe_complete_publications(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'lake.sqlite')
            with Lake(path) as writer:
                old = writer.create([{'path': 'x', 'type': 'int'}])['snapshot']
                with Lake(path) as reader:
                    writer.append('main', 'r', [{'x': 1}])
                    self.assertEqual(reader.read(reader.head()), [{'x': 1}])
                    self.assertEqual(reader.read(old), [])

    def test_memory_publish_failures_are_atomic(self):
        for operation in ('create', 'alter', 'append', 'merge'):
            with self.subTest(operation=operation), Lake() as lake:
                if operation == 'create':
                    before_head = None
                    action = lambda: lake.create([{'path': 'x', 'type': 'int'}])
                else:
                    root = lake.create([{'path': 'x', 'type': 'int'}])['snapshot']
                    lake.fork(root, 'right')
                    lake.alter('right', [{'op': 'add', 'path': 'y', 'type': 'int'}])
                    before_head = lake.head()
                    actions = {
                        'alter': lambda: lake.alter('main', [
                            {'op': 'add', 'path': 'z', 'type': 'int'}]),
                        'append': lambda: lake.append('main', 'r', [{'x': 1}]),
                        'merge': lambda: lake.merge('main', 'right'),
                    }
                    action = actions[operation]
                tables = ('field_events', 'snapshots', 'branches', 'batches', 'receipts')
                before = {table: lake.catalog.db.execute(
                    'SELECT * FROM ' + table + ' ORDER BY 1').fetchall() for table in tables}

                def fail(stage, snapshot):
                    if stage == 'head_updated':
                        raise RuntimeError('cancel publication')

                lake.catalog.publish_hook = fail
                with self.assertRaisesRegex(RuntimeError, 'cancel publication'):
                    action()
                for table in tables:
                    self.assertEqual(lake.catalog.db.execute(
                        'SELECT * FROM ' + table + ' ORDER BY 1').fetchall(), before[table])
                if before_head is not None:
                    self.assertEqual(lake.head(), before_head)
                lake.catalog.publish_hook = None
                action()
