"""Runs on a GitHub Actions schedule (see .github/workflows/scrape.yml).

Reads the public web preview of each monitored channel (https://t.me/s/<channel>)
— no login, no API key, works for any public channel — finds posts newer than
what we've already ingested, and pushes them to the Flask API's /internal/ingest
endpoint. This replaces a persistent MTProto listener so nothing needs to run
24/7 on a server we'd have to pay for or keep a computer on for.

Trade-off: near-real-time (bounded by how often this workflow runs, ~5 min),
not instant push. Good enough while a home-grown crawler is the free option.
"""
from __future__ import annotations

import os
import re

import requests
from bs4 import BeautifulSoup

API_BASE_URL = os.environ["API_BASE_URL"]
INGEST_TOKEN = os.environ["INGEST_TOKEN"]
SOURCE_CHANNELS = [c.strip() for c in os.environ.get("SOURCE_CHANNELS", "").split(",") if c.strip()]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DanangApartmentsBot/1.0)"}


def fetch_last_message_ids() -> dict[str, int]:
    resp = requests.get(
        f"{API_BASE_URL}/admin/sources", headers={"X-Ingest-Token": INGEST_TOKEN}, timeout=15
    )
    resp.raise_for_status()
    return {s["channel_username"]: s["last_message_id"] or 0 for s in resp.json()}


def scrape_channel(channel: str, since_id: int) -> list[dict]:
    resp = requests.get(f"https://t.me/s/{channel}", headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        print(f"[{channel}] fetch failed: HTTP {resp.status_code}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    posts = []
    for block in soup.select(".tgme_widget_message"):
        data_post = block.get("data-post", "")
        if "/" not in data_post:
            continue
        message_id = int(data_post.split("/")[-1])
        if message_id <= since_id:
            continue

        text_el = block.select_one(".tgme_widget_message_text")
        text = text_el.get_text("\n").strip() if text_el else ""

        photo_urls = []
        for photo_el in block.select(".tgme_widget_message_photo_wrap"):
            style = photo_el.get("style", "")
            match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
            if match:
                photo_urls.append(match.group(1))

        time_el = block.select_one("time")
        posted_at = time_el.get("datetime") if time_el else None

        posts.append(
            {
                "message_id": message_id,
                "text": text,
                "photo_urls": photo_urls,
                "posted_at": posted_at,
            }
        )
    return posts


def main() -> None:
    if not SOURCE_CHANNELS:
        print("SOURCE_CHANNELS is empty, nothing to do")
        return

    last_ids = fetch_last_message_ids()

    for channel in SOURCE_CHANNELS:
        since_id = last_ids.get(channel, 0)
        posts = scrape_channel(channel, since_id)
        if not posts:
            print(f"[{channel}] no new posts")
            continue

        resp = requests.post(
            f"{API_BASE_URL}/internal/ingest",
            headers={"X-Ingest-Token": INGEST_TOKEN},
            json={"channel_username": channel, "posts": posts},
            timeout=30,
        )
        resp.raise_for_status()
        print(f"[{channel}] ingested: {resp.json()}")


if __name__ == "__main__":
    main()
