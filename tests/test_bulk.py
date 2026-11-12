"""Tests for the unverified bulk scanner."""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server


class BulkScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'games.json'
        self.db_path.write_text(json.dumps({'games': [
            {'id': 1, 'name': 'Unchecked'},
            {'id': 2, 'name': 'Verified', 'verified': True},
            {'id': 3, 'name': 'Retry', 'last_check_status': 'error'},
            {'id': 4, 'name': 'Unchecked again'}]}))
        self.path_patch = patch.object(server, 'DB_FILE', self.db_path)
        self.path_patch.start()
        self.assertFalse(server.SCAN_LOCK.locked())

    def tearDown(self):
        server.BULK_CANCEL.set()
        deadline = time.monotonic() + 3
        while server.SCAN_LOCK.locked() and time.monotonic() < deadline:
            time.sleep(.01)
        self.path_patch.stop()
        self.tmp.cleanup()

    def await_finish(self):
        for _ in range(400):
            result = server.bulk_snapshot()
            if result['state'] != 'running':
                return result
            time.sleep(.01)
        self.fail('Scan took too long')

    def test_each_unverified_checked_once(self):
        calls = []
        def fake_check(appid, game):
            calls.append(appid)
            if appid == 3:
                return {'id': appid, 'status': 'error', 'checked_at': server.now(), 'reason': 'HTTP 500'}
            return {'id': appid, 'name': game['name'], 'status': 'confirmed',
                    'verified': True, 'set_count': 2, 'types': ['Modes'],
                    'checked_at': server.now()}
        with patch.object(server, 'check_game', side_effect=fake_check), patch.object(server, 'BULK_PAUSE_SECONDS', 0):
            result = server.start_unverified_scan()
            self.assertEqual(result['total'], 3)
            self.assertEqual(self.await_finish()['state'], 'completed')
        self.assertCountEqual(calls, [1, 3, 4])
        final = server.bulk_snapshot()
        self.assertEqual((final['completed'], final['confirmed'], final['errors']), (3, 2, 1))
        self.assertEqual(final['remaining_unverified'], 1)

    def test_cancel_after_current_request(self):
        started = threading.Event()
        proceed = threading.Event()
        calls = []
        def fake_check(appid, game):
            calls.append(appid)
            started.set()
            proceed.wait(3)
            return {'id': appid, 'status': 'not_detected',
                    'checked_at': server.now(), 'reason': 'No positive set evidence'}
        with patch.object(server, 'check_game', side_effect=fake_check), patch.object(server, 'BULK_PAUSE_SECONDS', 0):
            server.start_unverified_scan()
            self.assertTrue(started.wait(2))
            self.assertTrue(server.cancel_unverified_scan()['cancel_requested'])
            proceed.set()
            self.assertEqual(self.await_finish()['state'], 'cancelled')
        self.assertEqual(len(calls), 1)

    def test_empty_scan_unlocks(self):
        self.db_path.write_text(json.dumps({'games': [{'id': 2, 'name': 'Verified', 'verified': True}]}))
        result = server.start_unverified_scan()
        self.assertEqual((result['state'], result['total']), ('completed', 0))
        self.assertFalse(server.SCAN_LOCK.locked())


if __name__ == '__main__':
    unittest.main()
