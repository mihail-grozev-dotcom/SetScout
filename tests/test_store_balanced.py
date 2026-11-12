"""Balanced scan: two in-flight fetches, adaptive pacing, and durable checkpoints."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import store_index as idx


class BalancedTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(idx, 'DB_PATH', Path(self.folder.name) / 'store.sqlite3')
        self.db_patch.start()
        idx.SCAN = {'state': 'idle', 'total': 0, 'checked': 0, 'confirmed': 0,
                    'not_detected': 0, 'errors': 0, 'automatic': False}
        idx.STOP_SCAN.clear()
        idx.STOP_SYNC.clear()

    def tearDown(self):
        idx.shutdown()
        deadline = time.monotonic() + 5
        while idx.SCAN_LOCK.locked() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(idx.SCAN_LOCK.locked(), 'Balanced worker did not finish')
        self.db_patch.stop()
        self.folder.cleanup()

    def wait_for_finish(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with idx.LOCK:
                value = idx.SCAN.copy()
            if value['state'] != 'running':
                return value
            time.sleep(.01)
        self.fail('Scan did not finish')

    def test_balanced_uses_two_workers_and_records_each_game_once(self):
        idx.import_page([(x, f'Game {x}', 0) for x in range(1, 6)], 5, False)
        gate = threading.Barrier(2, timeout=4)
        seen = []
        lock = threading.Lock()
        concurrent = [0, 0]

        def check(appid, _item):
            with lock:
                seen.append(appid)
                concurrent[0] += 1
                concurrent[1] = max(concurrent[1], concurrent[0])
            if appid in (1, 2):
                gate.wait()   # Proves they overlap; sequential mode would time out.
            time.sleep(.015)
            with lock:
                concurrent[0] -= 1
            return {'id': appid, 'status': 'not_detected'}

        with patch.object(idx, 'SCAN_DELAY', 0), patch.object(idx, 'SCAN_MIN_DELAY', 0):
            result = idx.start_scan(set(), check, lambda *_: self.fail('Not detected'))
            self.assertEqual(result['workers'], 2)
            self.assertEqual(result['mode'], 'balanced')
            self.assertEqual(self.wait_for_finish()['state'], 'completed')
        self.assertEqual(concurrent[1], 2)
        self.assertCountEqual(seen, list(range(1, 6)))
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(idx.stats()['pending'], 0)
        self.assertEqual(idx.stats()['not_detected'], 5)
        self.assertEqual(idx.start_scan(set(), check, lambda *_: None)['total'], 0)

    def test_rate_limit_stops_early_and_retains_pending(self):
        idx.import_page([(x, f'Game {x}', 0) for x in range(1, 8)], 7, False)
        calls = []
        def check(appid, item):
            calls.append(appid)
            return {'id': appid, 'status': 'error', 'reason': 'HTTP 429'}
        with patch.object(idx, 'SCAN_DELAY', 0):
            idx.start_scan(set(), check, lambda *_: None)
            value = self.wait_for_finish()
        self.assertEqual(value['state'], 'rate_limited')
        self.assertLessEqual(len(calls), 2)
        self.assertEqual(idx.stats()['pending'], 7)
        self.assertEqual(idx.stats()['confirmed'], 0)

    def test_stop_does_not_schedule_another_batch_of_requests(self):
        idx.import_page([(x, f'Game {x}', 0) for x in range(1, 10)], 9, False)
        gate = threading.Event()
        active = threading.Event()
        calls = []
        def check(appid, _item):
            calls.append(appid)
            active.set()
            gate.wait(3)
            return {'id': appid, 'status': 'not_detected'}
        with patch.object(idx, 'SCAN_DELAY', 0):
            idx.start_scan(set(), check, lambda *_: None)
            self.assertTrue(active.wait(2))
            idx.stop()
            gate.set()
            value = self.wait_for_finish()
        self.assertEqual(value['state'], 'stopped')
        self.assertLessEqual(len(calls), 2)
        self.assertGreaterEqual(idx.stats()['pending'], 7)

    def test_pacer_reacts_to_errors_and_recovers(self):
        pace = idx.AdaptivePacer()
        first = pace.delay
        for _ in range(idx.SCAN_RECOVERY_COUNT):
            pace.observe('not_detected', '')
        self.assertLess(pace.delay, first)
        pace.observe('error', 'HTTP 503')
        self.assertGreater(pace.delay, first)
        backoff = pace.delay
        pace.observe('error', 'HTTP 404')
        self.assertEqual(pace.delay, backoff)
        for _ in range(idx.SCAN_RECOVERY_COUNT):
            pace.observe('confirmed', '')
        self.assertLess(pace.delay, backoff)


if __name__ == '__main__':
    unittest.main()
