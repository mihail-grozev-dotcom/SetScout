"""Persistent, opt-in continuous full-store discovery and refresh tests."""
import datetime as dt
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import store_index as idx


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(idx, 'DB_PATH', Path(self.folder.name) / 'store.sqlite3')
        self.db_patch.start()
        idx.SYNC = {'state': 'idle', 'pages': 0, 'imported': 0, 'error': None}
        idx.SCAN = {'state': 'idle', 'total': 0, 'checked': 0,
                    'confirmed': 0, 'not_detected': 0, 'errors': 0, 'automatic': False}
        idx.STOP_SCAN.clear()
        idx.STOP_SYNC.clear()

    def tearDown(self):
        idx.shutdown()
        for lock in (idx.SYNC_LOCK, idx.SCAN_LOCK):
            until = time.monotonic() + 5
            while lock.locked() and time.monotonic() < until:
                time.sleep(.01)
            self.assertFalse(lock.locked(), 'Worker did not stop')
        self.db_patch.stop()
        self.folder.cleanup()

    def await_scan(self):
        for _ in range(500):
            x = idx.SCAN.copy()
            if x['state'] != 'running':
                return x
            time.sleep(.01)
        self.fail('Scan timed out')

    def await_sync(self):
        for _ in range(500):
            x = idx.SYNC.copy()
            if x['state'] != 'running':
                return x
            time.sleep(.01)
        self.fail('Sync timed out')

    def test_opt_in_is_persisted_and_shutdown_keeps_preference(self):
        self.assertFalse(idx.stats()['auto_enabled'])
        with patch.object(idx, 'api_key', return_value='TEST_KEY'):
            idx.set_auto_enabled(True)
            self.assertTrue(idx.stats()['auto_enabled'])
            idx.shutdown()
            self.assertTrue(idx.stats()['auto_enabled'])
            idx.set_auto_enabled(False)
            self.assertFalse(idx.stats()['auto_enabled'])

    def test_initial_complete_then_incremental_refresh(self):
        idx.import_page([(1, 'Previously verified', 20)], 1, False)
        with idx.open_db() as con:
            con.execute("UPDATE apps SET status='confirmed' WHERE appid=1")
        arguments = []
        def fake_page(cursor, key, since=0):
            arguments.append((cursor, key, since))
            return {'response': {'apps': [
                {'appid': 1, 'name': 'Renamed verified', 'last_modified': 30},
                {'appid': 3, 'name': 'New Game', 'last_modified': 30}],
                'last_appid': 3, 'have_more_results': False}}
        with patch.object(idx, 'api_key', return_value='TEST_KEY'), \
             patch.object(idx, 'fetch_page', side_effect=fake_page):
            self.assertEqual(idx.start_sync()['mode'], 'refresh')
            self.assertEqual(self.await_sync()['state'], 'completed')
        self.assertEqual(len(arguments), 1)
        self.assertEqual(arguments[0][0], 0)
        self.assertGreater(arguments[0][2], 0)
        summary = idx.stats()
        self.assertTrue(summary['index_complete'])
        self.assertEqual(summary['total'], 2)
        self.assertEqual(summary['confirmed'], 1)
        self.assertEqual(summary['pending'], 1)
        self.assertTrue(summary['last_refresh'])

    def test_interrupted_refresh_resumes_same_since_and_cursor(self):
        idx.import_page([(1, 'Old', 10)], 1, False)
        calls = []
        def fake_page(cursor, key, since=0):
            calls.append((cursor, since))
            if cursor == 0:
                return {'response': {'apps': [{'appid': 10, 'name': 'Ten'}],
                                      'last_appid': 10, 'have_more_results': True}}
            return {'response': {'apps': [{'appid': 20, 'name': 'Twenty'}],
                                  'last_appid': 20, 'have_more_results': False}}
        with patch.object(idx, 'api_key', return_value='TEST_KEY'), \
             patch.object(idx, 'fetch_page', side_effect=fake_page), \
             patch.object(idx, 'SYNC_PAGES_PER_RUN', 1):
            self.assertEqual(idx.start_sync()['mode'], 'refresh')
            self.assertEqual(self.await_sync()['state'], 'completed')
            self.assertEqual(idx.start_sync()['mode'], 'refresh')
            self.assertEqual(self.await_sync()['state'], 'completed')
        self.assertEqual([x[0] for x in calls], [0, 10])
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertEqual(idx.stats()['total'], 3)
        with idx.open_db() as con:
            self.assertEqual(idx.meta(con, 'refresh_cursor'), '0')
            self.assertEqual(idx.meta(con, 'refresh_since'), '0')

    def test_rate_limit_keeps_app_pending_and_disables_auto(self):
        idx.import_page([(12, 'Game', 0)], 12, False)
        with patch.object(idx, 'api_key', return_value='TEST_KEY'), \
             patch.object(idx, 'SCAN_DELAY', 0):
            idx.set_auto_enabled(True)
            idx.start_scan(set(), lambda *_: {
                'status': 'error', 'reason': 'HTTP 429'}, lambda *_: None, automatic=True)
            result = self.await_scan()
        self.assertEqual(result['state'], 'rate_limited')
        summary = idx.stats()
        self.assertEqual(summary['pending'], 1)
        self.assertFalse(summary['auto_enabled'])
        self.assertIn('HTTP 429', summary['auto_pause_reason'])

    def test_background_processes_pending_and_pause_is_respected(self):
        idx.import_page([(40, 'Game', 0)], 40, False)
        done = threading.Event()
        stop = threading.Event()
        def check(appid, item):
            return {'id': appid, 'status': 'not_detected', 'reason': 'No evidence'}
        def saved(*_):
            self.fail('Nonmatches must not be promoted')
        with patch.object(idx, 'api_key', return_value='TEST_KEY'), \
             patch.object(idx, 'AUTO_POLL_SECONDS', .01), \
             patch.object(idx, 'AUTO_BATCH_GAP_SECONDS', 0), \
             patch.object(idx, 'SCAN_DELAY', 0):
            idx.set_auto_enabled(True)
            thread = threading.Thread(target=idx.automation_loop,
                                      args=(stop, lambda: set(), check, saved), daemon=True)
            thread.start()
            for _ in range(500):
                if idx.stats()['pending'] == 0:
                    break
                time.sleep(.01)
            stop.set()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(idx.stats()['pending'], 0)
        self.assertEqual(idx.stats()['not_detected'], 1)
        self.assertTrue(idx.stats()['auto_enabled'])

if __name__ == '__main__':
    unittest.main()
