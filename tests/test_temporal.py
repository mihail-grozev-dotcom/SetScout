"""Temporal status tests: explicit source evidence, safe unknowns, deadlines."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server


def _result(archived=True):
    return {'id': 620, 'name': 'Portal 2', 'status': 'confirmed', 'verified': True,
            'set_count': 2, 'dlc_set_count': 0, 'set_names': ['Base Game', 'Co-op'],
            'archive_visibility': 'structured_page', 'archived_count': int(archived),
            'steam_archived_items': [{'kind': 'set', 'name': 'Co-op'}] if archived else [],
            'types': ['Modes'] + (['Archived'] if archived else []),
            'checked_at': server.now(), 'verified_at': server.now()}


class TemporalTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        path = Path(self.folder.name) / 'games.json'
        path.write_text(json.dumps({'games': [{'id': 620, 'name': 'Portal 2',
            'source': 'Valve launch list', 'types': ['Modes']}]}), encoding='utf-8')
        self.patch = patch.object(server, 'DB_FILE', path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.folder.cleanup()

    def test_archive_boolean_strings_are_not_false_positives(self):
        groups = [{'name': 'Base Game', 'archived': '0', 'achievements': [
            {'name': 'Test', 'archived': 'false'}, {'name': 'Gone', 'archived': '1'}]},
            {'name': 'DLC', 'archived': 0, 'achievements': []}]
        recs = server.archived_records(groups)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['name'], 'Gone')

    def test_plain_achievement_page_provides_no_archive_evidence(self):
        html = '<div>All Sets (2)</div><div>12 achievements from base game across 2 sets</div>'
        result = server.parse_achievement_page(html)
        self.assertIsNone(result['archived_count'])
        self.assertNotIn('Archived', result['types'])

    def test_positive_archive_writes_event_once(self):
        server.save_result(_result())
        server.save_result(_result())
        data = server.read_db()
        self.assertEqual(len(data['events']), 1)
        self.assertEqual(data['events'][0]['kind'], 'archive')
        self.assertEqual(data['games'][0]['archived_count'], 1)

    def test_missing_archive_data_does_not_erase_observation(self):
        server.save_result(_result())
        check = {**_result(False), 'archive_visibility': 'not_available',
                 'archived_count': None, 'steam_archived_items': []}
        server.save_result(check)
        game = server.read_db()['games'][0]
        self.assertEqual(game['archived_count'], 1)
        self.assertEqual(len(game['steam_archived_items']), 1)

    def test_report_is_labelled_user_report(self):
        item = server.manual_report({'appid': 620, 'title': 'Winter Festival',
            'scope': 'set', 'status': 'limited_active',
            'source_url': 'https://store.steampowered.com/news/',
            'ends_at': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).isoformat(),
            'note': 'Source says the event is temporary.'})
        self.assertEqual(item['provenance'], 'user_report')
        self.assertFalse(server.read_db()['games'][0].get('verified', False))
        self.assertEqual(len(server.read_db()['events']), 1)
        self.assertEqual(server.update_deadlines(), 1)
        self.assertEqual(server.update_deadlines(), 0)
        data = server.read_db()
        self.assertEqual(len(data['events']), 2)
        self.assertFalse(any(e['kind'] == 'archive' for e in data['events']))

    def test_expired_report_never_claims_archived(self):
        server.manual_report({'appid': 620, 'title': 'Yesterday event',
            'scope': 'achievement', 'status': 'limited_active',
            'source_url': 'https://example.org/post',
            'ends_at': (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()})
        server.update_deadlines()
        db = server.read_db()
        self.assertNotIn('Archived', db['games'][0]['types'])
        self.assertTrue(any('archiving unverified' in x['title'] for x in db['events']))

    def test_report_validation(self):
        with self.assertRaisesRegex(ValueError, '[Ss]ource'):
            server.manual_report({'appid': 620, 'title': 'Holiday', 'status': 'limited_active'})
        with self.assertRaisesRegex(ValueError, '[Ss]ource'):
            server.manual_report({'appid': 620, 'title': 'Holiday', 'status': 'archived', 'source_url': 'javascript:alert(1)'})
        with self.assertRaisesRegex(ValueError, 'Only active'):
            server.manual_report({'appid': 620, 'title': 'Holiday', 'status': 'unknown', 'ends_at': '2030-01-01T00:00:00+00:00'})

    def test_report_edit_delete(self):
        args = {'appid': 620, 'title': 'Holiday achievement', 'scope': 'achievement',
            'status': 'limited_active', 'source_url': 'https://example.org/event'}
        original = server.manual_report(args)
        new = server.manual_report({**args, 'report_id': original['id'], 'status': 'archived', 'ends_at': ''})
        self.assertEqual(new['id'], original['id'])
        self.assertEqual(len(server.read_db()['games'][0]['limited_items']), 1)
        self.assertTrue(server.delete_report({'appid': 620, 'report_id': original['id']}))
        self.assertEqual(server.read_db()['games'][0]['limited_items'], [])


if __name__ == '__main__': unittest.main()
