"""Store the Steam Web API key in Windows Credential Manager.

The key is never embedded in the repository or sent to GitHub. On Windows,
CredRead/CredWrite use the current Windows user's protected credential vault.
Other platforms may supply STEAM_WEB_API_KEY via their own secret manager.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import getpass
import os
from pathlib import Path
import re
import sys

TARGET = 'SetScout:SteamWebAPI'
LEGACY_PATH = Path(__file__).resolve().parent / '.steam_api_key'
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


class _FILETIME(ctypes.Structure):
    _fields_ = [('low', wintypes.DWORD), ('high', wintypes.DWORD)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ('Flags', wintypes.DWORD),
        ('Type', wintypes.DWORD),
        ('TargetName', wintypes.LPWSTR),
        ('Comment', wintypes.LPWSTR),
        ('LastWritten', _FILETIME),
        ('CredentialBlobSize', wintypes.DWORD),
        ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
        ('Persist', wintypes.DWORD),
        ('AttributeCount', wintypes.DWORD),
        ('Attributes', ctypes.c_void_p),
        ('TargetAlias', wintypes.LPWSTR),
        ('UserName', wintypes.LPWSTR),
    ]


_PCREDENTIALW = ctypes.POINTER(_CREDENTIALW)


def _windows_api():
    if sys.platform != 'win32':
        raise RuntimeError('Windows Credential Manager requires Windows.')
    api = ctypes.WinDLL('Advapi32.dll', use_last_error=True)
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(_PCREDENTIALW)]
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def _read_windows(target: str = TARGET) -> str:
    api = _windows_api()
    pointer = _PCREDENTIALW()
    if not api.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return ''
        raise OSError(error, 'Windows Credential Manager could not read the credential')
    try:
        record = pointer.contents
        if not record.CredentialBlobSize:
            return ''
        return ctypes.string_at(record.CredentialBlob, record.CredentialBlobSize).decode('utf-8')
    finally:
        api.CredFree(pointer)


def _write_windows(value: str, target: str = TARGET) -> None:
    api = _windows_api()
    raw = value.encode('utf-8')
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    record = _CREDENTIALW()
    record.Type = CRED_TYPE_GENERIC
    record.TargetName = target
    record.UserName = 'SetScout'
    record.Persist = CRED_PERSIST_LOCAL_MACHINE
    record.CredentialBlobSize = len(raw)
    record.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    if not api.CredWriteW(ctypes.byref(record), 0):
        raise OSError(ctypes.get_last_error(), 'Windows Credential Manager could not save the credential')


def _delete_windows(target: str = TARGET) -> bool:
    api = _windows_api()
    if api.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        return True
    error = ctypes.get_last_error()
    if error == ERROR_NOT_FOUND:
        return False
    raise OSError(error, 'Windows Credential Manager could not delete the credential')


def get_api_key() -> str:
    """Read protected Windows credentials, or an optional environment variable.

    A legacy plaintext .steam_api_key is deliberately NOT read at runtime.
    """
    if sys.platform == 'win32':
        return _read_windows()
    return os.environ.get('STEAM_WEB_API_KEY', '').strip()


def validate_key(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r'[A-Fa-f0-9]{32}', value):
        raise ValueError('Expected a 32-character hexadecimal Steam Web API key.')
    return value


def store_key(value: str) -> None:
    key = validate_key(value)
    _write_windows(key)
    if _read_windows() != key:
        raise RuntimeError('Credential verification failed. Existing plaintext file, if any, was not touched.')


def import_legacy(path: Path = LEGACY_PATH) -> bool:
    """Migrate an old plaintext file only on explicit user request.

    Remove the plaintext file only AFTER a confirmed write and read-back.
    """
    if not path.is_file():
        raise FileNotFoundError('No .steam_api_key file was found in the SetScout folder.')
    value = validate_key(path.read_text(encoding='utf-8-sig'))
    store_key(value)
    path.unlink()
    return True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if sys.platform != 'win32':
        print('Windows Credential Manager is only available on Windows. Use a private environment variable on other platforms.')
        return 1
    if len(args) != 1 or args[0] not in ('import-file', 'set', 'status', 'delete'):
        print('Usage: python steam_credentials.py [import-file | set | status | delete]')
        return 2
    try:
        action = args[0]
        if action == 'import-file':
            import_legacy()
            print('Steam key stored in Windows Credential Manager; legacy plaintext file removed.')
            print('Restart SetScout for the new credential to be used.')
        elif action == 'set':
            store_key(getpass.getpass('Steam Web API key (hidden): '))
            print('Steam key stored securely in Windows Credential Manager.')
        elif action == 'status':
            print('Credential Manager: ' + ('configured' if _read_windows() else 'not configured'))
            print('Legacy plaintext file: ' + ('still present (migrate it)' if LEGACY_PATH.exists() else 'not present'))
        elif action == 'delete':
            print('Credential Manager: ' + ('credential deleted' if _delete_windows() else 'nothing to delete'))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        # Errors never contain the API key value.
        print('Unable to complete credential operation: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
