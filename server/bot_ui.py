"""Rendering of listings and menus for the Telegram side of the bot.

Kept apart from server/app.py so the formatting of a listing card lives in one
place: the same card is used by the moderation queue, by subscription pushes
and by an on-demand /listings request, and they must not drift apart.
"""
from __future__ import annotations

import html

from db.models import City, Listing, PetsPolicy, PropertyType, RenovationQuality
from server.i18n import LANG_FLAGS, LANG_NAMES, SUPPORTED_LANGS, t

# Telegram limits: 1024 chars for a media caption, 4096 for a plain message,
# and 10 items in one media group.
CAPTION_LIMIT = 1024
MEDIA_GROUP_LIMIT = 10

CITY_LABELS: dict[City, dict[str, str]] = {
    City.DA_NANG: {"ru": "Дананг", "en": "Da Nang", "vi": "Đà Nẵng"},
    City.NHA_TRANG: {"ru": "Нячанг", "en": "Nha Trang", "vi": "Nha Trang"},
    City.HO_CHI_MINH: {"ru": "Хошимин", "en": "Ho Chi Minh City", "vi": "TP. Hồ Chí Minh"},
    City.HANOI: {"ru": "Ханой", "en": "Hanoi", "vi": "Hà Nội"},
    City.HOI_AN: {"ru": "Хойан", "en": "Hoi An", "vi": "Hội An"},
    City.OTHER: {"ru": "город не указан", "en": "city not specified", "vi": "chưa rõ thành phố"},
}

PROPERTY_LABELS: dict[PropertyType, dict[str, str]] = {
    PropertyType.APARTMENT: {"ru": "Квартира", "en": "Apartment", "vi": "Căn hộ"},
    PropertyType.ROOM: {"ru": "Комната", "en": "Room", "vi": "Phòng"},
    PropertyType.HOUSE: {"ru": "Дом", "en": "House", "vi": "Nhà"},
    PropertyType.VILLA: {"ru": "Вилла", "en": "Villa", "vi": "Biệt thự"},
}

RENOVATION_LABELS: dict[RenovationQuality, dict[str, str]] = {
    RenovationQuality.NEEDS_REPAIR: {"ru": "Требует ремонта", "en": "Needs repair", "vi": "Cần sửa chữa"},
    RenovationQuality.STANDARD: {"ru": "Обычный ремонт", "en": "Standard", "vi": "Tiêu chuẩn"},
    RenovationQuality.GOOD: {"ru": "Хороший ремонт", "en": "Good condition", "vi": "Tình trạng tốt"},
    RenovationQuality.PREMIUM: {"ru": "Премиум", "en": "Premium", "vi": "Cao cấp"},
}

PETS_LABELS: dict[PetsPolicy, dict[str, str]] = {
    PetsPolicy.ALLOWED: {"ru": "🐾 Можно с питомцами", "en": "🐾 Pets allowed", "vi": "🐾 Cho nuôi thú cưng"},
    PetsPolicy.NOT_ALLOWED: {"ru": "🚫 Без питомцев", "en": "🚫 No pets", "vi": "🚫 Không nuôi thú cưng"},
    PetsPolicy.UNKNOWN: {"ru": "", "en": "", "vi": ""},
}

ROOM_LABELS: dict[str, dict[str, str]] = {
    "studio": {"ru": "Студия", "en": "Studio", "vi": "Studio"},
    "1": {"ru": "1 комната", "en": "1 bedroom", "vi": "1 phòng ngủ"},
    "2": {"ru": "2 комнаты", "en": "2 bedrooms", "vi": "2 phòng ngủ"},
    "3": {"ru": "3 комнаты", "en": "3 bedrooms", "vi": "3 phòng ngủ"},
    "4": {"ru": "4+ комнаты", "en": "4+ bedrooms", "vi": "4+ phòng ngủ"},
}

SOURCE_LABELS = {
    "telegram": {"ru": "Telegram", "en": "Telegram", "vi": "Telegram"},
    "facebook": {"ru": "Facebook", "en": "Facebook", "vi": "Facebook"},
    "manual": {"ru": "от пользователя", "en": "user submitted", "vi": "người dùng gửi"},
}

_MISC = {
    "per_month": {"ru": "/мес", "en": "/mo", "vi": "/tháng"},
    "price_unknown": {"ru": "Цена не указана", "en": "Price not stated", "vi": "Chưa có giá"},
    "open_original": {"ru": "↗️ Открыть оригинал", "en": "↗️ Open the original", "vi": "↗️ Mở bài gốc"},
    "photos_count": {"ru": "фото: {n}", "en": "{n} photos", "vi": "{n} ảnh"},
    "source": {"ru": "Источник", "en": "Source", "vi": "Nguồn"},
}


def _label(table: dict, key, lang: str, default: str = "") -> str:
    entry = table.get(key)
    if not entry:
        return default
    return entry.get(lang) or entry.get("ru") or default


def misc(key: str, lang: str, **kwargs) -> str:
    text = _MISC[key].get(lang) or _MISC[key]["ru"]
    return text.format(**kwargs) if kwargs else text


def price_line(listing: Listing, lang: str) -> str:
    if listing.price_min_usd is None:
        return misc("price_unknown", lang)
    suffix = misc("per_month", lang)
    if listing.price_max_usd and listing.price_max_usd != listing.price_min_usd:
        return f"💵 <b>${listing.price_min_usd:,.0f}–${listing.price_max_usd:,.0f}</b>{suffix}"
    return f"💵 <b>${listing.price_min_usd:,.0f}</b>{suffix}"


def render_card(listing: Listing, lang: str, *, body_chars: int) -> str:
    """One listing as a Telegram HTML message.

    `body_chars` differs by context: a photo caption may hold ~1024 characters
    in total, a standalone message ~4096, and the original post text is often
    longer than either.
    """
    facts = []
    if listing.rooms:
        facts.append(_label(ROOM_LABELS, listing.rooms, lang, listing.rooms))
    if listing.property_type:
        facts.append(_label(PROPERTY_LABELS, listing.property_type, lang))
    if listing.area_sqm:
        facts.append(f"{listing.area_sqm:.0f} m²")
    if listing.renovation_quality:
        facts.append(_label(RENOVATION_LABELS, listing.renovation_quality, lang))
    pets = _label(PETS_LABELS, listing.pets_policy, lang)
    if pets:
        facts.append(pets)

    header = [price_line(listing, lang)]
    header.append(f"📍 {html.escape(_label(CITY_LABELS, listing.city, lang))}")
    if listing.address_text:
        header[-1] += f", {html.escape(listing.address_text)}"
    if facts:
        header.append(" · ".join(html.escape(f) for f in facts))

    head = "\n".join(header)
    source_label = _label(SOURCE_LABELS, listing.source_type.value, lang, listing.source_type.value)
    tail = (
        f'\n\n<a href="{html.escape(listing.source_url, quote=True)}">'
        f'{misc("open_original", lang)}</a> · {html.escape(source_label)}'
    )

    room_for_body = body_chars - len(head) - len(tail) - 8
    body = ""
    if room_for_body > 80 and listing.description:
        snippet = listing.description.strip()
        if len(snippet) > room_for_body:
            snippet = snippet[: room_for_body - 1].rstrip() + "…"
        body = "\n\n" + html.escape(snippet)

    return head + body + tail


def language_keyboard(current: str) -> dict:
    """One row per language; the active one is marked so the state is visible."""
    rows = []
    for code in SUPPORTED_LANGS:
        mark = " ✓" if code == current else ""
        rows.append([{
            "text": f"{LANG_FLAGS[code]} {LANG_NAMES[code]}{mark}",
            "callback_data": f"lang:{code}",
        }])
    return {"inline_keyboard": rows}


def main_menu(lang: str, webapp_url: str, *, is_admin: bool = False) -> dict:
    """Inline menu shown with /start.

    The language button is on the first screen on purpose: someone who does not
    read Russian has to be able to find it without understanding anything else.
    """
    rows = [
        [{"text": t("btn_open_map", lang), "web_app": {"url": webapp_url}}],
        [
            {"text": t("btn_subscribe", lang), "callback_data": "sub:menu"},
            {"text": t("btn_latest", lang), "callback_data": "latest:0"},
        ],
        [
            {"text": t("btn_submit", lang), "callback_data": "submit:how"},
            {"text": t("btn_help", lang), "callback_data": "help:show"},
        ],
        [{"text": t("btn_language", lang), "callback_data": "lang:menu"}],
    ]
    if is_admin:
        rows.append([{"text": "🛠 Admin", "callback_data": "admin:menu"}])
    return {"inline_keyboard": rows}


def listing_buttons(listing: Listing, lang: str) -> dict:
    return {"inline_keyboard": [[
        {"text": misc("open_original", lang), "url": listing.source_url},
    ]]}


def persistent_keyboard(lang: str, webapp_url: str) -> dict:
    """The always-visible keyboard under the message box.

    An inline menu scrolls away with the conversation, so after a few listings
    there is nothing to press and the bot feels like it has no navigation. A
    reply keyboard stays put, so the main actions are always one tap away.
    """
    return {
        "keyboard": [
            [{"text": t("btn_open_map", lang), "web_app": {"url": webapp_url}}],
            [{"text": t("btn_latest", lang)}, {"text": t("btn_subscribe", lang)}],
            [{"text": t("btn_submit", lang)}, {"text": t("btn_help", lang)},
             {"text": t("btn_language", lang)}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def _all_translations(key: str) -> set[str]:
    return {v for v in STRINGS_FOR_BUTTONS.get(key, {}).values() if v}


# Reply-keyboard buttons arrive as ordinary text, so the handler has to
# recognise a label in *any* language: a user can switch language while an old
# keyboard is still on screen, and Telegram will keep sending the old labels.
from server.i18n import STRINGS as STRINGS_FOR_BUTTONS  # noqa: E402

def _build_button_actions() -> dict[str, str]:
    # Built in a function rather than a module-level loop: a bare `for _label
    # in …` at module scope leaks the loop variable and shadowed the _label()
    # helper above, turning it into a string for every later caller.
    actions: dict[str, str] = {}
    for key, action in [
        ("btn_latest", "latest"),
        ("btn_subscribe", "subscribe"),
        ("btn_submit", "submit"),
        ("btn_help", "help"),
        ("btn_language", "language"),
    ]:
        for text in _all_translations(key):
            actions[text] = action
    return actions


BUTTON_ACTIONS: dict[str, str] = _build_button_actions()


def action_for_button(text: str) -> str | None:
    return BUTTON_ACTIONS.get(text.strip())
