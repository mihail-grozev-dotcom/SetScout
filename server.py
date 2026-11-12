"""SetScout v4: local Achievement Sets discovery, temporal status, and activity feed.

Python 3.10+; no dependencies or Steam API key. Runs only on 127.0.0.1.
Steam's public HTML and public discovery feeds can change at any time.
The program never treats an HTTP error or an ordinary achievements list as sets.
"""
from __future__ import annotations

import datetime as dt
import difflib
import html
import json
import os
import re
import shutil
import threading
import store_index
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
DB_FILE = HERE / 'games.json'
SEED_FILE = HERE / 'games.example.json'
DB_LOCK = threading.RLock()
SCAN_LOCK = threading.Lock()
BULK_STATE_LOCK = threading.Lock()
BULK_CANCEL = threading.Event()
BULK_PROGRESS: dict = {'state': 'idle', 'total': 0, 'completed': 0,
                       'confirmed': 0, 'not_detected': 0, 'errors': 0}
BULK_PAUSE_SECONDS = 0.75  # Keep a respectful gap between requests to Steam.
USER_AGENT = 'Mozilla/5.0 (compatible; SetScout/3.0; locally run personal achievement catalogue)'
PORT = int(os.environ.get('SETSCOUT_PORT', '8765'))
MAX_FETCH = 6 * 1024 * 1024
AUTO_INTERVAL_HOURS = 6
MAX_CANDIDATES = 1800
FETCH_TIMEOUT = 9


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def parse_date(value: str | None) -> dt.datetime:
    try:
        return dt.datetime.fromisoformat((value or '').replace('Z', '+00:00')).astimezone(dt.timezone.utc)
    except (ValueError, TypeError):
        return dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


def defaults(db: dict) -> dict:
    db.setdefault('updated_at', now()[:10])
    db.setdefault('games', [])
    db.setdefault('candidates', [])
    db.setdefault('events', [])
    db.setdefault('auto_enabled', True)
    db.setdefault('auto_hours', AUTO_INTERVAL_HOURS)
    db.setdefault('scan', {'last_run': None, 'last_result': None, 'last_sources': {}})
    return db


def repair_starter_game_ids(db: dict) -> bool:
    """Fix an incorrect v1-v4 starter ID, preserving user reports and history.

    1151640 is Horizon Zero Dawn, NOT Crusader Kings III (1158310).
    Only touch the bad legacy pairing. Never rewrite an actual Horizon entry.
    """
    old_id, new_id = 1151640, 1158310
    old = next((g for g in db['games'] if str(g.get('id')) == str(old_id)
                and str(g.get('name', '')).strip().casefold() == 'crusader kings iii'), None)
    if old is None:
        return False

    correct = next((g for g in db['games'] if str(g.get('id')) == str(new_id)), None)
    if correct and correct is not old:
        # Preserve user reports if the corrected game already exists.
        reports = correct.setdefault('limited_items', [])
        known = {r.get('id') for r in reports}
        reports.extend(r for r in old.get('limited_items', []) if r.get('id') not in known)
        db['games'].remove(old)
    else:
        old['id'] = new_id
        # All these were obtained for Horizon and are invalid for Crusader Kings III.
        for field in ('verified', 'verified_at', 'checked_at', 'set_count',
                      'dlc_set_count', 'set_names', 'achievement_entries',
                      'archived_count', 'steam_archived_items', 'archive_visibility',
                      'url', 'last_check_status', 'last_check_note'):
            old.pop(field, None)
        old['types'] = []
        old['last_check_note'] = 'Starter App ID corrected; Steam recheck required.'
    db['candidates'] = [c for c in db['candidates'] if str(c.get('id')) != str(new_id)]
    retained = []
    for event in db['events']:
        if str(event.get('appid')) != str(old_id):
            retained.append(event)
        elif event.get('kind') in {'report', 'deadline'}:
            event['appid'] = new_id
            event['game'] = 'Crusader Kings III'
            retained.append(event)
        # Discard wrongly attributed automated achievement-set/archive events.
    db['events'] = retained
    return True


def read_db() -> dict:
    with DB_LOCK:
        if not DB_FILE.exists():
            # Local writable state is never part of the public repository.
            # Exclusive create avoids clobbering an existing user's database.
            try:
                with DB_FILE.open('x', encoding='utf-8') as new_db:
                    new_db.write(SEED_FILE.read_text(encoding='utf-8'))
            except FileExistsError:
                pass
        db = defaults(json.loads(DB_FILE.read_text(encoding='utf-8')))
        if repair_starter_game_ids(db):
            # Preserve the original on-disk database before the one-time correction.
            backup = DB_FILE.with_name(DB_FILE.name + '.before-appid-fix.bak')
            if not backup.exists():
                shutil.copy2(DB_FILE, backup)
            write_db(db)
        return db


def write_db(db: dict) -> None:
    with DB_LOCK:
        db['updated_at'] = now()[:10]
        temp = DB_FILE.with_suffix('.json.tmp')
        temp.write_text(json.dumps(db, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temp.replace(DB_FILE)


def update_db(callback):
    with DB_LOCK:
        db = read_db()
        result = callback(db)
        write_db(db)
        return result


def app_id_from(value: object) -> int:
    raw = str(value or '').strip()
    if re.fullmatch(r'\d{1,10}', raw):
        appid = int(raw)
    else:
        parsed = urlparse(raw)
        if parsed.hostname not in {'store.steampowered.com', 'steamcommunity.com'}:
            raise ValueError('Enter a numeric Steam App ID or Steam store/achievements URL.')
        match = re.search(r'/(?:app|stats|achievements)/(\d{1,10})(?:/|$)', parsed.path, re.I)
        if not match:
            raise ValueError('Steam URL does not contain an App ID.')
        appid = int(match.group(1))
    if not 1 <= appid <= 2_147_483_647:
        raise ValueError('Invalid Steam App ID.')
    return appid


def fetch(url: str) -> str:
    request = Request(url, headers={
        'User-Agent': USER_AGENT,
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept': 'text/html,application/json;q=0.9,*/*;q=0.5',
    })
    with urlopen(request, timeout=FETCH_TIMEOUT) as resp:
        if resp.status != 200:
            raise RuntimeError(f'HTTP {resp.status}')
        return resp.read(MAX_FETCH + 1)[:MAX_FETCH].decode('utf-8', 'replace')


def find_groups(page: str) -> list[dict]:
    """Parse optional embedded groups array, without executing page scripts."""
    decoder = json.JSONDecoder()
    variants = [page, html.unescape(page)]
    if '\\"groups\\"' in page:
        variants.append(page.replace('\\"', '"'))
    pattern = re.compile(r'["\']?groups["\']?\s*:\s*(\[)', re.I)
    for variant in variants:
        for match in pattern.finditer(variant):
            try:
                data, _ = decoder.raw_decode(variant[match.start(1):])
            except (json.JSONDecodeError, ValueError):
                continue
            if (isinstance(data, list) and data and len(data) <= 60
                    and all(isinstance(group, dict) for group in data)
                    and any(isinstance(g.get('achievements'), list) for g in data)):
                return data
    return []


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'svg'}:
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'svg'}:
            self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def archived_flag(value: object) -> bool:
    """Only an explicit archive flag counts, never truthiness of text '0'."""
    return value is True or value == 1 or (isinstance(value, str) and value.strip().casefold() in {'true', 'yes', '1'})


def archived_records(groups: list[dict]) -> list[dict]:
    """Archived data is available ONLY if explicitly included in group JSON.

    Note: Steam public pages can hide archived items for viewers who did not
    earn them. No records here means UNKNOWN, not no archived achievements.
    """
    found = []
    for index, group in enumerate(groups):
        name = str(group.get('name') or f'Set {index + 1}')[:120]
        if archived_flag(group.get('archived')):
            found.append({'kind': 'set', 'name': name})
        entries = group.get('achievements', [])
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and archived_flag(entry.get('archived')):
                    found.append({'kind': 'achievement', 'name': str(entry.get('name') or entry.get('displayName') or 'Archived achievement')[:120], 'set': name})
    return found[:100]


def add_event(db: dict, appid: int, game_name: str, kind: str, title: str,
              detail: str = '', dedupe_key: str | None = None) -> bool:
    if dedupe_key and any(e.get('key') == dedupe_key for e in db['events']):
        return False
    db['events'].append({'id': str(uuid.uuid4()), 'appid': appid,
        'game': game_name[:140], 'kind': kind, 'title': title[:180],
        'detail': detail[:320], 'created_at': now(), 'key': dedupe_key})
    db['events'] = db['events'][-300:]
    return True


def manual_report(payload: dict) -> dict:
    """Record sourced claims without ever calling them Steam-confirmed."""
    appid = app_id_from(payload.get('appid'))
    title = str(payload.get('title', '')).strip()[:140]
    scope = str(payload.get('scope', 'set')).strip()
    status = str(payload.get('status', 'unknown')).strip()
    source_url = str(payload.get('source_url', '')).strip()[:1000]
    note = str(payload.get('note', '')).strip()[:500]
    ends_at = str(payload.get('ends_at') or '').strip()
    if not title or len(title) < 2:
        raise ValueError('Enter an achievement or set name.')
    if scope not in {'set', 'achievement'} or status not in {'limited_active', 'archived', 'unknown'}:
        raise ValueError('Invalid scope or status.')
    if source_url:
        parsed = urlparse(source_url)
        if parsed.scheme not in {'https', 'http'} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError('Source must be a valid http(s) URL.')
    if status in {'limited_active', 'archived'} and not source_url:
        raise ValueError('Please add a source link for any active or archived claim.')
    if ends_at:
        when = parse_date(ends_at)
        if when.year == 1970 or when.year > 2100 or when.year < 2020:
            raise ValueError('Invalid deadline. Use an ISO timestamp with timezone.')
        if status != 'limited_active':
            raise ValueError('Only active limited-time entries may have a deadline.')
        ends_at = when.isoformat(timespec='seconds')
    report_id = str(payload.get('report_id', '')).strip()

    def apply(db):
        game = next((g for g in db['games'] if int(g['id']) == appid), None)
        if not game:
            raise ValueError('Add or verify this game in the catalogue before reporting an achievement.')
        reports = game.setdefault('limited_items', [])
        previous = next((r for r in reports if r.get('id') == report_id), None) if report_id else None
        item = {'id': previous.get('id') if previous else str(uuid.uuid4()), 'title': title,
                'scope': scope, 'status': status, 'source_url': source_url,
                'note': note, 'ends_at': ends_at or None, 'provenance': 'user_report',
                'updated_at': now()}
        if previous:
            changed = any(previous.get(k) != item.get(k) for k in ('title', 'scope', 'status', 'source_url', 'ends_at', 'note'))
            previous.clear(); previous.update(item)
        else:
            if len(reports) >= 100:
                raise ValueError('Maximum of 100 manual reports per game.')
            reports.append(item)
            changed = True
        if changed:
            add_event(db, appid, game['name'], 'report',
                ('Reported active limited-time' if status == 'limited_active' else 'Reported archived' if status == 'archived' else 'Report updated') + f': {title}',
                'User-submitted source, not independently verified by Steam.')
        return item
    return update_db(apply)


def delete_report(payload: dict) -> bool:
    appid = app_id_from(payload.get('appid'))
    report_id = str(payload.get('report_id') or '')
    def apply(db):
        game = next((g for g in db['games'] if int(g['id']) == appid), None)
        if not game:
            raise ValueError('Game not found.')
        items = game.get('limited_items', [])
        old_len = len(items)
        game['limited_items'] = [r for r in items if r.get('id') != report_id]
        return len(game['limited_items']) != old_len
    return update_db(apply)


def update_deadlines() -> int:
    """One-time local activity records; time passing does NOT prove archiving."""
    # Avoid rewriting DB unnecessarily (scanner writes are locked)
    with DB_LOCK:
        db = read_db()
        added = 0
        clock = dt.datetime.now(dt.timezone.utc)
        for game in db['games']:
            for item in game.get('limited_items', []):
                if item.get('status') != 'limited_active' or not item.get('ends_at'):
                    continue
                deadline = parse_date(item['ends_at'])
                remain = deadline - clock
                if remain <= dt.timedelta(0): suffix, label = 'passed', 'Reported deadline passed (archiving unverified)'
                elif remain <= dt.timedelta(hours=24): suffix, label = '24h', 'Reported deadline within 24 hours'
                elif remain <= dt.timedelta(days=7): suffix, label = '7d', 'Reported deadline within 7 days'
                else: continue
                added += add_event(db, int(game['id']), game['name'], 'deadline',
                                   f'{label}: {item["title"]}',
                                   f'Deadline based on a user-supplied source: {item["ends_at"]}',
                                   dedupe_key=f'deadline:{item["id"]}:{item["ends_at"]}:{suffix}')
        if added: write_db(db)
        return added


def stats_from_groups(groups: list[dict]) -> dict:
    dlc = 0
    names = []
    total = 0
    for group in groups:
        names.append(str(group.get('name') or f'Set {len(names) + 1}')[:100])
        dlcid = group.get('dlcappid', group.get('dlc_appid', 0))
        dlc += int(str(dlcid) not in {'0', '', 'None', 'null'} or str(group.get('type', '')).lower() == 'dlc')
        achievements = group.get('achievements', [])
        if isinstance(achievements, list):
            total += len(achievements)
    archived = archived_records(groups)
    types = []
    if dlc: types.append('DLC')
    if len(groups) - dlc > 1: types.append('Modes')
    if archived: types.append('Archived')
    return {'set_count': len(groups), 'dlc_set_count': dlc, 'archived_count': len(archived),
            'steam_archived_items': archived, 'archive_visibility': 'structured_page',
            'achievement_entries': total, 'set_names': names, 'types': types}


def parse_achievement_page(page: str) -> dict | None:
    """Return positive set evidence only. An ordinary achievement page is None.

    Steam has a *base game set* even without adopting custom sets. For this reason
    a single Base Game set never counts as a positive match.
    """
    groups = find_groups(page)
    if groups:
        stats = stats_from_groups(groups)
        if stats['set_count'] > 1 or (stats['set_count'] == 1 and
            stats['set_names'][0].strip().casefold() not in {'base game', 'base game achievements', 'base'}):
            return stats
    parser = VisibleText()
    parser.feed(page)
    txt = re.sub(r'\s+', ' ', ' '.join(parser.parts))
    # Steam's new public page uses "X from base game across N sets",
    # "Y from DLC across M sets" and "All Sets (K)".
    n = re.search(r'\bAll Sets\s*\(\s*(\d{1,2})\s*\)', txt, re.I)
    if not n:
        return None
    count = int(n.group(1))
    if not 2 <= count <= 40:
        return None
    # Require a second independent clue that it is the Steam achievement page.
    if not re.search(r'\b(?:achievements|from base game across|from DLC across)\b', txt, re.I):
        return None
    match_dlc = re.search(r'\bfrom DLC across\s+(\d{1,2})\s+sets?', txt, re.I)
    dlc = int(match_dlc.group(1)) if match_dlc else 0
    types = (['DLC'] if dlc else []) + (['Modes'] if count - dlc > 1 else [])
    return {'set_count': count, 'dlc_set_count': dlc, 'archived_count': None,
            'archive_visibility': 'not_available', 'set_names': [], 'types': types}


def page_title(page: str) -> str | None:
    title = re.search(r'<title[^>]*>(.*?)</title>', page, re.I | re.S)
    if title:
        raw = html.unescape(re.sub(r'<[^>]*>', '', title.group(1))).strip()
        match = re.match(r'Steam Community\s*::\s*(.+?)\s*::\s*Achievements?', raw, re.I)
        if match:
            return match.group(1)[:140]
    return None


def steam_name_matches(expected: str, actual: str) -> bool:
    """Reject pages for an unrelated Steam title before marking verified.

    Normalizes common trademark punctuation and capitalization. Preserve numbers
    to distinguish sequels, e.g. PowerWash Simulator vs PowerWash Simulator 2.
    """
    def clean(value):
        value = html.unescape(value).casefold().replace('®', '').replace('™', '')
        return re.sub(r'[^a-z0-9]+', ' ', value).strip()
    a, b = clean(expected), clean(actual)
    if not a or not b:
        return True
    if re.findall(r'\d+', a) != re.findall(r'\d+', b):
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.68 or a in b or b in a


def check_game(appid: int, existing: dict | None = None) -> dict:
    url = f'https://steamcommunity.com/achievements/{appid}?l=english'
    date = now()
    try:
        page = fetch(url)
        stats = parse_achievement_page(page)
        if stats is None:
            return {'id': appid, 'status': 'not_detected', 'checked_at': date,
                    'reason': 'No recognizable custom achievement sets on the public page; this is not proof they are absent.',
                    'url': url}
        old_name = (existing or {}).get('name', '')
        observed_name = page_title(page)
        if old_name and not old_name.startswith('Steam App ') and observed_name and not steam_name_matches(old_name, observed_name):
            return {'id': appid, 'status': 'name_mismatch', 'checked_at': date,
                    'reason': f'Steam page is for {observed_name!r}, not {old_name!r}; no verification recorded.',
                    'url': url}
        name = old_name if old_name and not old_name.startswith('Steam App ') else (observed_name or old_name or f'Steam App {appid}')
        result = {'id': appid, 'name': name,
                  'status': 'confirmed', 'verified': True, 'checked_at': date, 'verified_at': date,
                  'url': url, **stats}
        return result
    except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
        reason = (f'HTTP {exc.code}' if isinstance(exc, HTTPError) else
                  f'{type(exc).__name__}: {str(exc)[:110]}')
        return {'id': appid, 'status': 'error', 'checked_at': date, 'reason': reason, 'url': url}


def upsert_candidate(db: dict, appid: int, name: str, source: str) -> bool:
    if any(int(g['id']) == appid for g in db['games']):
        return False
    existing = next((c for c in db['candidates'] if int(c['id']) == appid), None)
    if existing:
        if existing.get('name', '').startswith('Steam App ') and name:
            existing['name'] = name[:140]
        return False
    if len(db['candidates']) >= MAX_CANDIDATES:
        # Drop oldest checked nonmatches first; never drop unscanned suggestions.
        old = sorted([c for c in db['candidates'] if c.get('checked_at')],
                     key=lambda c: c.get('checked_at', ''))
        if not old:
            return False
        db['candidates'].remove(old[0])
    db['candidates'].append({'id': appid, 'name': name[:140] or f'Steam App {appid}',
                             'source': source, 'discovered_at': now()[:10],
                             'status': 'pending'})
    return True


def save_result(result: dict, default: dict | None = None) -> str:
    """Persist every check attempt without promoting errors or nonmatches."""
    appid = int(result['id'])
    default = default or {}
    def apply(db):
        game = next((g for g in db['games'] if int(g['id']) == appid), None)
        candidate = next((c for c in db['candidates'] if int(c['id']) == appid), None)
        if result['status'] == 'confirmed':
            original = game or candidate or default
            original_name = original.get('name', '')
            name = original_name if original_name and not original_name.startswith('Steam App ') else result['name']
            record = {**original, **result, 'name': name, 'last_check_note': None,
                      'source': original.get('source') or 'Steam public achievement page',
                      'added': original.get('added') or now()[:10],
                      'last_check_status': 'confirmed'}
            # A public page that lacks archived items is not evidence that none
            # exist: Steam hides archives for players who didn't earn them.
            previous_items = original.get('steam_archived_items', [])
            fresh_items = result.get('steam_archived_items', [])
            known = {(x['kind'], x['name'], x.get('set', '')) for x in previous_items}
            if result.get('archive_visibility') != 'structured_page':
                record['steam_archived_items'] = previous_items
                record['archived_count'] = original.get('archived_count')
                record['archive_visibility'] = original.get('archive_visibility', 'not_available')
            else:
                # Archive observations persist once seen; removal is not proof
                # of unarchiving when Steam may filter the public view.
                record['steam_archived_items'] = previous_items + [x for x in fresh_items if (x['kind'], x['name'], x.get('set', '')) not in known]
                record['archived_count'] = len(record['steam_archived_items'])
                record['archive_visibility'] = 'structured_page'
                for entry in fresh_items:
                    identity = (entry['kind'], entry['name'], entry.get('set', ''))
                    if identity not in known:
                        add_event(db, appid, name, 'archive', f'Steam page flagged archived: {entry["name"]}',
                            f'Explicit archived flag in public page data ({entry["kind"]}).',
                            dedupe_key=f'archive:{appid}:{entry["kind"]}:{entry["name"]}:{entry.get("set", "")}')
            if game and game.get('set_count') and result.get('set_count') and game['set_count'] != result['set_count']:
                add_event(db, appid, name, 'sets', 'Achievement Set count changed',
                          f'{game["set_count"]} → {result["set_count"]} sets')
            if game:
                game.clear(); game.update(record)
            else:
                db['games'].append(record)
            db['candidates'] = [c for c in db['candidates'] if int(c['id']) != appid]
            db['games'].sort(key=lambda g: str(g.get('name', '')).casefold())
            if not game:
                add_event(db, appid, name, 'new_game', 'New game confirmed with custom Achievement Sets',
                          'Detected by SetScout on a public Steam achievements page.')
            return 'added' if not game else 'verified'
        if game:
            game.update({'last_check_status': result['status'], 'checked_at': result['checked_at'],
                         'last_check_note': result.get('reason')})
            # Leave past positive verification intact on transient errors.
            return 'unchanged'
        if not candidate:
            upsert_candidate(db, appid, default.get('name', ''), default.get('source', 'Manual submission'))
            candidate = next(c for c in db['candidates'] if int(c['id']) == appid)
        candidate.update({'checked_at': result['checked_at'], 'status': result['status'],
                          'last_check_note': result.get('reason')})
        return result['status']
    return update_db(apply)


def candidates_from_charts(payload: dict) -> list[tuple[int, str]]:
    ranks = payload.get('response', {}).get('ranks', [])
    result = []
    for item in ranks:
        try:
            value = int(item.get('appid'))
            if 1 <= value <= 2_147_483_647:
                result.append((value, str(item.get('name', ''))))
        except (ValueError, TypeError, AttributeError):
            pass
    return result


def candidates_from_featured(payload: dict) -> list[tuple[int, str]]:
    result = []
    for category in ('top_sellers', 'new_releases', 'specials', 'coming_soon'):
        data = payload.get(category, {})
        if not isinstance(data, dict):
            continue
        for item in data.get('items', []):
            try:
                appid = int(item.get('id'))
                if 1 <= appid <= 2_147_483_647:
                    result.append((appid, str(item.get('name', ''))))
            except (ValueError, TypeError, AttributeError):
                continue
    return result


SOURCES = (
    ('Steam most-played charts', 'https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/?format=json', candidates_from_charts),
    ('Steam store featured lists', 'https://store.steampowered.com/api/featuredcategories/?cc=us&l=en', candidates_from_featured),
)


def refresh_candidates() -> dict:
    statuses = {}
    for label, url, parser in SOURCES:
        try:
            found = parser(json.loads(fetch(url)))
            unique = {appid: name for appid, name in found}
            def apply(db):
                return sum(upsert_candidate(db, appid, name, label) for appid, name in unique.items())
            new_count = update_db(apply)
            statuses[label] = {'seen': len(unique), 'new': new_count, 'ok': True}
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            statuses[label] = {'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:130]}'}
    return statuses


def is_pending_candidate(candidate: dict) -> bool:
    """An untouched candidate awaits its first check; checked nonmatches stay in history."""
    return candidate.get('status', 'pending') == 'pending' and not candidate.get('checked_at')


def candidate_counts(candidates: list[dict]) -> dict:
    """Present meaningful queue counts without deleting inconclusive evidence."""
    return {'pending': sum(is_pending_candidate(c) for c in candidates),
            'not_detected': sum(c.get('status') == 'not_detected' for c in candidates),
            'errors': sum(c.get('status') in {'error', 'name_mismatch'} for c in candidates)}


def pick_candidates(limit: int, check_known: bool = False) -> list[dict]:
    db = read_db()
    if check_known:
        items = db['games']
        # Check unverified first, then oldest last-check timestamp.
        return sorted(items, key=lambda x: (bool(x.get('verified')), parse_date(x.get('checked_at'))))[:limit]
    items = db['candidates']
    # Discovery prioritizes unseen candidates; already rejected candidates are
    # rechecked only after 14 days. This avoids endlessly polling the same game.
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)
    eligible = [x for x in items if parse_date(x.get('checked_at')) <= cutoff]
    return sorted(eligible, key=lambda x: x.get('checked_at', ''))[:limit]


def run_scan(limit: int = 8, discover: bool = True) -> dict:
    if not SCAN_LOCK.acquire(blocking=False):
        return {'error': 'A scan is already running. Let it finish first.'}
    try:
        sources = refresh_candidates() if discover else {}
        items = pick_candidates(min(12, max(1, limit)), check_known=not discover)
        results = []
        # Bound parallel requests to avoid overloading Steam.
        with ThreadPoolExecutor(max_workers=2) as pool:
            future_to_item = {pool.submit(check_game, int(item['id']), item): item for item in items}
            for fut in as_completed(future_to_item):
                item = future_to_item[fut]
                try:
                    checked = fut.result()
                except Exception as exc:
                    checked = {'id': item['id'], 'checked_at': now(), 'status': 'error',
                               'reason': f'Unexpected: {type(exc).__name__}'}
                result_type = save_result(checked, item)
                results.append({'id': item['id'], 'name': item['name'],
                                'status': checked['status'], 'action': result_type})
        summary = {'checked': len(results),
                   'new_games': sum(r['action'] == 'added' for r in results),
                   'confirmed': sum(r['status'] == 'confirmed' for r in results),
                   'not_detected': sum(r['status'] == 'not_detected' for r in results),
                   'errors': sum(r['status'] == 'error' for r in results),
                   'sources': sources, 'results': results,
                   'ran_at': now(), 'mode': 'discover' if discover else 'recheck'}
        def apply(db):
            last_sources = sources if discover else db.get('scan', {}).get('last_sources', {})
            db['scan'] = {'last_run': summary['ran_at'], 'last_result': {k: v for k, v in summary.items() if k != 'results'},
                          'last_sources': last_sources}
        update_db(apply)
        return summary
    finally:
        SCAN_LOCK.release()


def background_scanner(stop_event: threading.Event):
    # The scheduler runs locally while SetScout is open, never as a remote service.
    while not stop_event.wait(25):
        update_deadlines()
        db = read_db()
        if not db.get('auto_enabled'):
            continue
        hours = max(1, min(48, int(db.get('auto_hours', AUTO_INTERVAL_HOURS))))
        previous = parse_date(db.get('scan', {}).get('last_run'))
        if dt.datetime.now(dt.timezone.utc) - previous >= dt.timedelta(hours=hours):
            run_scan(8, True)
            run_scan(8, False)


def bulk_snapshot() -> dict:
    """Report in-memory progress plus the current number of unverified listings."""
    with BULK_STATE_LOCK:
        snapshot = dict(BULK_PROGRESS)
        snapshot['cancel_requested'] = BULK_CANCEL.is_set() if snapshot['state'] == 'running' else False
    db = read_db()
    snapshot['remaining_unverified'] = sum(not g.get('verified') for g in db['games'])
    snapshot['remaining_pending'] = candidate_counts(db['candidates'])['pending']
    return snapshot


def _run_bulk_worker(items: list[dict]) -> None:
    """Run a fixed, deduplicated snapshot once, without reselecting failed games."""
    try:
        for index, game in enumerate(items):
            if BULK_CANCEL.is_set():
                break
            try:
                checked = check_game(int(game['id']), game)
                save_result(checked, game)
            except Exception as exc:
                checked = {'id': game['id'], 'status': 'error', 'checked_at': now(),
                           'reason': f'Unexpected: {type(exc).__name__}: {str(exc)[:110]}'}
                try:
                    save_result(checked, game)
                except Exception:
                    pass  # A failed database write must not silently mark this verified.
            status = checked.get('status', 'error')
            with BULK_STATE_LOCK:
                BULK_PROGRESS['completed'] += 1
                BULK_PROGRESS['confirmed'] += status == 'confirmed'
                BULK_PROGRESS['not_detected'] += status == 'not_detected'
                BULK_PROGRESS['errors'] += status not in {'confirmed', 'not_detected'}
                BULK_PROGRESS['last_game'] = game.get('name', f"Steam App {game['id']}")
                BULK_PROGRESS['last_status'] = status
            # Pause rather than hammering Steam after an explicit rate-limit response.
            if status == 'error' and 'HTTP 429' in checked.get('reason', ''):
                with BULK_STATE_LOCK:
                    BULK_PROGRESS['state'] = 'rate_limited'
                    BULK_PROGRESS['error'] = 'Steam returned HTTP 429 (rate limited). Remaining candidates were left queued; retry later.'
                break
            if index < len(items) - 1 and BULK_CANCEL.wait(BULK_PAUSE_SECONDS):
                break
    except Exception as exc:
        with BULK_STATE_LOCK:
            BULK_PROGRESS['state'] = 'error'
            BULK_PROGRESS['error'] = f'{type(exc).__name__}: {str(exc)[:150]}'
    finally:
        with BULK_STATE_LOCK:
            if BULK_PROGRESS['state'] == 'running':
                BULK_PROGRESS['state'] = 'cancelled' if BULK_CANCEL.is_set() else 'completed'
            BULK_PROGRESS['finished_at'] = now()
        SCAN_LOCK.release()


def start_bulk_scan(mode: str) -> dict:
    """Check each item in the selected snapshot once, preserving inconclusive candidates."""
    global BULK_PROGRESS
    if mode not in {'unverified', 'candidates'}:
        raise ValueError('Unknown bulk scan mode.')
    # Prevent overlapping automatic, single, and bulk scans.
    if not SCAN_LOCK.acquire(blocking=False):
        return {'error': 'Another scan is already running.'}
    try:
        db = read_db()
        source = (g for g in db['games'] if not g.get('verified')) if mode == 'unverified' else \
                 (c for c in db['candidates'] if is_pending_candidate(c))
        seen = set()
        items = []
        for item in source:
            appid = int(item['id'])
            if appid not in seen:
                items.append(item.copy())
                seen.add(appid)
        items.sort(key=lambda g: (bool(g.get('checked_at')), parse_date(g.get('checked_at')), int(g['id'])))
        BULK_CANCEL.clear()
        with BULK_STATE_LOCK:
            BULK_PROGRESS = {'state': 'running' if items else 'completed',
                             'mode': mode, 'total': len(items), 'completed': 0,
                             'confirmed': 0, 'not_detected': 0, 'errors': 0,
                             'started_at': now(), 'finished_at': None,
                             'last_game': None, 'last_status': None}
            response = {**BULK_PROGRESS, 'cancel_requested': False}
        if items:
            threading.Thread(target=_run_bulk_worker, args=(items,), daemon=True,
                             name=f'setscout-{mode}-scan').start()
        else:
            SCAN_LOCK.release()
        return response
    except Exception:
        SCAN_LOCK.release()
        raise


def start_unverified_scan() -> dict:
    return start_bulk_scan('unverified')


def start_pending_scan() -> dict:
    return start_bulk_scan('candidates')

def cancel_unverified_scan() -> dict:
    """Signal graceful stop after the current Steam request finishes."""
    with BULK_STATE_LOCK:
        if BULK_PROGRESS['state'] != 'running':
            return {'error': 'No unverified scan is currently running.'}
        BULK_CANCEL.set()
    return bulk_snapshot()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def json_response(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/games':
            db = read_db()
            return self.json_response({**{k: db.get(k) for k in ('games', 'candidates', 'events', 'updated_at', 'auto_enabled', 'auto_hours', 'scan')}, 'bulk': bulk_snapshot(), 'candidate_counts': candidate_counts(db['candidates']), 'store_index': store_index.stats()})
        if path == '/api/store-index':
            return self.json_response(store_index.stats())
        if path == '/api/bulk-status':
            return self.json_response(bulk_snapshot())
        if path == '/api/status':
            db = read_db()
            return self.json_response({'running_scan': SCAN_LOCK.locked(),
                                       'auto_enabled': db.get('auto_enabled', True),
                                       'auto_hours': db.get('auto_hours', 6), 'scan': db['scan']})
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in {'/api/check', '/api/scan', '/api/discover', '/api/settings', '/api/import', '/api/report', '/api/delete-report', '/api/scan-unverified', '/api/scan-pending', '/api/cancel-unverified', '/api/store-sync', '/api/store-scan', '/api/store-stop'}:
            return self.json_response({'error': 'Unknown action'}, 404)
        try:
            if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                return self.json_response({'error': 'Content-Type must be application/json'}, 415)
            origin = self.headers.get('Origin', '')
            if origin and origin not in {f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}'}:
                return self.json_response({'error': 'Cross-origin requests are blocked'}, 403)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 15000:
                return self.json_response({'error': 'Request too large'}, 413)
            payload = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(payload, dict):
                raise ValueError('Expected JSON object')
            if path == '/api/store-sync':
                return self.json_response(store_index.start_sync())
            if path == '/api/store-scan':
                if SCAN_LOCK.locked():
                    return self.json_response({'error': 'An existing SetScout scan is already running.'}, 409)
                known = {int(g['id']) for g in read_db()['games']}
                return self.json_response(store_index.start_scan(known, check_game, save_result))
            if path == '/api/store-stop':
                store_index.stop()
                return self.json_response({'stopping': True})
            if path == '/api/scan-unverified':
                result = start_unverified_scan()
                return self.json_response(result, 409 if 'error' in result else 200)
            if path == '/api/scan-pending':
                result = start_pending_scan()
                return self.json_response(result, 409 if 'error' in result else 200)
            if path == '/api/cancel-unverified':
                result = cancel_unverified_scan()
                return self.json_response(result, 409 if 'error' in result else 200)
            if path == '/api/report':
                return self.json_response({'report': manual_report(payload)})
            if path == '/api/delete-report':
                return self.json_response({'deleted': delete_report(payload)})
            if path == '/api/settings':
                if not isinstance(payload.get('auto_enabled'), bool):
                    raise ValueError('auto_enabled must be true or false')
                val = payload['auto_enabled']
                update_db(lambda db: db.update(auto_enabled=val))
                return self.json_response({'auto_enabled': val})
            if path == '/api/import':
                raw = str(payload.get('text', ''))[:13000]
                ids = list(dict.fromkeys(app_id_from(line) for line in raw.splitlines() if line.strip()))
                if len(ids) > 100:
                    raise ValueError('Import up to 100 App IDs at a time.')
                new = update_db(lambda db: sum(upsert_candidate(db, i, '', 'Manual import') for i in ids))
                return self.json_response({'received': len(ids), 'queued': new})
            if path == '/api/check':
                appid = app_id_from(payload.get('appid'))
                db = read_db()
                existing = next((g for g in db['games'] + db['candidates'] if int(g['id']) == appid), None)
                proposed = {'id': appid, 'name': str(payload.get('name', '')).strip()[:140] or f'Steam App {appid}',
                            'source': 'Manual check'}
                checked = check_game(appid, existing or proposed)
                action = save_result(checked, existing or proposed)
                return self.json_response({**checked, 'action': action, 'verified': checked['status'] == 'confirmed'})
            if path in ('/api/scan', '/api/discover'):
                limit = min(12, max(1, int(payload.get('limit', 8))))
                answer = run_scan(limit, discover=path == '/api/discover')
                return self.json_response(answer, 409 if 'error' in answer else 200)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.json_response({'error': str(exc)}, 400)
        except Exception as exc:
            return self.json_response({'error': f'Server error: {type(exc).__name__}'}, 500)


def main():
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    stop = threading.Event()
    scheduler = threading.Thread(target=background_scanner, args=(stop,), daemon=True)
    scheduler.start()
    print(f'\nSetScout v4 — http://127.0.0.1:{PORT}')
    print('Open the URL above. Automatic scans are enabled by default while the server is running.')
    print('Discovery: public feeds, 8 candidate + 8 known checks / 6 hours; local activity feed.')
    print('Steam may throttle checks. Ctrl+C stops the app and background scans.\n')
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        BULK_CANCEL.set()
        store_index.stop()
        server.server_close()

if __name__ == '__main__':
    main()
