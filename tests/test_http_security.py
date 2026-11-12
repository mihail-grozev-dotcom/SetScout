"""HTTP security regression tests for SetScout's local-only server."""
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server
import store_index


class SilentHandler(server.Handler):
    def log_message(self, *_args):
        pass


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        for name, data in [('index.html', '<h1>SetScout</h1>'), ('app.js', 'let ready=true'),
                           ('.steam_api_key', 'FAKE_SECRET_NEVER_SERVE'),
                           ('games.json', json.dumps({'games': [], 'candidates': [], 'events': []})),
                           ('steam_catalog.sqlite3', 'PRIVATE_SQLITE_DATA')]:
            (root / name).write_text(data, encoding='utf-8')
        (root / '.git').mkdir()
        (root / '.git' / 'config').write_text('[core]\n', encoding='utf-8')
        (root / 'docs').mkdir()
        (root / 'docs' / 'preview.jpg').write_bytes(b'PREVIEW')
        self.patchers = [patch.object(server, 'HERE', root),
                         patch.object(server, 'DB_FILE', root / 'games.json'),
                         patch.object(store_index, 'DB_PATH', root / 'index.sqlite3'),
                         patch.object(store_index, 'KEY_PATH', root / '.steam_api_key')]
        for p in self.patchers:
            p.start()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), SilentHandler)
        self.patch_port = patch.object(server, 'PORT', self.http.server_address[1])
        self.patch_port.start()
        self.base = f'http://127.0.0.1:{self.http.server_address[1]}'
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=3)
        self.patch_port.stop()
        for p in reversed(self.patchers):
            p.stop()
        self.folder.cleanup()

    def request(self, path, method='GET', headers=None, data=None):
        try:
            with urlopen(Request(self.base + path, headers=headers or {},
                                 method=method, data=data), timeout=4) as response:
                return response.status, response.read(), response.headers
        except HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def test_only_public_ui_files_are_served(self):
        for path in ('/', '/index.html', '/app.js', '/docs/preview.jpg', '/api/games', '/api/store-index'):
            with self.subTest(path=path):
                status, body, headers = self.request(path)
                self.assertEqual(status, 200)
                self.assertNotIn(b'FAKE_SECRET_NEVER_SERVE', body)
                self.assertEqual(headers.get('X-Frame-Options'), 'DENY')

    def test_private_paths_cannot_be_downloaded_or_headed(self):
        for path in ('/.steam_api_key', '/games.json', '/steam_catalog.sqlite3', '/.git/config',
                     '/server.py', '/README.md', '/%2e%73team_api_key',
                     '/docs/../.steam_api_key'):
            for method in ('GET', 'HEAD'):
                with self.subTest(path=path, method=method):
                    status, content, _ = self.request(path, method)
                    self.assertEqual(status, 404)
                    self.assertNotIn(b'FAKE_SECRET_NEVER_SERVE', content)

    def test_invalid_hosts_and_cross_origin_posts_blocked(self):
        bad = {'Host': f'evil.example:{self.http.server_address[1]}'}
        self.assertEqual(self.request('/', headers=bad)[0], 403)
        self.assertEqual(self.request('/api/games', headers=bad)[0], 403)
        payload = b'{"auto_enabled": false}'
        headers = {'Content-Type': 'application/json'}
        self.assertEqual(self.request('/api/settings', 'POST', headers, payload)[0], 403)
        self.assertEqual(self.request('/api/settings', 'POST',
                                      {**headers, 'Origin': 'https://evil.example'}, payload)[0], 403)
        self.assertEqual(self.request('/api/settings', 'POST',
                                      {**headers, 'Origin': self.base}, payload)[0], 200)


if __name__ == '__main__':
    unittest.main()
