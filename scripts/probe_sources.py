"""Check candidate Telegram sources before adding them to SOURCE_CHANNELS.

Answers the three things that decide whether a source is worth having:
is it a channel (groups have no t.me/s/ preview and can never be scraped),
is it still posting, and do its posts actually parse into priced listings.

    python scripts/probe_sources.py

Edit CANDIDATES below to test new names.
"""
import io
import re
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser.extractor import extract  # noqa: E402

CANDIDATES = [
    # already configured
    "danangrentaflat", "onewaydanang",
    # Nha Trang
    "nyachang_arenda_kvartir", "Arenda_Nyachang_Zhilye", "Viet_life_niachang",
    "nhatrangrental", "arenda_nhatrang",
    # Da Nang / general, worth checking
    "danang_arenda", "arenda_danang", "danangrent", "vietnamrent",
    "arenda_vietnam", "housing_danang", "danang_housing",
    "rent_danang", "dananghousing", "vietnam_arenda",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DanangApartmentsBot/1.0)"}
now = datetime.now(timezone.utc)

results = []
for name in CANDIDATES:
    try:
        r = requests.get(f"https://t.me/s/{name}", headers=HEADERS, timeout=20)
    except Exception as e:
        results.append((name, "ERROR", str(e)[:40], 0, None, 0))
        continue

    soup = BeautifulSoup(r.text, "html.parser")
    blocks = soup.select(".tgme_widget_message")

    if not blocks:
        # Distinguish "group (no web preview)" from "does not exist".
        r2 = requests.get(f"https://t.me/{name}", headers=HEADERS, timeout=20)
        s2 = BeautifulSoup(r2.text, "html.parser")
        title = s2.select_one(".tgme_page_title")
        extra = s2.select_one(".tgme_page_extra")
        extra_text = extra.get_text(strip=True) if extra else ""
        if not title:
            verdict = "NOT FOUND"
        elif "member" in extra_text or "участник" in extra_text:
            verdict = "GROUP (not scrapable)"
        else:
            verdict = "NO POSTS"
        results.append((name, verdict, (title.get_text(strip=True)[:38] if title else ""), 0, None, 0))
        continue

    # Freshness: how old is the newest post with a timestamp?
    newest = None
    for b in reversed(blocks):
        te = b.select_one("time[datetime]")
        if te:
            try:
                newest = datetime.fromisoformat(te["datetime"].replace("Z", "+00:00"))
                break
            except ValueError:
                pass
    age_days = (now - newest).days if newest else None

    # How many of the posts actually parse as listings with a price?
    parsed = 0
    for b in blocks:
        el = b.select_one(".tgme_widget_message_text")
        if not el:
            continue
        e = extract(el.get_text("\n").strip())
        if e.price_min_usd is not None:
            parsed += 1

    title_el = soup.select_one(".tgme_channel_info_header_title")
    results.append((
        name, "CHANNEL", (title_el.get_text(strip=True)[:38] if title_el else ""),
        len(blocks), age_days, parsed,
    ))

print(f"{'channel':<26} {'verdict':<22} {'posts':>5} {'age':>6} {'priced':>7}  title")
print("-" * 110)
for name, verdict, title, n, age, parsed in results:
    age_s = f"{age}d" if age is not None else "-"
    print(f"{name:<26} {verdict:<22} {n:>5} {age_s:>6} {parsed:>7}  {title}")

print("\nRECOMMENDED (channel, has posts, priced listings found):")
for name, verdict, title, n, age, parsed in results:
    if verdict == "CHANNEL" and parsed > 0:
        fresh = "fresh" if (age is not None and age <= 14) else f"stale({age}d)" if age is not None else "unknown age"
        print(f"  {name:<26} {parsed}/{n} priced, {fresh}  — {title}")
