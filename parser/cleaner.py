"""Turn a raw channel post into something readable in a listing card.

Channels post through templates that look fine in their own feed but read
badly in a card that already shows the key facts as structured fields. A
typical post arrives as:

    Brand-New 2BR apartment in Khue My
    0️⃣
     Type: apartment
    2️⃣
     Rooms: 2 bedrooms
    3️⃣
     Square meters: 70 m2
    ...
    5️⃣
     Distance to the sea
    ...
    🔟
    Contact +84384575439

The keycap bullets each land on their own line (the scraper joins every HTML
element with a newline), labels get separated from their values, several
fields are empty, and type/rooms/area/price repeat what the card already
displays above. The result is a wall of text nobody reads.

So this drops the noise and the already-displayed fields, keeping the parts a
reader actually needs from the body: the headline, the terms (deposit, minimum
period, availability) and how to make contact.

The original is never lost — Listing.raw_text keeps it verbatim, and every
card links to the source post.
"""
from __future__ import annotations

import re

# Keycap digits (0️⃣–9️⃣, 🔟) used as bullets, with their variation selectors.
_KEYCAP = re.compile(r"[0-9#*]️?⃣|\U0001F51F")

# Fields the card already renders as price, location and the fact badges.
# Repeating them in the body is what makes these posts feel endless.
_REDUNDANT_FIELD = re.compile(
    r"^\s*(?:"
    r"type|тип|loại"
    r"|rooms?|комнат\w*|phòng"
    r"|bedrooms?|спальн\w*"
    r"|square\s*meters?|площадь|diện\s*tích"
    r"|price(?:\s+for\s+\d+\s+months?)?|стоимость|цена|giá"
    r"|district|район|quận"
    r"|distance\s+to\s+the\s+sea|до\s+моря|расстояние\s+до\s+моря"
    r"|address|адрес|địa\s*chỉ"
    r")\s*[:：]?\s*.{0,60}$",
    re.IGNORECASE,
)

# A label with no value after it.
_EMPTY_LABEL = re.compile(r"^[^\w]*[\w\s/()]{1,40}\s*[:：]\s*$")

# Decorative-only lines: emoji, punctuation, separators.
_DECORATIVE = re.compile(r"^[\W_]+$", re.UNICODE)

# Trailers a channel repeats on every post. The optional bullet matters: these
# usually arrive as list items ("- Zalo group: https://…").
_PROMO = re.compile(
    r"^\s*[-–•]?\s*(?:website|веб-?сайт|video\s*tour|видео\s*тур|отзывы|подписывайтесь|"
    r"наш\s+канал|подробности\s+и\s+запись|фото\s+и\s+видео|zalo\s+group|"
    r"whatsapp\s+group|telegram\s*[:：])\b",
    re.IGNORECASE,
)

# A line that is nothing but hashtags containing digits: the channel's internal
# reference numbers and measurements (#125, #600m, #1BR). The facts they encode
# are already shown as badges, and place-name tags (#SonTra) keep no digits, so
# they survive.
_NUMERIC_TAGS_ONLY = re.compile(r"^(?:\s*#\w*\d\w*\s*)+$")

# Bare links on their own line — the card already carries a source button, and
# group-invite links are channel promotion rather than listing detail.
_BARE_LINK = re.compile(r"^\s*[-–•]?\s*https?://\S+\s*$")


def _looks_like_label(line: str) -> bool:
    return line.rstrip().endswith((":", "：")) and len(line) < 48


def clean_post_text(text: str) -> str:
    """Collapse a templated post into the part worth reading.

    Conservative about anything it doesn't recognise: unknown lines are kept,
    because the body is often the only place a detail like "deposit 1 month,
    pay 1 month" appears.
    """
    if not text:
        return ""

    raw_lines = text.split("\n")

    # Strip keycap bullets. A line that held nothing else disappears entirely
    # rather than becoming a blank line — otherwise removing the bullets just
    # trades digits for empty space and the post stays as long as before.
    stripped: list[str] = []
    for line in raw_lines:
        without_keycap = _KEYCAP.sub("", line).strip()
        if without_keycap or not _KEYCAP.search(line):
            stripped.append(without_keycap)

    # Rejoin "Label:" with the value the HTML-to-text conversion split onto the
    # next line, so each field can be judged as one unit.
    merged: list[str] = []
    for line in stripped:
        if merged and _looks_like_label(merged[-1]) and line and not _looks_like_label(line):
            merged[-1] = f"{merged[-1]} {line}"
        else:
            merged.append(line)

    kept: list[str] = []
    for i, line in enumerate(merged):
        if not line:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        # The first line is the headline; never treat it as a redundant field.
        if i > 0 and _REDUNDANT_FIELD.match(line):
            continue
        if _EMPTY_LABEL.match(line) or _DECORATIVE.match(line) or _PROMO.match(line):
            continue
        if _BARE_LINK.match(line) or _NUMERIC_TAGS_ONLY.match(line):
            continue
        kept.append(line)

    while kept and kept[-1] == "":
        kept.pop()
    while kept and kept[0] == "":
        kept.pop(0)

    # Collapse the blank lines left behind by removed fields.
    out: list[str] = []
    for line in kept:
        if line == "" and out and out[-1] == "":
            continue
        out.append(line)

    return "\n".join(out)
