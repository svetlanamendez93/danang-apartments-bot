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
from datetime import datetime, timedelta, timezone

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
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
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
    RateLimitState,
    RenovationQuality,
    SavedFilter,
    SessionLocal,
    Source,
    SourceType,
    TgUser,
    init_db,
)
from parser.cleaner import clean_post_text
from parser.extractor import extract
from server import bot_ui, i18n, quality, ratelimit, telegram_api

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

# `rooms` is a free-form column ("studio", "1"…"4"), so filter values are
# validated against this set rather than an enum.
ROOM_CHOICES = {"studio", "1", "2", "3", "4"}

# After this long a rental has almost certainly been taken; listings older
# than this are hidden automatically so the map stays trustworthy.
LISTING_TTL_DAYS = int(os.environ.get("LISTING_TTL_DAYS", "45"))

# Cap on how many listings one subscription receives per ingest, so a large
# scrape cannot dump dozens of messages into a chat at once.
SUBSCRIPTION_BATCH_LIMIT = 5

app = Flask(__name__)
init_db()


@app.before_request
def throttle_public_api(response=None):
    """Keep one client from burning the whole daily CPU quota.

    Only the public read endpoints are throttled: the bot webhook must never be
    rate-limited (Telegram would retry and the bot would look broken), and the
    admin/ingest routes already require a token.
    """
    if not request.path.startswith("/listings"):
        return None
    # PythonAnywhere puts the real client IP here; remote_addr is their proxy.
    ip = (request.headers.get("X-Real-IP") or request.remote_addr or "unknown").split(",")[0].strip()
    if not ratelimit.check_http(ip, limit=120, window_seconds=60):
        return jsonify({"error": "too many requests"}), 429
    return None


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


def coerce_enum_list(enum_cls, raw: str | None) -> list:
    """Parse a comma-separated multi-select filter into enum members.

    The UI lets a user tick several rooms counts or property types at once
    ("1 or 2 bedrooms"), which arrives as ?rooms=1,2. Unknown values are
    dropped rather than rejected, so an outdated client can't break the query.
    """
    if not raw:
        return []
    members = [coerce_enum(enum_cls, part.strip()) for part in raw.split(",")]
    return [m for m in members if m is not None]


def parse_str_list(raw: str | None, allowed: set[str]) -> list[str]:
    """Same as coerce_enum_list, for the free-form `rooms` column."""
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip() in allowed]


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
        "posted_at": listing.posted_at.isoformat() if listing.posted_at else None,
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

        # Every filter is optional ("любое"). Those with more than two choices
        # accept several values at once (?rooms=1,2 = "1 or 2"), so each is an
        # IN over whatever the client ticked.
        cities = coerce_enum_list(City, request.args.get("city"))
        if cities:
            query = query.filter(Listing.city.in_(cities))
        property_types = coerce_enum_list(PropertyType, request.args.get("property_type"))
        if property_types:
            query = query.filter(Listing.property_type.in_(property_types))
        rooms = parse_str_list(request.args.get("rooms"), ROOM_CHOICES)
        if rooms:
            query = query.filter(Listing.rooms.in_(rooms))
        renovations = coerce_enum_list(RenovationQuality, request.args.get("renovation_quality"))
        if renovations:
            query = query.filter(Listing.renovation_quality.in_(renovations))

        # Pets is a yes/no question, so it stays a single choice.
        pets_policy = coerce_enum(PetsPolicy, request.args.get("pets_policy"))
        if pets_policy:
            query = query.filter(Listing.pets_policy == pets_policy)

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

    published = 0
    held = 0
    flagged = 0
    duplicates = 0

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

            text = post.get("text", "") or ""
            fingerprint = quality.content_hash(text)

            # The same flat is routinely cross-posted to several channels and
            # reposted weeks later; without this the map fills with copies.
            if session.query(Listing).filter_by(content_hash=fingerprint).first():
                duplicates += 1
                max_message_id = max(max_message_id, post["message_id"])
                continue

            extracted = extract(
                text,
                default_city=CHANNEL_DEFAULT_CITY.get(channel_username.lower()),
            )
            verdict = quality.assess(text, extracted.city, extracted.price_min_usd, extracted.rooms)

            if not source.auto_publish:
                status = ListingStatus.PENDING
            elif verdict.publish:
                status = ListingStatus.APPROVED
            else:
                status = ListingStatus.REJECTED

            if status == ListingStatus.APPROVED:
                published += 1
                if verdict.needs_review:
                    flagged += 1
            elif status == ListingStatus.REJECTED:
                held += 1

            fallback_lat, fallback_lng = fallback_coords(extracted.city, source_url)
            listing = Listing(
                status=status,
                content_hash=fingerprint,
                quality_note=verdict.reason,
                needs_review=verdict.needs_review,
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
                # Cleaned for display; raw_text keeps the post verbatim.
                description=clean_post_text(text),
                raw_text=text,
                posted_at=parse_posted_at(post.get("posted_at")),
            )
            for i, url in enumerate(post.get("photo_urls", [])):
                listing.photos.append(Photo(url=url, position=i))
            session.add(listing)
            max_message_id = max(max_message_id, post["message_id"])

        source.last_message_id = max_message_id
        expired = _expire_stale_listings(session)
        session.commit()

    if published:
        # Push to subscribers only after the transaction has committed, so a
        # failure here cannot roll back the ingest itself.
        try:
            deliver_subscriptions()
        except Exception:
            logger.exception("Subscription delivery failed")

    if published or held:
        _notify_admins_ingest(channel_username, published, held, flagged, duplicates)

    return jsonify({
        "published": published, "held": held, "flagged": flagged,
        "duplicates": duplicates, "expired": expired,
    })


def _expire_stale_listings(session) -> int:
    """Hide listings old enough that the flat is almost certainly gone.

    Nothing removes a listing when it gets rented, so without this the map
    slowly fills with places nobody can rent any more — the same staleness
    problem that made a manual approval queue unworkable.
    """
    cutoff = datetime.utcnow() - timedelta(days=LISTING_TTL_DAYS)
    stale = (
        session.query(Listing)
        .filter(Listing.status == ListingStatus.APPROVED)
        .filter(Listing.posted_at.isnot(None), Listing.posted_at < cutoff)
    )
    count = stale.count()
    if count:
        stale.update({Listing.status: ListingStatus.EXPIRED}, synchronize_session=False)
    return count


def _notify_admins_ingest(channel: str, published: int, held: int, flagged: int, duplicates: int) -> None:
    """One digest per scrape run — a message per listing would be unusable at
    the rate the channels post."""
    lines = [f"📥 <b>@{channel}</b>", f"Опубликовано: {published}"]
    if flagged:
        lines.append(f"⚠️ Из них требуют проверки: {flagged} → /review")
    if held:
        lines.append(f"🚫 Отсеяно автоматически: {held} → /rejected")
    if duplicates:
        lines.append(f"♻️ Пропущено дублей: {duplicates}")

    for admin_id in ADMIN_TELEGRAM_IDS:
        telegram_api.send_message(admin_id, "\n".join(lines))


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

    # Always answer 200, even on an unexpected error: Telegram retries any
    # update it did not get an OK for, so a bug on one message would otherwise
    # turn into the same failing update arriving again and again.
    try:
        if "message" in update:
            _handle_message(update["message"])
        elif "callback_query" in update:
            _handle_callback(update["callback_query"])
    except Exception:
        logger.exception("Failed to handle update %s", update.get("update_id"))

    return jsonify({"ok": True})


def _remember_user(sender: dict) -> None:
    """Record who talks to the bot, so /stats can report real audience size."""
    with SessionLocal() as session:
        user = session.query(TgUser).filter_by(telegram_id=sender["id"]).first()
        if user:
            if user.username != sender.get("username"):
                user.username = sender.get("username")
                session.commit()
            return
        # Seed the language from the Telegram client so someone who doesn't
        # read Russian sees their own language on the very first message,
        # rather than having to find the switch first.
        session.add(TgUser(
            telegram_id=sender["id"],
            username=sender.get("username"),
            lang=i18n.normalize_lang(sender.get("language_code")),
        ))
        try:
            session.commit()
        except IntegrityError:
            # Two messages from a new user can race; the row exists either way.
            session.rollback()


def _user_lang(sender: dict) -> str:
    """The user's chosen language, or their Telegram client's as a starting point."""
    with SessionLocal() as session:
        user = session.query(TgUser).filter_by(telegram_id=sender["id"]).first()
        if user and user.lang:
            return user.lang
    return i18n.normalize_lang(sender.get("language_code"))


def _set_user_lang(telegram_id: int, lang: str) -> None:
    with SessionLocal() as session:
        user = session.query(TgUser).filter_by(telegram_id=telegram_id).first()
        if user:
            user.lang = lang
            session.commit()
    # Re-label the button beside the input field in the language just chosen.
    _sync_menu_button(telegram_id, lang)


def _sync_menu_button(chat_id: int, lang: str) -> None:
    """Point this chat's input-field button at the Mini App, in `lang`.

    Set per chat rather than globally so each user gets their own language;
    the global default is set once by scripts/set_bot_commands.py.
    """
    if not WEBAPP_URL:
        return
    try:
        telegram_api.set_chat_menu_button(i18n.t("menu_button", lang), WEBAPP_URL, chat_id)
    except Exception:
        logger.exception("Could not set the chat menu button for %s", chat_id)


def _send_main_menu(chat_id: int, lang: str, is_admin_user: bool) -> None:
    telegram_api.send_message(
        chat_id,
        f"{i18n.t('welcome_title', lang)}\n\n{i18n.t('welcome_body', lang)}",
        reply_markup=bot_ui.main_menu(lang, WEBAPP_URL, is_admin=is_admin_user),
    )
    # Sent as a second message because one message can carry only one keyboard,
    # and the inline menu above and the persistent one below are different
    # kinds. This one stays on screen for the rest of the conversation.
    telegram_api.send_message(
        chat_id,
        i18n.t("menu_hint", lang),
        reply_markup=bot_ui.persistent_keyboard(lang, WEBAPP_URL),
    )


def _handle_message(message: dict) -> None:
    sender = message.get("from")
    if not sender:
        return  # channel posts and some service messages carry no sender
    chat_id = message["chat"]["id"]
    user_id = sender["id"]
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

    _remember_user(sender)
    lang = _user_lang(sender)

    # Persistent-keyboard taps arrive as plain text carrying the button label,
    # so translate a label back into the command it stands for. Matched across
    # all languages, since an old keyboard may still show the previous ones.
    button_action = bot_ui.action_for_button(text)
    if button_action:
        text = f"/{button_action}"

    if text.startswith("/start"):
        _sync_menu_button(chat_id, lang)
        _send_main_menu(chat_id, lang, is_admin_user)
        return

    if text.startswith("/help"):
        telegram_api.send_message(chat_id, i18n.t("help_text", lang),
                                  reply_markup=bot_ui.main_menu(lang, WEBAPP_URL, is_admin=is_admin_user))
        return

    if text.startswith("/language") or text.startswith("/lang"):
        telegram_api.send_message(chat_id, i18n.t("choose_language", lang),
                                  reply_markup=bot_ui.language_keyboard(lang))
        return

    if text.startswith("/latest"):
        _send_listing_page(chat_id, lang, offset=0)
        return

    if text.startswith("/subscribe") or text.startswith("/alerts"):
        _send_subscription_menu(chat_id, user_id, lang)
        return

    # --- admin commands ---
    if text.startswith("/stats"):
        if not is_admin_user:
            telegram_api.send_message(chat_id, i18n.t("admins_only", lang))
            return
        telegram_api.send_message(chat_id, _render_stats())
        return

    if text.startswith("/review"):
        if not is_admin_user:
            telegram_api.send_message(chat_id, i18n.t("admins_only", lang))
            return
        _send_review_queue(chat_id, lang, flagged_only=True)
        return

    if text.startswith("/pending"):
        if not is_admin_user:
            telegram_api.send_message(chat_id, i18n.t("admins_only", lang))
            return
        _send_review_queue(chat_id, lang, flagged_only=False)
        return

    if text.startswith("/spam"):
        if not is_admin_user:
            telegram_api.send_message(chat_id, i18n.t("admins_only", lang))
            return
        telegram_api.send_message(chat_id, _render_spam_report())
        return

    if text.startswith("/submit"):
        # Stricter throttle here: /submit writes to the DB and pings every
        # admin, so it's worth more than the generic debounce above. First
        # time over the limit just asks the user to slow down; only repeated
        # abuse across several separate windows escalates to a longer wait,
        # and that escalation decays after a day of normal use — never a ban.
        if not is_admin_user:
            with SessionLocal() as session:
                allowed, blocked_minutes = ratelimit.check(
                    session,
                    user_id,
                    "submit",
                    limit=3,
                    window_seconds=15 * 60,
                    block_durations_seconds=[5 * 60, 30 * 60, 2 * 60 * 60, 24 * 60 * 60],
                )
            if not allowed:
                if blocked_minutes:
                    telegram_api.send_message(
                        chat_id, i18n.t("rate_limited", lang, minutes=blocked_minutes)
                    )
                return

        content = text[len("/submit"):].strip()
        if not content:
            telegram_api.send_message(chat_id, i18n.t("submit_prompt", lang))
            return
        _create_manual_listing(message, content)
        telegram_api.send_message(chat_id, i18n.t("submit_thanks", lang))
        return

    # Anything else: show the menu rather than staying silent, which reads as
    # a broken bot.
    telegram_api.send_message(
        chat_id,
        i18n.t("unknown_command", lang),
        reply_markup=bot_ui.main_menu(lang, WEBAPP_URL, is_admin=is_admin_user),
    )


# --- sending listings into the chat ------------------------------------------

LISTINGS_PER_PAGE = 5


def _send_listing(chat_id: int, listing: Listing, lang: str, prefix: str = "") -> None:
    """Send one listing with every photo the original post had.

    Photos go as an album so the whole set stays one swipeable unit; the card
    text follows as its own message because an album caption is capped well
    below what a listing description needs.
    """
    photo_urls = [photo_url(p.url) for p in sorted(listing.photos, key=lambda p: p.position)]
    header = f"{prefix}\n" if prefix else ""

    if len(photo_urls) > 1:
        # Caption on the album is short; the full card follows separately.
        short = header + bot_ui.render_card(listing, lang, body_chars=bot_ui.CAPTION_LIMIT)
        sent = telegram_api.send_media_group(chat_id, photo_urls, short[: bot_ui.CAPTION_LIMIT])
        if sent:
            telegram_api.send_message(
                chat_id,
                bot_ui.render_card(listing, lang, body_chars=3500),
                reply_markup=bot_ui.listing_buttons(listing, lang),
            )
            return
    elif len(photo_urls) == 1:
        caption = header + bot_ui.render_card(listing, lang, body_chars=bot_ui.CAPTION_LIMIT)
        sent = telegram_api.send_photo(
            chat_id, photo_urls[0], caption[: bot_ui.CAPTION_LIMIT],
            reply_markup=bot_ui.listing_buttons(listing, lang),
        )
        if sent:
            return

    # No photos, or the CDN links have expired and Telegram refused them.
    telegram_api.send_message(
        chat_id,
        header + bot_ui.render_card(listing, lang, body_chars=3500),
        reply_markup=bot_ui.listing_buttons(listing, lang),
    )


def _send_listing_page(chat_id: int, lang: str, offset: int) -> None:
    """Browse published listings inside the chat, for people who would rather
    not open the Mini App at all."""
    with SessionLocal() as session:
        query = (
            session.query(Listing)
            .filter(Listing.status == ListingStatus.APPROVED)
            .order_by(Listing.posted_at.desc().nullslast(), Listing.id.desc())
        )
        total = query.count()
        listings = query.offset(offset).limit(LISTINGS_PER_PAGE).all()
        for listing in listings:
            _ = listing.photos  # load before the session closes

        if not listings:
            key = "no_listings" if offset == 0 else "no_more_listings"
            telegram_api.send_message(chat_id, i18n.t(key, lang))
            return

        if offset == 0:
            telegram_api.send_message(chat_id, i18n.t("latest_intro", lang))

        for listing in listings:
            _send_listing(chat_id, listing, lang)

    next_offset = offset + LISTINGS_PER_PAGE
    if next_offset < total:
        telegram_api.send_message(
            chat_id,
            f"{next_offset} / {total}",
            reply_markup={"inline_keyboard": [[
                {"text": i18n.t("btn_more", lang), "callback_data": f"latest:{next_offset}"}
            ]]},
        )


# --- subscriptions ------------------------------------------------------------

def _send_subscription_menu(chat_id: int, telegram_id: int, lang: str) -> None:
    with SessionLocal() as session:
        user = session.query(TgUser).filter_by(telegram_id=telegram_id).first()
        sub = None
        if user:
            sub = session.query(SavedFilter).filter_by(user_id=user.id).first()
        active = bool(sub and sub.is_active)

    toggle = "btn_sub_disable" if active else "btn_sub_enable"
    rows = [[{"text": i18n.t(toggle, lang), "callback_data": "sub:toggle"}]]
    rows.append([{"text": i18n.t("btn_back", lang), "callback_data": "menu:main"}])

    status = i18n.t("sub_on" if active else "sub_off", lang)
    telegram_api.send_message(
        chat_id,
        f"{i18n.t('sub_menu_title', lang)}\n\n{status}",
        reply_markup={"inline_keyboard": rows},
    )


def _toggle_subscription(telegram_id: int) -> bool:
    """Flip the user's subscription; returns whether it is now active."""
    with SessionLocal() as session:
        user = session.query(TgUser).filter_by(telegram_id=telegram_id).first()
        if not user:
            user = TgUser(telegram_id=telegram_id)
            session.add(user)
            session.flush()

        sub = session.query(SavedFilter).filter_by(user_id=user.id).first()
        if not sub:
            # Start from the newest listing so switching on doesn't replay the
            # whole back catalogue into the user's chat.
            latest = session.query(func.max(Listing.id)).scalar() or 0
            sub = SavedFilter(user_id=user.id, is_active=True, last_sent_listing_id=latest)
            session.add(sub)
        else:
            sub.is_active = not sub.is_active
        session.commit()
        return sub.is_active


def deliver_subscriptions() -> int:
    """Push newly published listings to everyone subscribed to matching filters.

    Called after each ingest. Delivery is capped per run so one big scrape
    cannot flood a chat, and last_sent_listing_id makes it idempotent.
    """
    sent = 0
    with SessionLocal() as session:
        subs = session.query(SavedFilter).filter(SavedFilter.is_active.is_(True)).all()
        for sub in subs:
            user = session.get(TgUser, sub.user_id)
            if not user:
                continue
            listings = (
                session.query(Listing)
                .filter(Listing.status == ListingStatus.APPROVED)
                .filter(Listing.id > sub.last_sent_listing_id)
                .order_by(Listing.id.asc())
                .limit(SUBSCRIPTION_BATCH_LIMIT)
                .all()
            )
            if not listings:
                continue
            for listing in listings:
                if sub.matches(listing):
                    _send_listing(
                        user.telegram_id, listing, user.lang or i18n.DEFAULT_LANG,
                        prefix=i18n.t("sub_new_listing", user.lang or i18n.DEFAULT_LANG),
                    )
                    sent += 1
                sub.last_sent_listing_id = max(sub.last_sent_listing_id, listing.id)
        session.commit()
    return sent


# --- admin reporting ----------------------------------------------------------

def _render_stats() -> str:
    """Operational summary for the admin: is the pipeline healthy, what's live."""
    now = datetime.utcnow()
    with SessionLocal() as session:
        by_status = dict(
            session.query(Listing.status, func.count(Listing.id)).group_by(Listing.status).all()
        )
        by_city = (
            session.query(Listing.city, func.count(Listing.id))
            .filter(Listing.status == ListingStatus.APPROVED)
            .group_by(Listing.city)
            .order_by(func.count(Listing.id).desc())
            .all()
        )
        by_source = (
            session.query(Listing.source_channel, func.count(Listing.id))
            .filter(Listing.status == ListingStatus.APPROVED)
            .group_by(Listing.source_channel)
            .order_by(func.count(Listing.id).desc())
            .all()
        )
        last_24h = session.query(Listing).filter(Listing.created_at > now - timedelta(hours=24)).count()
        last_7d = session.query(Listing).filter(Listing.created_at > now - timedelta(days=7)).count()
        newest = session.query(func.max(Listing.created_at)).scalar()
        flagged = session.query(Listing).filter(
            Listing.needs_review.is_(True), Listing.status == ListingStatus.APPROVED
        ).count()
        users = session.query(TgUser).count()
        subs = session.query(SavedFilter).filter(SavedFilter.is_active.is_(True)).count()

    def n(status):
        return by_status.get(status, 0)

    lines = [
        "📊 <b>Статистика</b>",
        "",
        "<b>Объявления</b>",
        f"  ✅ Опубликовано: {n(ListingStatus.APPROVED)}",
        f"  ⏳ Ждут проверки: {n(ListingStatus.PENDING)}",
        f"  🚫 Отсеяно: {n(ListingStatus.REJECTED)}",
        f"  🕒 Устарело: {n(ListingStatus.EXPIRED)}",
    ]
    if flagged:
        lines.append(f"  ⚠️ С пометкой «проверить»: {flagged} → /review")

    lines += ["", "<b>Приток</b>", f"  За 24 часа: {last_24h}", f"  За 7 дней: {last_7d}"]
    if newest:
        age_min = int((now - newest).total_seconds() // 60)
        health = "✅" if age_min < 30 else "⚠️"
        lines.append(f"  {health} Последнее добавление: {age_min} мин назад")
    else:
        lines.append("  ⚠️ Ни одного объявления ещё не добавлено")

    if by_city:
        lines += ["", "<b>По городам</b>"]
        for city, count in by_city:
            lines.append(f"  {bot_ui._label(bot_ui.CITY_LABELS, city, 'ru')}: {count}")

    if by_source:
        lines += ["", "<b>По источникам</b>"]
        for channel, count in by_source:
            lines.append(f"  @{channel or 'вручную'}: {count}")

    lines += ["", "<b>Аудитория</b>", f"  Пользователей: {users}", f"  Подписок: {subs}"]
    return "\n".join(lines)


def _render_spam_report() -> str:
    """Who is currently throttled, so abuse is visible rather than silent."""
    now = datetime.utcnow()
    with SessionLocal() as session:
        blocked = (
            session.query(RateLimitState)
            .filter(RateLimitState.blocked_until.isnot(None), RateLimitState.blocked_until > now)
            .order_by(RateLimitState.blocked_until.desc())
            .limit(20)
            .all()
        )
        repeat = (
            session.query(RateLimitState)
            .filter(RateLimitState.violation_count > 0)
            .order_by(RateLimitState.violation_count.desc())
            .limit(10)
            .all()
        )

    lines = ["🛡 <b>Защита от флуда</b>", ""]
    if blocked:
        lines.append("<b>Сейчас в паузе</b>")
        for s in blocked:
            mins = int((s.blocked_until - now).total_seconds() // 60) + 1
            lines.append(f"  id {s.telegram_id} · {s.action} · ещё {mins} мин")
    else:
        lines.append("Сейчас никто не заблокирован. ✅")

    if repeat:
        lines += ["", "<b>Повторные нарушения</b>"]
        for s in repeat:
            lines.append(f"  id {s.telegram_id} · {s.action} · нарушений: {s.violation_count}")

    lines += [
        "",
        "<i>Блокировки временные и снимаются сами; повторные нарушения "
        "обнуляются через сутки нормального поведения. Постоянных банов нет.</i>",
    ]
    return "\n".join(lines)


def _send_review_queue(chat_id: int, lang: str, flagged_only: bool) -> None:
    """Show listings worth a second look: auto-flagged, or awaiting approval."""
    with SessionLocal() as session:
        query = session.query(Listing)
        if flagged_only:
            query = query.filter(
                Listing.needs_review.is_(True), Listing.status == ListingStatus.APPROVED
            )
        else:
            query = query.filter(Listing.status == ListingStatus.PENDING)
        listing = query.order_by(Listing.created_at.asc()).first()
        remaining = query.count()
        if listing:
            _ = listing.photos

    if not listing:
        telegram_api.send_message(chat_id, "Очередь пуста. 👍")
        return

    note = f"⚠️ {html.escape(listing.quality_note)}\n" if listing.quality_note else ""
    prefix = f"{note}<b>#{listing.id}</b> · осталось: {remaining}"
    _send_listing(chat_id, listing, lang, prefix=prefix)
    telegram_api.send_message(
        chat_id,
        f"Что сделать с #{listing.id}?",
        reply_markup=telegram_api.approve_reject_buttons(listing.id),
    )


def _create_manual_listing(message: dict, content: str) -> None:
    import re

    chat_id = message["chat"]["id"]
    username = message.get("from", {}).get("username")
    url_match = re.search(r"https?://\S+", content)

    extracted = extract(content)
    source_url = url_match.group(0) if url_match else f"tg://user?id={chat_id}"
    fallback_lat, fallback_lng = fallback_coords(extracted.city, f"{source_url}:{message['message_id']}")

    with SessionLocal() as session:
        listing = Listing(
            # User submissions are never auto-published: unlike the curated
            # channels, anyone can send anything here.
            status=ListingStatus.PENDING,
            content_hash=quality.content_hash(content),
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
            f"➕ Новое объявление #{listing_id} от пользователя:\n\n{html.escape(content[:600])}",
            reply_markup=telegram_api.approve_reject_buttons(listing_id),
        )


def _handle_callback(callback_query: dict) -> None:
    sender = callback_query["from"]
    user_id = sender["id"]
    message = callback_query.get("message") or {}
    chat_id = message.get("chat", {}).get("id", user_id)
    query_id = callback_query["id"]
    data = callback_query.get("data", "")
    lang = _user_lang(sender)
    is_admin_user = _is_admin(user_id)

    # --- public actions ---
    if data == "menu:main":
        telegram_api.answer_callback_query(query_id)
        _send_main_menu(chat_id, lang, is_admin_user)
        return

    if data == "help:show":
        telegram_api.answer_callback_query(query_id)
        telegram_api.send_message(chat_id, i18n.t("help_text", lang))
        return

    if data == "submit:how":
        telegram_api.answer_callback_query(query_id)
        telegram_api.send_message(chat_id, i18n.t("submit_prompt", lang))
        return

    if data == "lang:menu":
        telegram_api.answer_callback_query(query_id)
        telegram_api.send_message(chat_id, i18n.t("choose_language", lang),
                                  reply_markup=bot_ui.language_keyboard(lang))
        return

    if data.startswith("lang:"):
        chosen = data.split(":", 1)[1]
        if chosen in i18n.SUPPORTED_LANGS:
            _set_user_lang(user_id, chosen)
            telegram_api.answer_callback_query(query_id, i18n.t("language_set", chosen))
            _send_main_menu(chat_id, chosen, is_admin_user)
        else:
            telegram_api.answer_callback_query(query_id)
        return

    if data.startswith("latest:"):
        offset = data.split(":", 1)[1]
        telegram_api.answer_callback_query(query_id)
        _send_listing_page(chat_id, lang, offset=int(offset) if offset.isdigit() else 0)
        return

    if data == "sub:menu":
        telegram_api.answer_callback_query(query_id)
        _send_subscription_menu(chat_id, user_id, lang)
        return

    if data == "sub:toggle":
        now_active = _toggle_subscription(user_id)
        telegram_api.answer_callback_query(query_id, i18n.t("sub_on" if now_active else "sub_off", lang))
        _send_subscription_menu(chat_id, user_id, lang)
        return

    # --- admin actions ---
    if data == "admin:menu":
        if not is_admin_user:
            telegram_api.answer_callback_query(query_id, i18n.t("admins_only", lang))
            return
        telegram_api.answer_callback_query(query_id)
        telegram_api.send_message(
            chat_id,
            "🛠 <b>Админ-панель</b>\n\n"
            "/stats — статистика\n"
            "/review — объявления с пометкой «проверить»\n"
            "/pending — присланные пользователями, ждут решения\n"
            "/spam — кто сейчас ограничен за флуд",
        )
        return

    if data.startswith(("approve:", "reject:")):
        if not is_admin_user:
            telegram_api.answer_callback_query(query_id, i18n.t("admins_only", lang))
            return

        # callback_data is attacker-controllable in principle, so parse
        # defensively instead of letting a malformed value raise.
        parts = data.split(":")
        if len(parts) != 2 or not parts[1].isdigit():
            telegram_api.answer_callback_query(query_id, "Некорректная команда.")
            return
        action, listing_id = parts[0], int(parts[1])

        with SessionLocal() as session:
            listing = session.get(Listing, listing_id)
            if not listing:
                telegram_api.answer_callback_query(query_id, "Объявление не найдено.")
                return
            if action == "approve":
                listing.status = ListingStatus.APPROVED
                listing.needs_review = False
            else:
                listing.status = ListingStatus.REJECTED
            session.commit()

        suffix = "✅ Опубликовано" if action == "approve" else "❌ Скрыто"
        telegram_api.edit_message_reply_markup(chat_id, message.get("message_id"), None)
        telegram_api.answer_callback_query(query_id, suffix)
        telegram_api.send_message(chat_id, f"#{listing_id}: {suffix}")
        return

    telegram_api.answer_callback_query(query_id)


if __name__ == "__main__":
    app.run(port=5000, debug=True)
