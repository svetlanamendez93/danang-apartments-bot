"""Translations for everything the user sees, in ru / en / vi.

Russian is the primary language, but the audience is mixed: the monitored
channels post in Russian, English and Vietnamese, and an English speaker
opening the bot has to be able to switch without knowing any Russian — so the
language control is always visible rather than buried in settings.

Adding a string means adding all three languages here. `t()` falls back to
Russian for a missing key so a half-finished translation degrades to something
readable instead of showing a raw key.
"""
from __future__ import annotations

DEFAULT_LANG = "ru"
SUPPORTED_LANGS = ("ru", "en", "vi")

LANG_NAMES = {"ru": "Русский", "en": "English", "vi": "Tiếng Việt"}
LANG_FLAGS = {"ru": "🇷🇺", "en": "🇬🇧", "vi": "🇻🇳"}

STRINGS: dict[str, dict[str, str]] = {
    # --- bot: welcome and help ---
    "welcome_title": {
        "ru": "🏠 <b>Аренда жилья во Вьетнаме</b>",
        "en": "🏠 <b>Rental housing in Vietnam</b>",
        "vi": "🏠 <b>Nhà cho thuê tại Việt Nam</b>",
    },
    "welcome_body": {
        "ru": (
            "Свежие объявления из Telegram-каналов — на одной карте, "
            "с фильтрами и ссылкой на оригинал.\n\n"
            "Города: Дананг, Нячанг, Хошимин, Ханой, Хойан.\n"
            "Объявления обновляются автоматически каждые несколько минут."
        ),
        "en": (
            "Fresh listings from Telegram channels on a single map, "
            "with filters and a link to every original post.\n\n"
            "Cities: Da Nang, Nha Trang, Ho Chi Minh City, Hanoi, Hoi An.\n"
            "Listings refresh automatically every few minutes."
        ),
        "vi": (
            "Tin cho thuê mới từ các kênh Telegram trên một bản đồ, "
            "có bộ lọc và liên kết tới bài gốc.\n\n"
            "Thành phố: Đà Nẵng, Nha Trang, TP. Hồ Chí Minh, Hà Nội, Hội An.\n"
            "Tin được cập nhật tự động vài phút một lần."
        ),
    },
    "btn_open_map": {
        "ru": "🗺 Открыть карту объявлений",
        "en": "🗺 Open the listings map",
        "vi": "🗺 Mở bản đồ tin đăng",
    },
    # Deliberately identical in all three languages. Whoever needs this button
    # is by definition looking at a language they may not read — if the label
    # were translated, someone who got the wrong language on first contact
    # would have no word they recognise to aim for.
    "btn_language": {
        "ru": "🌐 Язык · Language · Ngôn ngữ",
        "en": "🌐 Язык · Language · Ngôn ngữ",
        "vi": "🌐 Язык · Language · Ngôn ngữ",
    },
    "btn_help": {"ru": "❓ Помощь", "en": "❓ Help", "vi": "❓ Trợ giúp"},
    "btn_submit": {"ru": "➕ Добавить объявление", "en": "➕ Submit a listing", "vi": "➕ Đăng tin"},
    # Same reasoning as btn_language: the prompt has to be legible to someone
    # who is here precisely because they can't read the current language.
    "choose_language": {
        "ru": "🌐 Выберите язык · Choose your language · Chọn ngôn ngữ",
        "en": "🌐 Выберите язык · Choose your language · Chọn ngôn ngữ",
        "vi": "🌐 Выберите язык · Choose your language · Chọn ngôn ngữ",
    },
    "language_set": {
        "ru": "Готово, язык переключён на русский.",
        "en": "Done, the language is now English.",
        "vi": "Xong, ngôn ngữ đã chuyển sang tiếng Việt.",
    },
    "help_text": {
        "ru": (
            "<b>Что умеет бот</b>\n\n"
            "🗺 <b>Карта</b> — все актуальные объявления с метками по адресам. "
            "Нажмите на метку, чтобы увидеть фото, цену, условия и ссылку на оригинал.\n\n"
            "🔎 <b>Фильтры</b> — бюджет, количество комнат, тип жилья, ремонт, питомцы. "
            "Можно выбирать несколько вариантов сразу.\n\n"
            "➕ <b>/submit</b> — прислать объявление, которого нет в боте "
            "(например, из Facebook). Формат:\n"
            "<code>/submit текст объявления и ссылка на оригинал</code>\n\n"
            "🌐 <b>/language</b> — сменить язык.\n\n"
            "Объявления собираются автоматически из открытых Telegram-каналов. "
            "У каждого всегда есть ссылка на первоисточник — "
            "проверяйте условия и не переводите депозит, не увидев жильё."
        ),
        "en": (
            "<b>What this bot does</b>\n\n"
            "🗺 <b>Map</b> — every current listing pinned by address. Tap a pin for "
            "photos, price, terms and a link to the original post.\n\n"
            "🔎 <b>Filters</b> — budget, number of rooms, property type, condition, pets. "
            "Several options can be selected at once.\n\n"
            "➕ <b>/submit</b> — send a listing the bot doesn't have yet "
            "(from Facebook, for example). Format:\n"
            "<code>/submit listing text and a link to the original</code>\n\n"
            "🌐 <b>/language</b> — change language.\n\n"
            "Listings are collected automatically from public Telegram channels. "
            "Every one keeps a link to its source — check the terms yourself and "
            "never send a deposit before seeing the place."
        ),
        "vi": (
            "<b>Bot này làm gì</b>\n\n"
            "🗺 <b>Bản đồ</b> — mọi tin hiện có được ghim theo địa chỉ. Chạm vào ghim để xem "
            "ảnh, giá, điều kiện và liên kết tới bài gốc.\n\n"
            "🔎 <b>Bộ lọc</b> — ngân sách, số phòng, loại nhà, tình trạng, thú cưng. "
            "Có thể chọn nhiều mục cùng lúc.\n\n"
            "➕ <b>/submit</b> — gửi tin mà bot chưa có (ví dụ từ Facebook). Định dạng:\n"
            "<code>/submit nội dung tin và liên kết bài gốc</code>\n\n"
            "🌐 <b>/language</b> — đổi ngôn ngữ.\n\n"
            "Tin được thu thập tự động từ các kênh Telegram công khai. Mỗi tin luôn kèm "
            "liên kết nguồn — hãy tự kiểm tra điều kiện và đừng chuyển tiền cọc "
            "trước khi xem nhà."
        ),
    },
    # --- bot: submissions ---
    "submit_prompt": {
        "ru": (
            "Пришлите объявление одним сообщением: текст, цена, город "
            "и ссылка на оригинал. Например:\n\n"
            "<code>/submit Квартира в Дананге, 1 спальня, $400/мес "
            "https://facebook.com/...</code>\n\n"
            "Можно приложить фото."
        ),
        "en": (
            "Send the listing in one message: text, price, city and a link to the "
            "original. For example:\n\n"
            "<code>/submit Apartment in Da Nang, 1 bedroom, $400/mo "
            "https://facebook.com/...</code>\n\n"
            "A photo can be attached."
        ),
        "vi": (
            "Gửi tin trong một tin nhắn: nội dung, giá, thành phố và liên kết bài gốc. "
            "Ví dụ:\n\n"
            "<code>/submit Căn hộ ở Đà Nẵng, 1 phòng ngủ, $400/tháng "
            "https://facebook.com/...</code>\n\n"
            "Có thể đính kèm ảnh."
        ),
    },
    "submit_thanks": {
        "ru": "Спасибо! Объявление отправлено на проверку и скоро появится на карте.",
        "en": "Thank you! The listing has been sent for review and will appear on the map shortly.",
        "vi": "Cảm ơn bạn! Tin đã được gửi để kiểm duyệt và sẽ sớm xuất hiện trên bản đồ.",
    },
    "rate_limited": {
        "ru": "Слишком много запросов подряд. Подождите {minutes} мин. и попробуйте снова.",
        "en": "Too many requests in a row. Please wait {minutes} min and try again.",
        "vi": "Quá nhiều yêu cầu liên tiếp. Vui lòng đợi {minutes} phút rồi thử lại.",
    },
    "unknown_command": {
        "ru": "Неизвестная команда. Нажмите кнопку ниже или отправьте /help.",
        "en": "Unknown command. Use the buttons below or send /help.",
        "vi": "Lệnh không hợp lệ. Dùng các nút bên dưới hoặc gửi /help.",
    },
    "admins_only": {
        "ru": "Эта команда только для администраторов.",
        "en": "This command is for administrators only.",
        "vi": "Lệnh này chỉ dành cho quản trị viên.",
    },
    # --- bot: menu buttons ---
    "btn_subscribe": {"ru": "🔔 Подписка", "en": "🔔 Alerts", "vi": "🔔 Nhận thông báo"},
    "btn_latest": {"ru": "🆕 Свежие", "en": "🆕 Latest", "vi": "🆕 Mới nhất"},
    "btn_back": {"ru": "⬅️ Назад", "en": "⬅️ Back", "vi": "⬅️ Quay lại"},
    "btn_more": {"ru": "Показать ещё", "en": "Show more", "vi": "Xem thêm"},
    # --- bot: browsing listings in chat ---
    "latest_intro": {
        "ru": "🆕 <b>Свежие объявления</b>\nПоказываю последние. Полная карта с фильтрами — в мини-приложении.",
        "en": "🆕 <b>Latest listings</b>\nShowing the most recent ones. The full map with filters is in the Mini App.",
        "vi": "🆕 <b>Tin mới nhất</b>\nĐang hiển thị tin gần đây. Bản đồ đầy đủ có bộ lọc nằm trong Mini App.",
    },
    "no_listings": {
        "ru": "Пока нет объявлений. Загляните позже — база обновляется каждые несколько минут.",
        "en": "No listings yet. Check back soon — the database refreshes every few minutes.",
        "vi": "Chưa có tin nào. Hãy quay lại sau — dữ liệu được cập nhật vài phút một lần.",
    },
    "no_more_listings": {
        "ru": "Это все объявления по вашему запросу.",
        "en": "That's every listing matching your request.",
        "vi": "Đó là tất cả tin phù hợp với yêu cầu của bạn.",
    },
    # --- bot: subscriptions ---
    "sub_menu_title": {
        "ru": (
            "🔔 <b>Подписка на новые объявления</b>\n\n"
            "Бот будет присылать новые объявления сразу, как они появятся — "
            "не нужно открывать карту.\n\nВыберите города:"
        ),
        "en": (
            "🔔 <b>Alerts for new listings</b>\n\n"
            "The bot will send new listings the moment they appear — "
            "no need to open the map.\n\nChoose cities:"
        ),
        "vi": (
            "🔔 <b>Thông báo tin mới</b>\n\n"
            "Bot sẽ gửi tin mới ngay khi có — bạn không cần mở bản đồ.\n\n"
            "Chọn thành phố:"
        ),
    },
    "sub_on": {
        "ru": "🔔 Подписка включена. Новые подходящие объявления будут приходить сюда.",
        "en": "🔔 Alerts are on. Matching new listings will arrive here.",
        "vi": "🔔 Đã bật thông báo. Tin mới phù hợp sẽ được gửi vào đây.",
    },
    "sub_off": {
        "ru": "🔕 Подписка отключена.",
        "en": "🔕 Alerts are off.",
        "vi": "🔕 Đã tắt thông báo.",
    },
    "btn_sub_enable": {"ru": "🔔 Включить подписку", "en": "🔔 Turn alerts on", "vi": "🔔 Bật thông báo"},
    "btn_sub_disable": {"ru": "🔕 Отключить подписку", "en": "🔕 Turn alerts off", "vi": "🔕 Tắt thông báo"},
    "btn_all_cities": {"ru": "Все города", "en": "All cities", "vi": "Tất cả thành phố"},
    "sub_new_listing": {
        "ru": "🔔 Новое объявление по вашей подписке",
        "en": "🔔 New listing matching your alert",
        "vi": "🔔 Tin mới khớp với thông báo của bạn",
    },
}


def normalize_lang(raw: str | None) -> str:
    """Map a Telegram language_code ("en-GB", "ru") onto a supported language."""
    if not raw:
        return DEFAULT_LANG
    code = raw.lower().split("-")[0]
    return code if code in SUPPORTED_LANGS else DEFAULT_LANG


def t(key: str, lang: str = DEFAULT_LANG, **kwargs) -> str:
    entry = STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(lang) or entry.get(DEFAULT_LANG, key)
    return text.format(**kwargs) if kwargs else text
