"""Credential handling never prints secrets and never reads plaintext at runtime."""
import io
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import steam_credentials as credentials
import store_index


SAMPLE = '0123456789ABCDEF0123456789ABCDEF'  # Only test data, not a real key.


class CredentialTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(credentials.validate_key('  ' + SAMPLE.lower() + '\n'), SAMPLE.lower())
        with self.assertRaises(ValueError):
            credentials.validate_key('not-a-real-key')

    def test_migrate_verifies_before_deleting_plaintext(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '.steam_api_key'
            path.write_text(SAMPLE + '\n', encoding='utf-8')
            saved = {}
            with patch.object(credentials, '_write_windows', side_effect=lambda x: saved.update(value=x)), \
                 patch.object(credentials, '_read_windows', side_effect=lambda: saved.get('value', '')):
                self.assertTrue(credentials.import_legacy(path))
            self.assertFalse(path.exists())
            self.assertEqual(saved['value'], SAMPLE)

    def test_failed_migration_leaves_file_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '.steam_api_key'
            path.write_text(SAMPLE, encoding='utf-8')
            with patch.object(credentials, '_write_windows', return_value=None), \
                 patch.object(credentials, '_read_windows', return_value=''):
                with self.assertRaises(RuntimeError):
                    credentials.import_legacy(path)
            self.assertEqual(path.read_text(encoding='utf-8'), SAMPLE)

    def test_runtime_ignores_legacy_plaintext_even_when_present(self):
        with tempfile.TemporaryDirectory() as temp:
            legacy = Path(temp) / '.steam_api_key'
            legacy.write_text(SAMPLE, encoding='utf-8')
            with patch.object(credentials.sys, 'platform', 'win32'), \
                 patch.object(credentials, 'LEGACY_PATH', legacy), \
                 patch.object(credentials, '_read_windows', return_value=''), \
                 patch.dict('os.environ', {'STEAM_WEB_API_KEY': SAMPLE}):
                self.assertEqual(store_index.api_key(), '')
            self.assertTrue(legacy.exists())

    def test_status_never_displays_the_secret(self):
        output = io.StringIO()
        with patch.object(credentials.sys, 'platform', 'win32'), \
             patch.object(credentials, '_read_windows', return_value=SAMPLE), \
             redirect_stdout(output):
            self.assertEqual(credentials.main(['status']), 0)
        self.assertIn('configured', output.getvalue())
        self.assertNotIn(SAMPLE, output.getvalue())

    @unittest.skipUnless(sys.platform == 'win32', 'Native Credential Manager is Windows-only')
    def test_native_windows_generic_credential_round_trip(self):
        # Isolated disposable target: never touches the user's real Steam key.
        target = 'SetScout:CredentialTest:' + str(uuid.uuid4())
        try:
            self.assertEqual(credentials._read_windows(target), '')
            credentials._write_windows(SAMPLE, target)
            self.assertEqual(credentials._read_windows(target), SAMPLE)
        finally:
            credentials._delete_windows(target)


if __name__ == '__main__':
    unittest.main()
