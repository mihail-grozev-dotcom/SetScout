"""Optional SetScout push alerts using a private ntfy.sh topic.

The topic is a bearer-like secret. It is read only from a systemd encrypted
credential and is never sent to the dashboard, logs, or GitHub.
No network request is made if alerts are not configured.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen

_CREDENTIAL = 'setscout-alert-topic'
_TOPIC_PATTERN = re.compile(r'[A-Za-z0-9_-]{24,128}\Z')


def _topic() -> str:
    credentials_directory = os.environ.get('CREDENTIALS_DIRECTORY')
    if not credentials_directory:
        return ''
    try:
        topic = (Path(credentials_directory) / _CREDENTIAL).read_text(
            encoding='utf-8').strip()
    except (OSError, UnicodeError, ValueError):
        return ''
    return topic if _TOPIC_PATTERN.fullmatch(topic) else ''


def configured() -> bool:
    """Report only whether push is configured, never the topic itself."""
    return bool(_topic())


def send(message: str, *, test: bool = False) -> bool:
    """Publish a non-sensitive message to ntfy; never interfere with scanning."""
    topic = _topic()
    if not topic:
        return False
    request = Request(
        'https://ntfy.sh/' + topic,
        data=message.encode('utf-8'),
        headers={
            'Title': 'SetScout test notification' if test else 'SetScout scanner stopped',
            'Priority': 'default' if test else 'high',
            'Tags': 'white_check_mark' if test else 'warning',
            'Content-Type': 'text/plain; charset=utf-8',
        },
        method='POST',
    )
    try:
        with urlopen(request, timeout=7) as response:
            return 200 <= response.status < 300
    except (OSError, ValueError):
        # Do not log the exception: urllib errors may include the secret URL.
        print('SetScout: phone push notification could not be delivered.',
              file=sys.stderr)
        return False


def rate_limited(pending: int) -> bool:
    return send(
        'Steam returned HTTP 429. Continuous verification is OFF. '
        f'{pending:,} indexed games remain pending. '
        'Progress is saved. Wait for the rate limit to clear before resuming.'
    )


def test_message() -> bool:
    return send('Test successful. SetScout can send alerts when Steam pauses scanning.',
                test=True)
