"""Verify the identity Telegram gives a Mini App.

Favourites and "already viewed" are per-person, so the API has to know who is
asking — and must not simply believe a user id sent in a query string, or
anyone could read and edit anyone else's shortlist.

Telegram hands the Mini App an `initData` string signed with a key derived from
the bot token. Checking that signature proves the request really comes from
Telegram on behalf of that user, without any login of our own.

See https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

# initData older than this is rejected: it stops a captured string being
# replayed indefinitely by someone who got hold of it.
MAX_AGE_SECONDS = 24 * 60 * 60


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def verify_init_data(init_data: str, bot_token: str | None = None) -> dict | None:
    """Return the authenticated user dict, or None if the data isn't genuine."""
    if not init_data:
        return None
    token = bot_token or os.environ.get("BOT_TOKEN", "")
    if not token:
        return None

    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    # The signed payload is every remaining field, sorted, as "key=value" lines.
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    expected = hmac.new(_secret_key(token), check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None

    auth_date = pairs.get("auth_date")
    if auth_date and auth_date.isdigit():
        if time.time() - int(auth_date) > MAX_AGE_SECONDS:
            return None

    try:
        user = json.loads(pairs.get("user", "null"))
    except json.JSONDecodeError:
        return None
    if not isinstance(user, dict) or "id" not in user:
        return None
    return user
