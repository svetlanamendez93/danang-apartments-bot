"""Minimal Telegram Bot API client (plain HTTP, no framework).

We talk to Telegram over plain `requests` instead of aiogram/python-telegram-bot
because the bot runs as a webhook inside a synchronous Flask app (that's what
PythonAnywhere's free tier supports well) — there's no persistent event loop to
host an async framework's polling/dispatch machinery, and for a handful of
commands it's not needed.
"""
from __future__ import annotations

import logging
import os

import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

logger = logging.getLogger(__name__)


def _post(method: str, payload: dict, timeout: int = 10) -> dict | None:
    """POST to the Bot API and log failures instead of swallowing them.

    Telegram rejects the *whole* request on things like an invalid web_app
    button URL (must be HTTPS) — without this check that failure was
    completely silent: our webhook still returned 200 to Telegram, so nothing
    ever showed up as an error anywhere, and the bot just looked dead.
    """
    resp = requests.post(f"{API_URL}/{method}", json=payload, timeout=timeout)
    if not resp.ok:
        logger.error("Telegram API %s failed: %s %s", method, resp.status_code, resp.text)
        return None
    return resp.json()


def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> dict | None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    result = _post("sendMessage", payload)
    if result is None:
        # HTML parse errors reject the whole message (a stray "<" in a scraped
        # post is enough), so retry once as plain text rather than losing it.
        payload.pop("parse_mode")
        result = _post("sendMessage", payload)
    return result


def send_photo(chat_id: int, photo_url: str, caption: str, reply_markup: dict | None = None) -> dict | None:
    payload = {"chat_id": chat_id, "photo": photo_url, "caption": caption, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _post("sendPhoto", payload, timeout=20)


def send_media_group(chat_id: int, photo_urls: list[str], caption: str) -> dict | None:
    """Send up to 10 photos as one album, with the caption on the first.

    A listing usually comes with the whole photo set from the original post,
    and sending them one by one would bury the chat; an album keeps them as a
    single swipeable unit. Telegram allows at most 10 items per group.
    """
    media = []
    for i, url in enumerate(photo_urls[:10]):
        item = {"type": "photo", "media": url}
        if i == 0 and caption:
            item["caption"] = caption
            item["parse_mode"] = "HTML"
        media.append(item)
    if not media:
        return None
    return _post("sendMediaGroup", {"chat_id": chat_id, "media": media}, timeout=30)


def set_my_commands(commands: list[dict], language_code: str | None = None) -> dict | None:
    """Register the command list Telegram shows in its own menu button."""
    payload: dict = {"commands": commands}
    if language_code:
        payload["language_code"] = language_code
    return _post("setMyCommands", payload)


def edit_message_text(chat_id: int, message_id: int, text: str) -> None:
    _post("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text})


def edit_message_reply_markup(chat_id: int, message_id: int, reply_markup: dict | None) -> None:
    payload = {"chat_id": chat_id, "message_id": message_id}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _post("editMessageReplyMarkup", payload)


def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    _post("answerCallbackQuery", payload)


def get_file_path(file_id: str) -> str:
    resp = requests.get(f"{API_URL}/getFile", params={"file_id": file_id}, timeout=10)
    resp.raise_for_status()
    return resp.json()["result"]["file_path"]


def download_file(file_id: str, destination: str) -> None:
    file_path = get_file_path(file_id)
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    with open(destination, "wb") as f:
        f.write(resp.content)


def set_webhook(webhook_url: str, secret_token: str | None = None) -> dict:
    payload: dict = {"url": webhook_url}
    if secret_token:
        # Telegram sends this back as X-Telegram-Bot-Api-Secret-Token on every
        # call, which is how the server tells real updates from forged ones.
        payload["secret_token"] = secret_token
    resp = requests.post(f"{API_URL}/setWebhook", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def webapp_button(text: str, url: str) -> dict:
    return {"inline_keyboard": [[{"text": text, "web_app": {"url": url}}]]}


def approve_reject_buttons(listing_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Одобрить", "callback_data": f"approve:{listing_id}"},
                {"text": "❌ Отклонить", "callback_data": f"reject:{listing_id}"},
            ]
        ]
    }
