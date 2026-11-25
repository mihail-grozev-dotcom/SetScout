"""Mocked tests for optional ntfy push delivery. No real network calls."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import alerts


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_disabled_without_systemd_credential(self):
        with patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': ''}):
            with patch.object(alerts, 'urlopen') as post:
                self.assertFalse(alerts.configured())
                self.assertFalse(alerts.rate_limited(123))
                post.assert_not_called()

    def test_invalid_topic_rejected(self):
        (self.directory / 'setscout-alert-topic').write_text('../invalid', encoding='utf-8')
        with patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': str(self.directory)}):
            with patch.object(alerts, 'urlopen') as post:
                self.assertFalse(alerts.configured())
                self.assertFalse(alerts.test_message())
                post.assert_not_called()

    def test_post_includes_no_api_key_and_uses_private_topic(self):
        topic = 'SetScout-' + 'a' * 35
        (self.directory / 'setscout-alert-topic').write_text(topic, encoding='utf-8')
        class FakeResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
        with patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': str(self.directory)}):
            with patch.object(alerts, 'urlopen', return_value=FakeResponse()) as post:
                self.assertTrue(alerts.configured())
                self.assertTrue(alerts.rate_limited(4))
                request = post.call_args.args[0]
                self.assertEqual(request.full_url, 'https://ntfy.sh/' + topic)
                self.assertEqual(request.get_method(), 'POST')
                self.assertIn(b'4 indexed games remain pending', request.data)
                self.assertNotIn(b'STEAM_WEB_API_KEY', request.data)
                self.assertEqual(post.call_args.kwargs['timeout'], 7)

    def test_network_failure_does_not_break_scanner(self):
        topic = 'SetScout-' + 'b' * 35
        (self.directory / 'setscout-alert-topic').write_text(topic, encoding='utf-8')
        with patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': str(self.directory)}):
            with patch.object(alerts, 'urlopen', side_effect=OSError('network down')):
                self.assertFalse(alerts.rate_limited(100))


if __name__ == '__main__':
    unittest.main()
