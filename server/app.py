"""Single Flask app: public listings API, moderation API, ingest endpoint for
the GitHub Actions scraper, and the Telegram bot webhook.

One app because PythonAnywhere's free tier only lets you run a single always-on
web app — everything that needs to be reachable over HTTP lives here.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

# Must run before any local import: db.models reads DATABASE_URL and
# server.telegram_api reads BOT_TOKEN at import time, so .env has to already
# be loaded into os.environ by the time those modules are imported.
load_dotenv()

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import Forbidden, NotFound

from db.models import (
    CITY_CENTERS,
    Listing,
    ListingStatus,
    Photo,
    PropertyType,
    RenovationQuality,
    SessionLocal,
    Source,
    SourceType,
    init_db,
)
from parser.extractor import extract
from server import ratelimit, telegram_api

ADMIN_API_TOKEN = os.environ.get("ADMIN_API_TOKEN", "")
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")
ADMIN_TELEGRAM_IDS = {int(x) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",") if x.strip()}
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:5000")
MEDIA_DIR = os.path.join(os.path.dirname(__file__), "..", "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

app = Flask(__name__)
init_db()


# --- helpers -----------------------------------------------------------------

def require_token(header_name: str, expected: str) -> None:
    if not expected or request.headers.get(header_name) != expected:
        raise Forbidden("invalid token")


def photo_url(filename_or_url: str) -> str:
    if filename_or_url.startswith("http"):
        return filename_or_url
    return f"{API_BASE_URL}/media/{os.path.basename(filename_or_url)}"


def listing_to_dict(listing: Listing) -> dict:
    return {
        "id": listing.id,
        "status": listing.status.value,
        "source_type": listing.source_type.value,
        "source_url": listing.source_url,
        "city": listing.city.value,
        "address_text": listing.address_text,
        "lat": listing.lat,
        "lng": listing.lng,
        "price_min_usd": listing.price_min_usd,
        "price_max_usd": listing.price_max_usd,
        "rooms": listing.rooms,
        "property_type": listing.property_type.value if listing.property_type else None,
        "renovation_quality": listing.renovation_quality.value if listing.renovation_quality else None,
        "pets_policy": listing.pets_policy.value,
        "area_sqm": listing.area_sqm,
        "floor": listing.floor,
        "furnished": listing.furnished,
        "has_parking": listing.has_parking,
        "has_pool": listing.has_pool,
        "description": listing.description,
        "contact": listing.contact,
        "photos": [
            {"url": photo_url(p.url), "position": p.position}
            for p in sorted(listing.photos, key=lambda p: p.position)
        ],
    }


# --- public API ----------------------------------------------------------------

@app.get("/media/<path:filename>")
def media(filename: str):
    return send_from_directory(MEDIA_DIR, filename)


@app.get("/listings")
def list_listings():
    with SessionLocal() as session:
        query = session.query(Listing).filter(Listing.status == ListingStatus.APPROVED)

        city = request.args.get("city")
        if city:
            query = query.filter(Listing.city == city)
        property_type = request.args.get("property_type")
        if property_type:
            query = query.filter(Listing.property_type == property_type)
        rooms = request.args.get("rooms")
        if rooms:
            query = query.filter(Listing.rooms == rooms)
        pets_policy = request.args.get("pets_policy")
        if pets_policy:
            query = query.filter(Listing.pets_policy == pets_policy)
        renovation_quality = request.args.get("renovation_quality")
        if renovation_quality:
            query = query.filter(Listing.renovation_quality == renovation_quality)
        price_min = request.args.get("price_min", type=float)
        if price_min is not None:
            query = query.filter(Listing.price_max_usd >= price_min)
        price_max = request.args.get("price_max", type=float)
        if price_max is not None:
            query = query.filter(Listing.price_min_usd <= price_max)

        listings = query.order_by(Listing.posted_at.desc()).all()
        return jsonify([listing_to_dict(l) for l in listings])


@app.get("/listings/<int:listing_id>")
def get_listing(listing_id: int):
    with SessionLocal() as session:
        listing = session.get(Listing, listing_id)
        if not listing or listing.status != ListingStatus.APPROVED:
            raise NotFound()
        return jsonify(listing_to_dict(listing))


# --- moderation API (called by the bot webhook and/or an admin directly) -----

@app.get("/admin/listings/pending")
def admin_list_pending():
    require_token("X-Admin-Token", ADMIN_API_TOKEN)
    with SessionLocal() as session:
        listings = (
            session.query(Listing)
            .filter(Listing.status == ListingStatus.PENDING)
            .order_by(Listing.created_at.asc())
            .all()
        )
        return jsonify([listing_to_dict(l) for l in listings])


@app.post("/admin/listings/<int:listing_id>/approve")
def admin_approve(listing_id: int):
    require_token("X-Admin-Token", ADMIN_API_TOKEN)
    with SessionLocal() as session:
        listing = session.get(Listing, listing_id)
        if not listing:
            raise NotFound()
        listing.status = ListingStatus.APPROVED
        session.commit()
        return jsonify({"ok": True})


@app.post("/admin/listings/<int:listing_id>/reject")
def admin_reject(listing_id: int):
    require_token("X-Admin-Token", ADMIN_API_TOKEN)
    with SessionLocal() as session:
        listing = session.get(Listing, listing_id)
        if not listing:
            raise NotFound()
        listing.status = ListingStatus.REJECTED
        session.commit()
        return jsonify({"ok": True})


@app.patch("/admin/listings/<int:listing_id>")
def admin_update(listing_id: int):
    require_token("X-Admin-Token", ADMIN_API_TOKEN)
    patch = request.get_json(force=True)
    editable_fields = {
        "city", "address_text", "lat", "lng", "price_min_usd", "price_max_usd",
        "rooms", "property_type", "renovation_quality", "pets_policy",
        "area_sqm", "description", "contact",
    }
    with SessionLocal() as session:
        listing = session.get(Listing, listing_id)
        if not listing:
            raise NotFound()
        for field, value in patch.items():
            if field in editable_fields:
                setattr(listing, field, value)
        session.commit()
        return jsonify(listing_to_dict(listing))


# --- ingest API (called by the GitHub Actions scraper) -----------------------

@app.get("/admin/sources")
def list_sources():
    require_token("X-Ingest-Token", INGEST_TOKEN)
    with SessionLocal() as session:
        sources = session.query(Source).filter(Source.is_active.is_(True)).all()
        return jsonify(
            [
                {"channel_username": s.channel_username, "last_message_id": s.last_message_id}
                for s in sources
            ]
        )


@app.post("/internal/ingest")
def ingest():
    """Body: {channel_username, posts: [{message_id, text, photo_urls, posted_at}]}.

    photo_urls are hotlinked directly from Telegram's public CDN (as scraped from
    the t.me/s/ preview page) — we don't re-host them, just store the URL.
    """
    require_token("X-Ingest-Token", INGEST_TOKEN)
    body = request.get_json(force=True)
    channel_username = body["channel_username"]
    posts = body.get("posts", [])

    created = 0
    with SessionLocal() as session:
        source = session.query(Source).filter_by(channel_username=channel_username).first()
        if not source:
            source = Source(platform=SourceType.TELEGRAM, channel_username=channel_username)
            session.add(source)
            session.flush()

        max_message_id = source.last_message_id or 0
        for post in posts:
            source_url = f"https://t.me/{channel_username}/{post['message_id']}"
            exists = session.query(Listing).filter_by(source_url=source_url).first()
            if exists:
                continue

            extracted = extract(post.get("text", ""))
            fallback_lat, fallback_lng = CITY_CENTERS.get(extracted.city, (None, None))
            listing = Listing(
                status=ListingStatus.PENDING,
                source_type=SourceType.TELEGRAM,
                source_channel=channel_username,
                source_url=source_url,
                source_message_id=post["message_id"],
                city=extracted.city,
                lat=fallback_lat,
                lng=fallback_lng,
                price_min_usd=extracted.price_min_usd,
                price_max_usd=extracted.price_max_usd,
                rooms=extracted.rooms,
                property_type=extracted.property_type,
                renovation_quality=extracted.renovation_quality,
                pets_policy=extracted.pets_policy,
                description=post.get("text"),
                raw_text=post.get("text"),
                posted_at=post.get("posted_at"),
            )
            for i, url in enumerate(post.get("photo_urls", [])):
                listing.photos.append(Photo(url=url, position=i))
            session.add(listing)
            created += 1
            max_message_id = max(max_message_id, post["message_id"])

        source.last_message_id = max_message_id
        session.commit()

    if created:
        _notify_admins_pending_count(created, channel_username)

    return jsonify({"created": created})


def _notify_admins_pending_count(created: int, channel_username: str) -> None:
    for admin_id in ADMIN_TELEGRAM_IDS:
        telegram_api.send_message(
            admin_id,
            f"Новых объявлений из @{channel_username}: {created}. Проверить: /pending",
        )


# --- Telegram bot webhook ------------------------------------------------------

def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_TELEGRAM_IDS


@app.post("/bot/webhook")
def bot_webhook():
    update = request.get_json(force=True)

    if "message" in update:
        _handle_message(update["message"])
    elif "callback_query" in update:
        _handle_callback(update["callback_query"])

    return jsonify({"ok": True})


def _handle_message(message: dict) -> None:
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text") or message.get("caption") or ""
    is_admin_user = _is_admin(user_id)

    # Loose anti-flood debounce: a few accidental rapid taps (double /start,
    # mashing a button) are just silently dropped — no warning, no penalty.
    # Admins are exempt so moderation clicks are never throttled.
    if not is_admin_user:
        with SessionLocal() as session:
            allowed, _ = ratelimit.check(session, user_id, "message", limit=5, window_seconds=10)
        if not allowed:
            return

    if text.startswith("/start"):
        telegram_api.send_message(
            chat_id,
            "Привет! Здесь актуальные объявления по аренде жилья в Дананге, Нячанге, "
            "Хошимине и других городах Вьетнама.\n\n"
            "Нашли объявление в Facebook, которого нет в боте? Пришлите его командой "
            "/submit <текст объявления и ссылка на оригинал>, можно с одним фото.",
            reply_markup=telegram_api.webapp_button("🏠 Смотреть квартиры", WEBAPP_URL),
        )
        return

    if text.startswith("/submit"):
        # Stricter throttle here: /submit writes to the DB and pings every
        # admin, so it's worth more than the generic debounce above. First
        # time over the limit just asks the user to slow down; only repeated
        # abuse across several separate windows escalates to a longer wait,
        # and that escalation decays after a day of normal use — never a ban.
        if not is_admin_user:
            with SessionLocal() as session:
                allowed, warning = ratelimit.check(
                    session,
                    user_id,
                    "submit",
                    limit=3,
                    window_seconds=15 * 60,
                    block_durations_seconds=[5 * 60, 30 * 60, 2 * 60 * 60, 24 * 60 * 60],
                )
            if not allowed:
                if warning:
                    telegram_api.send_message(chat_id, warning)
                return

        content = text[len("/submit"):].strip()
        if not content:
            telegram_api.send_message(
                chat_id, "После /submit пришлите текст объявления и ссылку на оригинал, например:\n"
                "/submit Квартира в Дананге, $400/мес https://facebook.com/..."
            )
            return
        _create_manual_listing(message, content)
        telegram_api.send_message(chat_id, "Спасибо! Объявление отправлено на модерацию.")
        return


def _create_manual_listing(message: dict, content: str) -> None:
    import re

    chat_id = message["chat"]["id"]
    username = message["from"].get("username")
    url_match = re.search(r"https?://\S+", content)

    extracted = extract(content)
    fallback_lat, fallback_lng = CITY_CENTERS.get(extracted.city, (None, None))

    with SessionLocal() as session:
        listing = Listing(
            status=ListingStatus.PENDING,
            source_type=SourceType.FACEBOOK if url_match and "facebook" in url_match.group(0) else SourceType.MANUAL,
            source_url=url_match.group(0) if url_match else f"tg://user?id={chat_id}",
            city=extracted.city,
            lat=fallback_lat,
            lng=fallback_lng,
            price_min_usd=extracted.price_min_usd,
            price_max_usd=extracted.price_max_usd,
            rooms=extracted.rooms,
            property_type=extracted.property_type,
            renovation_quality=extracted.renovation_quality,
            pets_policy=extracted.pets_policy,
            description=content,
            raw_text=content,
            contact=f"@{username}" if username else str(chat_id),
        )
        if message.get("photo"):
            file_id = message["photo"][-1]["file_id"]
            filename = f"manual_{message['message_id']}.jpg"
            telegram_api.download_file(file_id, os.path.join(MEDIA_DIR, filename))
            listing.photos.append(Photo(url=filename, position=0))
        session.add(listing)
        session.commit()
        listing_id = listing.id

    for admin_id in ADMIN_TELEGRAM_IDS:
        telegram_api.send_message(
            admin_id,
            f"Новое объявление #{listing_id} ждёт модерации:\n\n{content}",
            reply_markup=telegram_api.approve_reject_buttons(listing_id),
        )


def _handle_callback(callback_query: dict) -> None:
    user_id = callback_query["from"]["id"]
    if not _is_admin(user_id):
        telegram_api.answer_callback_query(callback_query["id"], "Только для модераторов.")
        return

    action, listing_id = callback_query["data"].split(":")
    with SessionLocal() as session:
        listing = session.get(Listing, int(listing_id))
        if not listing:
            telegram_api.answer_callback_query(callback_query["id"], "Объявление не найдено.")
            return
        listing.status = ListingStatus.APPROVED if action == "approve" else ListingStatus.REJECTED
        session.commit()

    message = callback_query["message"]
    suffix = "✅ Одобрено" if action == "approve" else "❌ Отклонено"
    telegram_api.edit_message_text(message["chat"]["id"], message["message_id"], f"{message['text']}\n\n{suffix}")
    telegram_api.answer_callback_query(callback_query["id"])


if __name__ == "__main__":
    app.run(port=5000, debug=True)
