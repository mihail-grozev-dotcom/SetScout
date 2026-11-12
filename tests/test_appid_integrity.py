"""Regression tests for legacy CK3/Horizon App ID confusion."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server

class AppIDTests(unittest.TestCase):
    def test_seed(self):
        seed = json.loads(server.SEED_FILE.read_text(encoding='utf-8'))
        self.assertEqual(next(g['id'] for g in seed['games'] if g['name'] == 'Crusader Kings III'), 1158310)

    def test_migration_backup_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'games.json'
            db = {'games': [{'id':1151640,'name':'Crusader Kings III','verified':True,
                 'set_count':1,'checked_at':'2026-11-12',
                 'limited_items':[{'id':'r1','title':'User report'}]}],
                 'events':[{'appid':1151640,'kind':'report','game':'Crusader Kings III'},
                           {'appid':1151640,'kind':'archive','game':'Crusader Kings III'}]}
            file.write_text(json.dumps(db), encoding='utf-8')
            with patch.object(server,'DB_FILE',file):
                result=server.read_db()
                game=result['games'][0]
                self.assertEqual(game['id'],1158310)
                self.assertFalse(game.get('verified',False))
                self.assertNotIn('set_count',game)
                self.assertEqual(game['limited_items'][0]['id'],'r1')
                self.assertEqual(len(result['events']),1)
                self.assertEqual(result['events'][0]['appid'],1158310)
                self.assertTrue(Path(str(file)+'.before-appid-fix.bak').exists())
                self.assertEqual(server.read_db()['games'][0]['id'],1158310)

    def test_horizon_is_not_rewritten(self):
        db={'games':[{'id':1151640,'name':'Horizon Zero Dawn Complete Edition'}],
            'events':[],'candidates':[]}
        self.assertFalse(server.repair_starter_game_ids(db))
        self.assertEqual(db['games'][0]['id'],1151640)

    def test_title_mismatch_prevents_false_verification(self):
        page='<title>Steam Community :: Horizon Zero Dawn Complete Edition :: Achievements</title><p>12 achievements from base game across 2 sets</p><p>All Sets (2)</p>'
        with patch.object(server,'fetch',return_value=page):
            result=server.check_game(1151640, {'id':1151640,'name':'Crusader Kings III'})
        self.assertEqual(result['status'],'name_mismatch')
        self.assertFalse(result.get('verified',False))

    def test_matching_rules(self):
        self.assertTrue(server.steam_name_matches('LEGO Batman: Legacy of the Dark Knight', 'LEGO® Batman™: Legacy of the Dark Knight'))
        self.assertFalse(server.steam_name_matches('PowerWash Simulator 2', 'PowerWash Simulator'))
        self.assertFalse(server.steam_name_matches('Crusader Kings III', 'Horizon Zero Dawn Complete Edition'))

if __name__=='__main__':
    unittest.main()
