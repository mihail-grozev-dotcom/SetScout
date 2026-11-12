"""Optional, paginated full-store game discovery for SetScout.

SQLite contains the broad discovery pool separately from the hand-curated game list.
The private Steam API key is read locally and never returned to browser clients.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / 'steam_catalog.sqlite3'
KEY_PATH = HERE / '.steam_api_key'
URL = 'https://api.steampowered.com/IStoreService/GetAppList/v1/'
SYNC_PAGE_SIZE = 5000
SYNC_PAGES_PER_RUN = 24
SCAN_LIMIT = 50
SCAN_DELAY = 1.3
LOCK = threading.RLock()
SYNC_LOCK = threading.Lock()
SCAN_LOCK = threading.Lock()
SYNC = {'state': 'idle', 'pages': 0, 'imported': 0, 'error': None}
SCAN = {'state': 'idle', 'total': 0, 'checked': 0, 'confirmed': 0, 'not_detected': 0, 'errors': 0}
STOP_SYNC = threading.Event()
STOP_SCAN = threading.Event()


def api_key() -> str:
    key = os.environ.get('STEAM_WEB_API_KEY', '').strip()
    if not key and KEY_PATH.exists():
        key = KEY_PATH.read_text(encoding='utf-8').strip()
    return key


def open_db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.execute('PRAGMA busy_timeout=20000')
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE IF NOT EXISTS apps (appid INTEGER PRIMARY KEY, name TEXT NOT NULL, '
                'modified INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT \'pending\', '
                'checked_at TEXT, note TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    return con


def meta(con, key, default=''):
    row = con.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def set_meta(con, key, value):
    con.execute('INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (key, str(value)))


def stats() -> dict:
    with LOCK, open_db() as con:
        total, pending, confirmed, other, errors = con.execute('''
            SELECT COUNT(*),
              COALESCE(SUM(status='pending'),0), COALESCE(SUM(status='confirmed'),0),
              COALESCE(SUM(status='not_detected'),0),
              COALESCE(SUM(status IN ('error','name_mismatch')),0)
            FROM apps''').fetchone()
        with LOCK:
            return {'key_configured': bool(api_key()), 'total': total, 'pending': pending,
                    'confirmed': confirmed, 'not_detected': other, 'errors': errors,
                    'cursor': int(meta(con, 'cursor', '0')),
                    'index_complete': meta(con, 'index_complete') == '1',
                    'last_sync': meta(con, 'last_sync'),
                    'sync': dict(SYNC), 'scan': dict(SCAN)}


def parse_page(body: dict, cursor: int) -> tuple[list[tuple[int, str, int]], int, bool]:
    response = body.get('response')
    if not isinstance(response, dict) or not isinstance(response.get('apps'), list):
        raise ValueError('Steam app list response did not contain an apps array')
    result = []
    seen = set()
    for item in response['apps']:
        try:
            appid = int(item['appid'])
            name = str(item.get('name') or '').strip()[:250]
            modified = max(0, int(item.get('last_modified') or 0))
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        if appid > cursor and appid <= 2_147_483_647 and name and appid not in seen:
            result.append((appid, name, modified))
            seen.add(appid)
    result.sort(key=lambda t: t[0])
    returned_cursor = int(response.get('last_appid') or (result[-1][0] if result else cursor))
    more = response.get('have_more_results')
    if more is None:
        more = len(response['apps']) >= SYNC_PAGE_SIZE
    if more and (returned_cursor <= cursor or not result):
        raise ValueError('Steam returned a non-advancing cursor; refusing to loop')
    return result, returned_cursor, bool(more)


def fetch_page(cursor: int, key: str) -> dict:
    # Authenticate via header; no API key in URLs, logs or browser responses.
    params = {'input_json': json.dumps({'include_games': True, 'include_dlc': False,
                                       'include_software': False, 'include_videos': False,
                                       'include_hardware': False, 'max_results': SYNC_PAGE_SIZE,
                                       'last_appid': cursor}, separators=(',', ':'))}
    req = Request(URL + '?' + urlencode(params),
                  headers={'x-webapi-key': key, 'Accept': 'application/json',
                           'User-Agent': 'SetScout/5 local app catalogue index'})
    with urlopen(req, timeout=25) as resp:
        raw = resp.read(5_000_001)
    if len(raw) > 5_000_000:
        raise ValueError('Steam response too large')
    return json.loads(raw)


def import_page(rows, cursor, more):
    with LOCK, open_db() as con:
        con.executemany('''INSERT INTO apps(appid,name,modified) VALUES(?,?,?)
            ON CONFLICT(appid) DO UPDATE SET name=excluded.name, modified=excluded.modified''', rows)
        set_meta(con, 'cursor', cursor)
        set_meta(con, 'index_complete', '0' if more else '1')
        if not more:
            set_meta(con, 'last_sync', dt.datetime.now(dt.timezone.utc).isoformat())
        con.commit()


def _sync_worker():
    global SYNC
    try:
        with LOCK, open_db() as con:
            cursor = int(meta(con, 'cursor', '0'))
        key = api_key()
        for _ in range(SYNC_PAGES_PER_RUN):
            if STOP_SYNC.is_set():
                break
            rows, next_cursor, more = parse_page(fetch_page(cursor, key), cursor)
            import_page(rows, next_cursor, more)
            cursor = next_cursor
            with LOCK:
                SYNC['pages'] += 1
                SYNC['imported'] += len(rows)
            if not more:
                break
            if STOP_SYNC.wait(0.65):
                break
        with LOCK:
            SYNC['state'] = 'stopped' if STOP_SYNC.is_set() else 'completed'
    except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        # Never include a URL or exception message that could echo credentials.
        with LOCK:
            SYNC['state'] = 'error'
            SYNC['error'] = ('Steam API rejected the request (HTTP %s)' % exc.code) if isinstance(exc, HTTPError) else type(exc).__name__ + ': ' + str(exc)[:150]
    finally:
        SYNC_LOCK.release()


def start_sync():
    global SYNC
    if not api_key():
        raise ValueError('Steam API key missing. Add it to .steam_api_key in the SetScout directory.')
    if not SYNC_LOCK.acquire(blocking=False):
        raise ValueError('Catalogue indexing is already running.')
    try:
        STOP_SYNC.clear()
        with LOCK, open_db() as con:
            if meta(con, 'index_complete') == '1':
                SYNC_LOCK.release()
                return {'state': 'completed', 'already_complete': True}
        with LOCK:
            SYNC = {'state': 'running', 'pages': 0, 'imported': 0, 'error': None}
        threading.Thread(target=_sync_worker, daemon=True).start()
        return {'state': 'running'}
    except Exception:
        if SYNC_LOCK.locked():
            SYNC_LOCK.release()
        raise


def choose_scan_batch(known: set[int], limit: int = SCAN_LIMIT):
    with LOCK, open_db() as con:
        # Already listed games are handled by SetScout's own recheck scanner.
        # Mark them in the separate index so they cannot block its queue.
        if known:
            con.executemany("UPDATE apps SET status='already_listed' WHERE appid=? AND status='pending'",
                            ((appid,) for appid in known))
            con.commit()
        rows = con.execute('SELECT appid,name FROM apps WHERE status=? ORDER BY appid LIMIT ?',
                           ('pending', limit)).fetchall()
    return rows


def _scan_worker(items, check_game, save_result):
    try:
        for appid, name in items:
            if STOP_SCAN.is_set():
                break
            result = None
            try:
                result = check_game(appid, {'id': appid, 'name': name, 'source': 'Full Steam store index'})
                status = result['status']
                if status == 'confirmed':
                    save_result(result, {'id': appid, 'name': name, 'source': 'Full Steam store index'})
                note = str(result.get('reason') or '')[:240]
            except Exception as exc:
                status, note = 'error', type(exc).__name__
            with LOCK, open_db() as con:
                con.execute('UPDATE apps SET status=?,checked_at=?,note=? WHERE appid=?',
                            (status, dt.datetime.now(dt.timezone.utc).isoformat(), note, appid))
                con.commit()
                SCAN['checked'] += 1
                SCAN['confirmed'] += (status == 'confirmed')
                SCAN['not_detected'] += (status == 'not_detected')
                SCAN['errors'] += status not in ('confirmed', 'not_detected')
            if status == 'error' and 'HTTP 429' in note:
                with LOCK:
                    SCAN['state'] = 'rate_limited'
                break
            if STOP_SCAN.wait(SCAN_DELAY):
                break
        with LOCK:
            if SCAN['state'] == 'running':
                SCAN['state'] = 'stopped' if STOP_SCAN.is_set() else 'completed'
    finally:
        SCAN_LOCK.release()


def start_scan(known: set[int], check_game, save_result):
    global SCAN
    if not SCAN_LOCK.acquire(blocking=False):
        raise ValueError('Store-index scanner is already running.')
    try:
        items = choose_scan_batch(known)
        with LOCK:
            SCAN = {'state': 'running' if items else 'completed', 'total': len(items),
                    'checked': 0, 'confirmed': 0, 'not_detected': 0, 'errors': 0}
        STOP_SCAN.clear()
        if items:
            threading.Thread(target=_scan_worker, args=(items, check_game, save_result), daemon=True).start()
        else:
            SCAN_LOCK.release()
        return dict(SCAN)
    except Exception:
        if SCAN_LOCK.locked():
            SCAN_LOCK.release()
        raise


def stop():
    STOP_SYNC.set()
    STOP_SCAN.set()
