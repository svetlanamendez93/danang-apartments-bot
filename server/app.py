"""Single Flask app: public listings API, moderation API, ingest endpoint for
the GitHub Actions scraper, and the Telegram bot webhook.

One app because PythonAnywhere's free tier only lets you run a single always-on
web app — everything that needs to be reachable over HTTP lives here.
"""
from __future__ import annotations

import hashlib
import html
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

# Must run before any local import: db.models reads DATABASE_URL and
# server.telegram_api reads BOT_TOKEN at import time, so .env has to already
# be loaded into os.environ by the time those modules are imported.
# Path is explicit (not just load_dotenv()) because under WSGI the process's
# working directory is not the project directory, so dotenv's default
# cwd-based search for ".env" fails silently and nothing gets loaded.
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
load_dotenv(_ENV_PATH)

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import Forbidden, NotFound

from db.models import (
    CHANNEL_DEFAULT_CITY,
    CITY_CENTERS,
    City,
    Listing,
    ListingStatus,
    PetsPolicy,
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

logger = logging.getLogger(__name__)

ADMIN_API_TOKEN = os.environ.get("ADMIN_API_TOKEN", "")
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")
ADMIN_TELEGRAM_IDS = {int(x) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",") if x.strip()}
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:5000")
# Telegram echoes this back on every webhook call, so we can tell a real update
# from anyone who simply guessed the URL. Set by scripts/set_webhook.py.
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
MEDIA_DIR = os.path.join(os.path.dirname(__file__), "..", "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

app = Flask(__name__)
init_db()


@app.after_request
def add_cors_headers(response):
    """The Mini App is served from GitHub Pages and calls this API on another
    origin, so without these headers the browser blocks every fetch and the map
    stays empty. Only the public read endpoints need to be reachable that way;
    admin/ingest routes are guarded by tokens regardless of origin."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response


# --- helpers -----------------------------------------------------------------

def require_token(header_name: str, expected: str) -> None:
    if not expected or request.headers.get(header_name) != expected:
        raise Forbidden("invalid token")


def photo_url(filename_or_url: str) -> str:
    if filename_or_url.startswith("http"):
        return filename_or_url
    return f"{API_BASE_URL}/media/{os.path.basename(filename_or_url)}"


def parse_posted_at(raw: str | None) -> datetime | None:
    """Turn the scraper's ISO-8601 <time datetime="..."> string into a datetime.

    The scraper sends whatever the t.me page had, which is a string (or nothing,
    for posts that render no <time> element). Handing that string straight to a
    DateTime column raises "SQLite DateTime type only accepts Python datetime",
    which would abort the whole ingest batch — so parse here and fall back to
    None rather than letting one odd post kill the run.

    Stored naive in UTC: the column is a plain DateTime, and mixing tz-aware and
    naive values in one column makes later comparisons raise.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Could not parse posted_at %r, storing NULL", raw)
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def fallback_coords(city: City, seed: str) -> tuple[float | None, float | None]:
    """Approximate coordinates for a listing whose address isn't geocoded yet.

    Straight city-center coordinates would stack every listing of a city on one
    pixel, where the markers hide each other and only the top one is clickable.
    A small deterministic offset (~up to 1.5km, derived from the listing's own
    source URL so it never moves between requests) keeps them individually
    visible until a moderator sets the real lat/lng.
    """
    center = CITY_CENTERS.get(city)
    if not center:
        return None, None
    digest = hashlib.sha256(seed.encode()).digest()
    # Two independent bytes -> offsets in [-0.0075, +0.0075] degrees.
    lat_offset = (digest[0] / 255 - 0.5) * 0.015
    lng_offset = (digest[1] / 255 - 0.5) * 0.015
    return center[0] + lat_offset, center[1] + lng_offset


def coerce_enum(enum_cls, raw: str | None):
    """Map a query-string value onto an enum member, or None if it isn't one.

    Filters come straight from the URL, so an unknown value must not reach the
    query — returning None makes the caller skip that filter instead.
    """
    if not raw:
        return None
    try:
        return enum_cls(raw)
    except ValueError:
        return None


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

        # Every filter is optional ("любое"), so each one is only applied when
        # the query string actually carries a value the enum recognises.
        city = coerce_enum(City, request.args.get("city"))
        if city:
            query = query.filter(Listing.city == city)
        property_type = coerce_enum(PropertyType, request.args.get("property_type"))
        if property_type:
            query = query.filter(Listing.property_type == property_type)
        rooms = request.args.get("rooms")
        if rooms:
            query = query.filter(Listing.rooms == rooms)
        pets_policy = coerce_enum(PetsPolicy, request.args.get("pets_policy"))
        if pets_policy:
            query = query.filter(Listing.pets_policy == pets_policy)
        renovation_quality = coerce_enum(RenovationQuality, request.args.get("renovation_quality"))
        if renovation_quality:
            query = query.filter(Listing.renovation_quality == renovation_quality)

        # A listing carries its own [min, max] range, so "fits my budget" means
        # the two ranges overlap — not that a single number sits inside one.
        price_min = request.args.get("price_min", type=float)
        if price_min is not None:
            query = query.filter(Listing.price_max_usd >= price_min)
        price_max = request.args.get("price_max", type=float)
        if price_max is not None:
            query = query.filter(Listing.price_min_usd <= price_max)

        limit = min(request.args.get("limit", default=500, type=int), 1000)
        listings = query.order_by(Listing.posted_at.desc().nullslast()).limit(limit).all()
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

            extracted = extract(
                post.get("text", ""),
                default_city=CHANNEL_DEFAULT_CITY.get(channel_username.lower()),
            )
            fallback_lat, fallback_lng = fallback_coords(extracted.city, source_url)
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
                area_sqm=extracted.area_sqm,
                description=post.get("text"),
                raw_text=post.get("text"),
                posted_at=parse_posted_at(post.get("posted_at")),
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
    # Without this check anyone who learns the URL can POST a handcrafted
    # update claiming to come from an admin id and approve or reject listings
    # at will — the handlers below trust update["from"]["id"] for authorisation.
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise Forbidden("bad webhook secret")

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

    if text.startswith("/start") or text.startswith("/help"):
        telegram_api.send_message(
            chat_id,
            "Привет! Здесь актуальные объявления по аренде жилья в Дананге, Нячанге, "
            "Хошимине и других городах Вьетнама.\n\n"
            "Нажмите кнопку ниже, чтобы открыть карту с фильтрами по цене, "
            "количеству комнат, типу жилья и питомцам.\n\n"
            "Нашли объявление в Facebook, которого нет в боте? Пришлите его командой\n"
            "/submit текст объявления и ссылка на оригинал\n"
            "— можно с одним фото.",
            reply_markup=telegram_api.webapp_button("🏠 Смотреть квартиры", WEBAPP_URL),
        )
        return

    if text.startswith("/pending"):
        if not is_admin_user:
            telegram_api.send_message(chat_id, "Эта команда только для модераторов.")
            return
        _send_next_pending(chat_id)
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

    # Any other text: point the user at what the bot actually understands
    # rather than staying silent, which reads as "the bot is broken".
    if text.startswith("/"):
        telegram_api.send_message(chat_id, "Неизвестная команда. Доступно: /start, /help, /submit")
    else:
        telegram_api.send_message(
            chat_id,
            "Чтобы посмотреть объявления, откройте карту командой /start.\n"
            "Чтобы предложить объявление — /submit с текстом и ссылкой.",
        )


def _listing_summary(listing: Listing) -> str:
    """Short moderator-facing description of a listing awaiting review."""
    price = "цена не указана"
    if listing.price_min_usd is not None:
        if listing.price_max_usd and listing.price_max_usd != listing.price_min_usd:
            price = f"${listing.price_min_usd:.0f}–{listing.price_max_usd:.0f}"
        else:
            price = f"${listing.price_min_usd:.0f}"

    city_labels = {
        City.DA_NANG: "Дананг", City.NHA_TRANG: "Нячанг", City.HO_CHI_MINH: "Хошимин",
        City.HANOI: "Ханой", City.HOI_AN: "Хойан", City.OTHER: "город не определён",
    }
    rooms = {"studio": "студия"}.get(listing.rooms, f"{listing.rooms} комн." if listing.rooms else "комнаты не указаны")

    body = (listing.description or "")[:600]
    return (
        f"<b>Объявление #{listing.id}</b>\n"
        f"{html.escape(city_labels.get(listing.city, '—'))} · {price} · {html.escape(rooms)}\n"
        f"Источник: {html.escape(listing.source_url)}\n\n"
        f"{html.escape(body)}"
    )


def _send_next_pending(chat_id: int) -> None:
    """Show the moderator the oldest listing still awaiting review.

    Scraped listings land in PENDING and are invisible in the Mini App until
    someone approves them, so without this queue nothing a scraper finds could
    ever reach users.
    """
    with SessionLocal() as session:
        listing = (
            session.query(Listing)
            .filter(Listing.status == ListingStatus.PENDING)
            .order_by(Listing.created_at.asc())
            .first()
        )
        if not listing:
            telegram_api.send_message(chat_id, "Очередь пуста — все объявления проверены. 👍")
            return

        remaining = session.query(Listing).filter(Listing.status == ListingStatus.PENDING).count()
        caption = _listing_summary(listing) + f"\n\nОсталось в очереди: {remaining}"
        photo_urls = [p.url for p in sorted(listing.photos, key=lambda p: p.position)]
        buttons = telegram_api.approve_reject_buttons(listing.id)

    # Telegram caps a photo caption at 1024 chars, so long posts go as a plain
    # message with the first photo sent separately rather than being truncated.
    if photo_urls and len(caption) <= 1024:
        sent = telegram_api.send_photo(chat_id, photo_url(photo_urls[0]), caption, reply_markup=buttons)
        if sent:
            return
        # Photo hotlink can fail (CDN link expired); fall through to text.
    telegram_api.send_message(chat_id, caption, reply_markup=buttons)


def _create_manual_listing(message: dict, content: str) -> None:
    import re

    chat_id = message["chat"]["id"]
    username = message["from"].get("username")
    url_match = re.search(r"https?://\S+", content)

    extracted = extract(content)
    source_url = url_match.group(0) if url_match else f"tg://user?id={chat_id}"
    fallback_lat, fallback_lng = fallback_coords(extracted.city, f"{source_url}:{message['message_id']}")

    with SessionLocal() as session:
        listing = Listing(
            status=ListingStatus.PENDING,
            source_type=SourceType.FACEBOOK if url_match and "facebook" in url_match.group(0) else SourceType.MANUAL,
            source_url=source_url,
            city=extracted.city,
            lat=fallback_lat,
            lng=fallback_lng,
            price_min_usd=extracted.price_min_usd,
            price_max_usd=extracted.price_max_usd,
            rooms=extracted.rooms,
            property_type=extracted.property_type,
            renovation_quality=extracted.renovation_quality,
            pets_policy=extracted.pets_policy,
            area_sqm=extracted.area_sqm,
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
            f"Новое объявление #{listing_id} ждёт модерации:\n\n{html.escape(content[:600])}",
            reply_markup=telegram_api.approve_reject_buttons(listing_id),
        )


def _handle_callback(callback_query: dict) -> None:
    user_id = callback_query["from"]["id"]
    if not _is_admin(user_id):
        telegram_api.answer_callback_query(callback_query["id"], "Только для модераторов.")
        return

    # callback_data is attacker-controllable in principle, so parse defensively
    # instead of letting a malformed value raise inside the webhook.
    parts = callback_query.get("data", "").split(":")
    if len(parts) != 2 or parts[0] not in ("approve", "reject") or not parts[1].isdigit():
        telegram_api.answer_callback_query(callback_query["id"], "Некорректная команда.")
        return
    action, listing_id = parts[0], int(parts[1])

    with SessionLocal() as session:
        listing = session.get(Listing, listing_id)
        if not listing:
            telegram_api.answer_callback_query(callback_query["id"], "Объявление не найдено.")
            return
        listing.status = ListingStatus.APPROVED if action == "approve" else ListingStatus.REJECTED
        session.commit()

    message = callback_query["message"]
    chat_id = message["chat"]["id"]
    suffix = "✅ Одобрено" if action == "approve" else "❌ Отклонено"

    # The queue message may be a photo (caption) or plain text — editing the
    # wrong one fails, so just strip the buttons to mark it handled.
    telegram_api.edit_message_reply_markup(chat_id, message["message_id"], None)
    telegram_api.send_message(chat_id, f"Объявление #{listing_id}: {suffix}")
    telegram_api.answer_callback_query(callback_query["id"], suffix)

    # Keep the moderator moving through the queue without retyping /pending.
    _send_next_pending(chat_id)


if __name__ == "__main__":
    app.run(port=5000, debug=True)
