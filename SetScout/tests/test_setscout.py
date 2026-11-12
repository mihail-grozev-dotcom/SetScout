"""Run with: python -m unittest discover -s tests -v"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server


PORTAL = '''<!doctype html><title>Steam Community :: Portal 2 :: Achievements</title>
<div>51 achievements (22 rare)</div><div>51 from base game across 2 sets</div>
<div>Show Global Stats</div><nav>All Sets (2)</nav><h2>Single Player</h2>ACHIEVEMENTS (29)'''
FORZA = '''<!doctype html><title>Steam Community :: Forza Horizon 5 :: Achievements</title>
<div>164 achievements</div><p>110 from base game across 1 set</p>
<p>54 from DLC across 2 sets</p><nav>All Sets (3)</nav>'''
CYBERPUNK = '''<!doctype html><title>Steam Community :: Cyberpunk 2077 :: Achievements</title>
<div>57 achievements (21 rare)</div><h2>ACHIEVEMENTS (36)</h2>'''
JSON_PAGE = '''<script>window.STATE={"groups":[{"id":0,"name":"Base Game","achievements":[{"name":"One"}]},{"id":1,"name":"Holiday","archived":true,"dlcappid":19,"achievements":[{"name":"A \\"quoted\\" one","archived":true}]}]};</script>'''


class ParserTests(unittest.TestCase):
    def test_multi_set_html(self):
        out = server.parse_achievement_page(PORTAL)
        self.assertEqual(out['set_count'], 2)
        self.assertEqual(out['dlc_set_count'], 0)
        self.assertIn('Modes', out['types'])
    def test_dlc_counts(self):
        out = server.parse_achievement_page(FORZA)
        self.assertEqual(out['set_count'], 3)
        self.assertEqual(out['dlc_set_count'], 2)
        self.assertIn('DLC', out['types'])
    def test_no_false_positive_ordinary_achievements(self):
        self.assertIsNone(server.parse_achievement_page(CYBERPUNK))
    def test_no_false_positive_default_group(self):
        self.assertIsNone(server.parse_achievement_page('<script>{"groups":[{"name":"Base Game","achievements":[]}]}</script>'))
    def test_json_groups_archive(self):
        out = server.parse_achievement_page(JSON_PAGE)
        self.assertEqual(out['set_count'], 2)
        self.assertEqual(out['dlc_set_count'], 1)
        self.assertIn('Archived', out['types'])
    def test_title_and_ids(self):
        self.assertEqual(server.page_title(PORTAL), 'Portal 2')
        self.assertEqual(server.app_id_from('https://steamcommunity.com/achievements/620?l=english'), 620)
        with self.assertRaises(ValueError):
            server.app_id_from('https://evil.test/app/620')
    def test_chart_candidate_reader(self):
        self.assertEqual(server.candidates_from_charts({'response': {'ranks': [{'appid': 440}]}}), [(440, '')])
    def test_featured_candidate_reader(self):
        self.assertEqual(server.candidates_from_featured({'top_sellers': {'items': [{'id': 620, 'name': 'Portal 2'}]}}), [(620, 'Portal 2')])


class DBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'games.json'
        self.db_path.write_text(json.dumps({'games': [{'id': 620, 'name': 'Portal 2', 'source': 'Valve launch list'}]}))
        self.patcher = patch.object(server, 'DB_FILE', self.db_path)
        self.patcher.start()
    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
    def test_save_unconfirmed_never_adds_game(self):
        res = {'id': 1091500, 'status': 'not_detected', 'checked_at': server.now(), 'reason': 'no sets'}
        self.assertEqual(server.save_result(res, {'id': 1091500, 'name': 'Cyberpunk 2077'}), 'not_detected')
        data = server.read_db()
        self.assertEqual(len(data['games']), 1)
        self.assertEqual(len(data['candidates']), 1)
    def test_confirmed_promotion_and_timestamp(self):
        res = {'id': 440, 'name': 'Team Fortress 2', 'verified': True, 'verified_at': server.now(),
               'status': 'confirmed', 'checked_at': server.now(), 'set_count': 16, 'types': ['Modes']}
        self.assertEqual(server.save_result(res), 'added')
        data = server.read_db()
        self.assertEqual(len(data['games']), 2)
        self.assertTrue(next(g for g in data['games'] if g['id']==440)['verified'])
    def test_failed_check_does_not_erase_listed(self):
        res = {'id': 620, 'status': 'error', 'checked_at': server.now(), 'reason': 'HTTP 429'}
        server.save_result(res)
        self.assertEqual(server.read_db()['games'][0]['source'], 'Valve launch list')
        self.assertEqual(server.read_db()['games'][0]['last_check_status'], 'error')
    def test_mocked_end_to_end_discovery(self):
        def fake_fetch(url):
            if 'GetMostPlayedGames' in url:
                return json.dumps({'response':{'ranks':[{'appid': 440, 'name': 'Team Fortress 2'}]}})
            if 'featuredcategories' in url:
                return json.dumps({'top_sellers': {'items': [{'id':1091500, 'name':'Cyberpunk 2077'}]}})
            if '/achievements/440' in url:
                return PORTAL.replace('Portal 2', 'Team Fortress 2')
            if '/achievements/1091500' in url:
                return CYBERPUNK
            raise AssertionError('Unexpected URL '+url)
        with patch.object(server, 'fetch', side_effect=fake_fetch):
            report = server.run_scan(limit=2, discover=True)
        self.assertEqual(report['checked'], 2)
        self.assertEqual(report['new_games'], 1)
        self.assertEqual(report['not_detected'], 1)
        db = server.read_db()
        self.assertTrue(next(g for g in db['games'] if g['id'] == 440)['verified'])
        self.assertEqual(next(c for c in db['candidates'] if c['id']==1091500)['status'], 'not_detected')


if __name__ == '__main__':
    unittest.main()
