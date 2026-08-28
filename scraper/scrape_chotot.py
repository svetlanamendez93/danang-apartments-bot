"""Pull rental listings from Chotot, Vietnam's main classifieds site.

Telegram channels give a few dozen listings a day between them. Chotot has
thousands standing at any moment for Da Nang alone, and — the reason it matters
most here — its public API returns them already structured: price, rooms, area,
district, photos, and **exact coordinates**. That removes the guesswork that
makes Telegram listings pile onto one fallback point.

Endpoint: https://gateway.chotot.com/v1/public/ad-listing — the same one the
site's own frontend calls, no key and no login.

Sale and rent share a category tree, so listings are separated by price: a
monthly rent is millions of dong, a sale price is hundreds of millions or more.

    python -m scraper.scrape_chotot            # recent listings
    python -m scraper.scrape_chotot --pages 20 # go deeper
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

API_BASE_URL = os.environ["API_BASE_URL"]
INGEST_TOKEN = os.environ["INGEST_TOKEN"]

LISTING_URL = "https://gateway.chotot.com/v1/public/ad-listing"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DanangApartmentsBot/1.0)",
    "Accept": "application/json",
}

# Chotot area codes, from /v1/public/chapy-pro/regions.
AREAS: dict[str, int] = {
    "da_nang": 17,
    "nha_trang": 44,     # Khánh Hòa province
    "ho_chi_minh": 13,
    "hanoi": 12,
    "hoi_an": 16,        # Quảng Nam province
}

# Rental categories. 1020 (houses) mixes rent and sale, hence the price filter.
CATEGORIES: dict[int, str] = {
    1010: "apartment",   # Căn hộ/Chung cư
    1020: "house",       # Nhà ở
    1050: "room",        # Phòng trọ
}

# A monthly rent in dong. Anything above this is a sale price, not a rent.
MIN_RENT_VND = 1_000_000
MAX_RENT_VND = 300_000_000

PAGE_SIZE = 50
VND_PER_USD = 26_000


def fetch_page(area: int, category: int, offset: int) -> list[dict]:
    resp = requests.get(
        LISTING_URL,
        params={"area": area, "cg": category, "limit": PAGE_SIZE, "o": offset, "st": "u"},
        headers=HEADERS,
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"    HTTP {resp.status_code} for area={area} cg={category} o={offset}")
        return []
    return resp.json().get("ads") or []


def to_post(ad: dict, city: str, property_type: str) -> dict | None:
    """Map one Chotot ad onto the ingest payload, or None if it isn't a rental."""
    price_vnd = ad.get("price")
    if not isinstance(price_vnd, (int, float)):
        return None
    if not (MIN_RENT_VND <= price_vnd <= MAX_RENT_VND):
        return None  # a sale price, or a placeholder

    list_id = ad.get("list_id")
    if not list_id:
        return None

    lat, lng = ad.get("latitude"), ad.get("longitude")
    has_coords = isinstance(lat, (int, float)) and isinstance(lng, (int, float))

    rooms = ad.get("rooms")
    rooms_str = None
    if isinstance(rooms, int) and rooms > 0:
        rooms_str = str(min(rooms, 4))

    size = ad.get("size")
    area_sqm = float(size) if isinstance(size, (int, float)) and 5 <= size <= 2000 else None

    address = ", ".join(p for p in [ad.get("ward_name"), ad.get("area_name")] if p)

    posted_at = None
    if isinstance(ad.get("list_time"), (int, float)):
        posted_at = datetime.fromtimestamp(ad["list_time"] / 1000, tz=timezone.utc).isoformat()

    body = (ad.get("body") or "").strip()
    subject = (ad.get("subject") or "").strip()
    text = f"{subject}\n\n{body}".strip()

    return {
        "message_id": int(list_id),
        "text": text,
        "photo_urls": [u for u in (ad.get("images") or []) if isinstance(u, str)][:10],
        "posted_at": posted_at,
        "url": f"https://www.chotot.com/{list_id}.htm",
        # Chotot states these outright, so they are sent as facts rather than
        # left to the text extractor to guess at.
        "structured": {
            "city": city,
            "property_type": property_type,
            "price_min_usd": round(price_vnd / VND_PER_USD),
            "rooms": rooms_str,
            "area_sqm": area_sqm,
            "address_text": address or None,
            "lat": lat if has_coords else None,
            "lng": lng if has_coords else None,
        },
    }


def ingest(source: str, posts: list[dict]) -> dict:
    resp = requests.post(
        f"{API_BASE_URL}/internal/ingest",
        headers={"X-Ingest-Token": INGEST_TOKEN},
        json={"channel_username": source, "source_type": "chotot", "posts": posts},
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()


def run(cities: list[str], pages: int) -> None:
    for city in cities:
        area = AREAS.get(city)
        if not area:
            print(f"[{city}] no Chotot area code, skipping")
            continue

        for category, property_type in CATEGORIES.items():
            totals = {"published": 0, "held": 0, "duplicates": 0}
            for page in range(pages):
                ads = fetch_page(area, category, page * PAGE_SIZE)
                if not ads:
                    break

                posts = []
                for ad in ads:
                    post = to_post(ad, city, property_type)
                    if post:
                        posts.append(post)

                if posts:
                    try:
                        result = ingest(f"chotot:{city}:{category}", posts)
                    except Exception as exc:
                        print(f"[{city}/{category}] ingest FAILED: {exc}")
                        break
                    for key in totals:
                        totals[key] += result.get(key, 0)

                print(f"[{city}/{category}] page {page + 1}: {len(posts)}/{len(ads)} were rentals")
                if len(ads) < PAGE_SIZE:
                    break
                time.sleep(0.7)  # be polite

            print(f"[{city}/{category}] total: {totals}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape rentals from Chotot")
    parser.add_argument("--pages", type=int, default=4, help="pages per city and category")
    parser.add_argument("--cities", default="da_nang,nha_trang",
                        help="comma-separated city keys")
    args = parser.parse_args()
    run([c.strip() for c in args.cities.split(",") if c.strip()], args.pages)
