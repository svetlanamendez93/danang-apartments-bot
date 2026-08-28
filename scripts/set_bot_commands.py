"""Register the bot's command menu with Telegram, in every supported language.

Telegram shows these behind the "/" button in the chat, translated to the
user's own client language. Run once after deploy, and again whenever the
command list changes:

    python scripts/set_bot_commands.py
"""
from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from server import telegram_api  # noqa: E402

COMMANDS = {
    "ru": [
        ("start", "Меню и карта объявлений"),
        ("latest", "Свежие объявления в чате"),
        ("subscribe", "Подписка на новые объявления"),
        ("submit", "Прислать своё объявление"),
        ("language", "Сменить язык"),
        ("help", "Помощь"),
    ],
    "en": [
        ("start", "Menu and listings map"),
        ("latest", "Latest listings in chat"),
        ("subscribe", "Alerts for new listings"),
        ("submit", "Send in a listing"),
        ("language", "Change language"),
        ("help", "Help"),
    ],
    "vi": [
        ("start", "Menu và bản đồ tin đăng"),
        ("latest", "Tin mới nhất trong chat"),
        ("subscribe", "Nhận thông báo tin mới"),
        ("submit", "Gửi tin của bạn"),
        ("language", "Đổi ngôn ngữ"),
        ("help", "Trợ giúp"),
    ],
}

if __name__ == "__main__":
    for lang, commands in COMMANDS.items():
        payload = [{"command": c, "description": d} for c, d in commands]
        # Russian doubles as the default list for clients in any other language.
        result = telegram_api.set_my_commands(payload, None if lang == "ru" else lang)
        print(f"{lang}: {result}")

    # The button beside the message box, for anyone who hasn't pressed /start
    # yet. Per-user language overrides come from the bot itself afterwards.
    webapp_url = os.environ.get("WEBAPP_URL", "")
    if webapp_url:
        from server.i18n import t  # noqa: E402

        print("menu button:", telegram_api.set_chat_menu_button(t("menu_button", "ru"), webapp_url))
    else:
        print("WEBAPP_URL is not set — skipping the menu button")
