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

import argparse
import os
import re
import time

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


def scrape_channel(channel: str, since_id: int, before: int | None = None) -> list[dict]:
    """Read one page of a channel's public preview.

    `before` walks backwards through history: t.me/s/<channel>?before=<id>
    returns the page of posts older than that id. Used only for the initial
    backfill — the scheduled run always reads the newest page.
    """
    url = f"https://t.me/s/{channel}"
    if before:
        url += f"?before={before}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        print(f"[{channel}] fetch failed: HTTP {resp.status_code}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    blocks = soup.select(".tgme_widget_message")
    if not blocks:
        # t.me/s/<name> only renders posts for public *channels*. Groups return
        # HTTP 200 with a members page and no posts, so a misconfigured source
        # would otherwise look like "nothing new" forever instead of an error.
        print(
            f"[{channel}] no posts on the page — this is normally a group/chat "
            f"(not a channel) or a private channel; it cannot be scraped this way"
        )
        return []

    posts = []
    for block in blocks:
        data_post = block.get("data-post", "")
        if "/" not in data_post:
            continue
        message_id = int(data_post.split("/")[-1])
        if message_id <= since_id:
            continue

        text_el = block.select_one(".tgme_widget_message_text")
        text = text_el.get_text("\n").strip() if text_el else ""
        if not text:
            # Photo-only posts (album continuations, stickers, service messages)
            # carry nothing to extract a price or city from.
            continue

        photo_urls = []
        for photo_el in block.select(".tgme_widget_message_photo_wrap"):
            style = photo_el.get("style", "")
            match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
            if match:
                photo_urls.append(match.group(1))

        # Must select on [datetime]: some posts render a <time> holding only a
        # display string, and picking that one yields None for every post.
        time_el = block.select_one("time[datetime]")
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


def ingest(channel: str, posts: list[dict]) -> dict:
    resp = requests.post(
        f"{API_BASE_URL}/internal/ingest",
        headers={"X-Ingest-Token": INGEST_TOKEN},
        json={"channel_username": channel, "posts": posts},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def backfill(pages: int) -> None:
    """Walk back through each channel's history to fill an empty database.

    A freshly deployed bot only sees posts published from that moment on, so
    the map stays empty for days. This pulls the recent history in one go —
    listings older than the TTL are expired by the server anyway, so there is
    no point going back further than a few pages.
    """
    for channel in SOURCE_CHANNELS:
        before = None
        total = {"published": 0, "held": 0, "duplicates": 0}
        for page in range(pages):
            posts = scrape_channel(channel, since_id=0, before=before)
            if not posts:
                print(f"[{channel}] page {page + 1}: no more posts")
                break
            try:
                result = ingest(channel, posts)
            except Exception as exc:
                print(f"[{channel}] page {page + 1} ingest FAILED: {exc}")
                break
            for key in total:
                total[key] += result.get(key, 0)
            print(f"[{channel}] page {page + 1}: {result}")
            # Continue from the oldest post on this page.
            before = min(p["message_id"] for p in posts)
            time.sleep(1)  # be polite to t.me
        print(f"[{channel}] backfill total: {total}")
        print()


def main() -> None:
    if not SOURCE_CHANNELS:
        print("SOURCE_CHANNELS is empty, nothing to do")
        return

    last_ids = fetch_last_message_ids()

    failures = 0
    for channel in SOURCE_CHANNELS:
        # One unreachable channel or a single ingest hiccup must not abort the
        # whole run — the remaining channels are independent of it.
        try:
            since_id = last_ids.get(channel, 0)
            posts = scrape_channel(channel, since_id)
            if not posts:
                print(f"[{channel}] no new posts")
                continue

            print(f"[{channel}] ingested: {ingest(channel, posts)}")
        except Exception as exc:
            failures += 1
            print(f"[{channel}] FAILED: {type(exc).__name__}: {exc}")

    if failures == len(SOURCE_CHANNELS):
        # Every source failing means something systemic (API down, bad token) —
        # exit non-zero so the scheduled run shows up as failed instead of green.
        raise SystemExit(f"all {failures} source(s) failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape rental listings from Telegram channels")
    parser.add_argument(
        "--backfill", type=int, metavar="PAGES",
        help="walk back PAGES pages of each channel's history instead of only "
             "reading the newest page (use once, to populate an empty database)",
    )
    args = parser.parse_args()

    if args.backfill:
        backfill(args.backfill)
    else:
        main()
