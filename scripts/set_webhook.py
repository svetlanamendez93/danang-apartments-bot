"""One-off: point Telegram at our /bot/webhook endpoint.

Run this once after the web app is deployed and reachable (and again any time
API_BASE_URL changes, e.g. after moving to a different PythonAnywhere account).

Usage (from the project root, inside the virtualenv):
    python scripts/set_webhook.py
"""
from __future__ import annotations

import os
import sys

# Running this file directly (`python scripts/set_webhook.py`) only puts
# scripts/ on sys.path, not the project root — so `import server` fails with
# ModuleNotFoundError unless we add the root ourselves first.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv

# Explicit path, not just load_dotenv() — dotenv's default cwd-based search
# only works if this is run from the project root; this way it doesn't matter.
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from server import telegram_api  # noqa: E402 (must import after load_dotenv)

if __name__ == "__main__":
    api_base_url = os.environ["API_BASE_URL"]
    webhook_url = f"{api_base_url}/bot/webhook"
    result = telegram_api.set_webhook(webhook_url)
    print(f"Webhook set to {webhook_url}")
    print(result)
