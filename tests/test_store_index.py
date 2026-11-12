"""Keyless mocked tests for paginated store discovery."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import store_index as idx

class StoreIndexTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.p = patch.object(idx, 'DB_PATH', Path(self.dir.name) / 'index.sqlite3')
        self.p.start()
        idx.SYNC = {'state':'idle','pages':0,'imported':0,'error':None}
        idx.SCAN = {'state':'idle','total':0,'checked':0,'confirmed':0,'not_detected':0,'errors':0}
    def tearDown(self):
        idx.stop()
        for lock in (idx.SYNC_LOCK,idx.SCAN_LOCK):
            for i in range(200):
                if not lock.locked(): break
                time.sleep(.01)
        self.p.stop()
        self.dir.cleanup()

    def test_connection_closes_when_context_exits(self):
        with idx.open_db() as con:
            self.assertEqual(con.execute('SELECT 1').fetchone(), (1,))
        with self.assertRaises(sqlite3.ProgrammingError):
            con.execute('SELECT 1')

    def test_connection_closes_and_rolls_back_on_error(self):
        with self.assertRaisesRegex(RuntimeError, 'deliberate test error'):
            with idx.open_db() as con:
                con.execute('INSERT INTO apps(appid,name) VALUES(?,?)', (777, 'Rollback test'))
                raise RuntimeError('deliberate test error')
        with self.assertRaises(sqlite3.ProgrammingError):
            con.execute('SELECT 1')
        with idx.open_db() as fresh:
            self.assertIsNone(fresh.execute('SELECT appid FROM apps WHERE appid=777').fetchone())

    def test_page_parses_advancing_cursor(self):
        data={'response':{'apps':[{'appid':21,'name':'Game 21','last_modified':123},
                                  {'appid':14,'name':'Game 14','last_modified':100}],
                          'last_appid':21,'have_more_results':True}}
        rows,cursor,more=idx.parse_page(data,0)
        self.assertEqual(rows,[(14,'Game 14',100),(21,'Game 21',123)])
        self.assertEqual((cursor,more),(21,True))
    def test_bad_page_does_not_advance(self):
        with self.assertRaises(ValueError): idx.parse_page({'response':{'apps': [], 'have_more_results':True,'last_appid':10}},10)
        with self.assertRaises(ValueError): idx.parse_page({'invalid': True},0)
    def test_upsert_preserves_verification_status(self):
        idx.import_page([(44,'Game',0)],44,True)
        with idx.LOCK,idx.open_db() as con:
            con.execute('UPDATE apps SET status=? WHERE appid=44',('confirmed',))
            con.commit()
        idx.import_page([(44,'New game name',123)],44,False)
        with idx.LOCK,idx.open_db() as con:
            self.assertEqual(con.execute('SELECT name,status FROM apps WHERE appid=44').fetchone(),('New game name','confirmed'))
        self.assertTrue(idx.stats()['index_complete'])
    def test_sync_resume_across_pages(self):
        calls=[]
        def fake_fetch(cursor, key):
            calls.append(cursor)
            if cursor==0: return {'response':{'apps':[{'appid':10,'name':'First'}],'last_appid':10,'have_more_results':True}}
            return {'response':{'apps':[{'appid':11,'name':'Second'}],'last_appid':11,'have_more_results':False}}
        with patch.object(idx, 'fetch_page',side_effect=fake_fetch),patch.object(idx,'api_key',return_value='LOCAL_SECRET'):
            result=idx.start_sync()
            self.assertEqual(result['state'],'running')
            for _ in range(200):
                if idx.SYNC['state'] != 'running': break
                time.sleep(.01)
            self.assertEqual(idx.SYNC['state'],'completed')
            self.assertEqual(calls,[0,10])
            self.assertEqual(idx.stats()['total'],2)
            self.assertTrue(idx.stats()['index_complete'])
    def test_scan_persists_and_does_not_promote_nonmatches(self):
        idx.import_page([(1,'One',0),(2,'Two',0),(3,'Three',0)],3,False)
        saved=[]
        def checker(i,item):
            if i==1:return {'id':i,'status':'confirmed','name':'One','set_count':2}
            return {'id':i,'status':'not_detected'}
        def save(result,original):saved.append(result['id'])
        with patch.object(idx,'SCAN_DELAY',0):
            idx.start_scan(set(),checker,save)
            for _ in range(300):
                if idx.SCAN['state'] != 'running':break
                time.sleep(.01)
        self.assertEqual(idx.SCAN['checked'],3)
        self.assertEqual(saved,[1])
        self.assertEqual(idx.stats()['pending'],0)
        self.assertEqual(idx.stats()['confirmed'],1)
        self.assertEqual(idx.stats()['not_detected'],2)

if __name__=='__main__': unittest.main()
