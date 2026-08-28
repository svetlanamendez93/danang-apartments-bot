"""Minimal Telegram Bot API client (plain HTTP, no framework).

We talk to Telegram over plain `requests` instead of aiogram/python-telegram-bot
because the bot runs as a webhook inside a synchronous Flask app (that's what
PythonAnywhere's free tier supports well) — there's no persistent event loop to
host an async framework's polling/dispatch machinery, and for a handful of
commands it's not needed.
"""
from __future__ import annotations

import os

import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(f"{API_URL}/sendMessage", json=payload, timeout=10)


def edit_message_text(chat_id: int, message_id: int, text: str) -> None:
    requests.post(
        f"{API_URL}/editMessageText",
        json={"chat_id": chat_id, "message_id": message_id, "text": text},
        timeout=10,
    )


def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    requests.post(f"{API_URL}/answerCallbackQuery", json=payload, timeout=10)


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


def set_webhook(webhook_url: str) -> dict:
    resp = requests.post(f"{API_URL}/setWebhook", json={"url": webhook_url}, timeout=10)
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
