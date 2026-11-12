"""Pending candidate queue and reviewed history tests."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server


class CandidateQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'games.json'
        self.path.write_text(json.dumps({'games': [], 'candidates': [
            {'id': 1001, 'name': 'Good', 'status': 'pending'},
            {'id': 1002, 'name': 'Not detected', 'status': 'pending'},
            {'id': 1003, 'name': 'Error', 'status': 'pending'},
            {'id': 1004, 'name': 'Already reviewed', 'status': 'not_detected',
             'checked_at': server.now()}]}))
        self.patcher = patch.object(server, 'DB_FILE', self.path)
        self.patcher.start()
        self.assertFalse(server.SCAN_LOCK.locked())

    def tearDown(self):
        server.BULK_CANCEL.set()
        for _ in range(200):
            if not server.SCAN_LOCK.locked():
                break
            time.sleep(.01)
        self.patcher.stop()
        self.tmp.cleanup()

    def wait_for_scan(self):
        for _ in range(300):
            result = server.bulk_snapshot()
            if result['state'] != 'running':
                return result
            time.sleep(.01)
        self.fail('Scan timed out')

    def test_distinct_pending_checked_error_counts(self):
        self.assertEqual(server.candidate_counts(server.read_db()['candidates']),
                         {'pending': 3, 'not_detected': 1, 'errors': 0})

    def test_pending_scan_drains_first_checks_preserves_review_history(self):
        called = []
        def fake_check(appid, item):
            called.append(appid)
            result = {'id': appid, 'checked_at': server.now()}
            if appid == 1001:
                return {**result, 'status': 'confirmed', 'verified': True,
                        'name': 'Good', 'set_count': 2, 'types': ['Modes']}
            if appid == 1002:
                return {**result, 'status': 'not_detected', 'reason': 'No matching sets'}
            return {**result, 'status': 'error', 'reason': 'HTTP 500'}
        with patch.object(server, 'check_game', side_effect=fake_check), \
             patch.object(server, 'BULK_PAUSE_SECONDS', 0):
            self.assertEqual(server.start_pending_scan()['total'], 3)
            self.assertEqual(self.wait_for_scan()['state'], 'completed')
        self.assertCountEqual(called, [1001, 1002, 1003])
        db = server.read_db()
        self.assertEqual(server.candidate_counts(db['candidates']),
                         {'pending': 0, 'not_detected': 2, 'errors': 1})
        self.assertEqual(len(db['games']), 1)
        self.assertEqual(server.start_pending_scan()['total'], 0)

    def test_rate_limit_pauses_and_leaves_remaining_candidates_untouched(self):
        with patch.object(server, 'check_game',
                          return_value={'id': 1001, 'checked_at': server.now(),
                                        'status': 'error', 'reason': 'HTTP 429'}), \
             patch.object(server, 'BULK_PAUSE_SECONDS', 0):
            self.assertEqual(server.start_pending_scan()['total'], 3)
            self.assertEqual(self.wait_for_scan()['state'], 'rate_limited')
        self.assertEqual(server.candidate_counts(server.read_db()['candidates']),
                         {'pending': 2, 'not_detected': 1, 'errors': 1})


if __name__ == '__main__':
    unittest.main()
