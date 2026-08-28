"""Best-effort extraction of structured fields from a free-text listing post.

Posts are written by humans in Russian/English/Vietnamese with no common
template, so this is deliberately a *draft*: every field it fills in is a guess
a moderator can correct before the listing is published. Nothing here should be
trusted as final without the moderation step in server/app.py.

The patterns are shaped by the formats actually seen in the monitored channels,
e.g.:
    Price for 1 month: 37 million VND/month (1,400 USD/month)
    Price for 1 month: 540 USD/month (14 million VND/month)
    Стоимость: 22 млн VND / месяц
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from db.models import City, PetsPolicy, PropertyType, RenovationQuality

# Rough VND->USD rate, only used for posts that quote no USD figure at all.
# It does not need to be exact: the number is a search/filter aid that a
# moderator sees and can overwrite, not a quoted price.
VND_PER_USD = 26_000

# Figures outside this band are almost certainly not rents — they are phone
# numbers, areas, distances or years that happened to sit next to a currency
# word. Deliberately wider than any believable rent: judging whether a price is
# *credible* belongs to server/quality.py, which flags scam bait. If this floor
# were set at a realistic rent, a $45 "beachfront villa" would be dropped here
# as unparseable and the scam check downstream would never see it.
MIN_PLAUSIBLE_USD = 20
MAX_PLAUSIBLE_USD = 20_000

# A number that may carry thousands separators: 1,400 / 1.400 / 1 400 / 540.
_NUM = r"\d{1,3}(?:[.,  ]\d{3})+|\d+"

_CITY_PATTERNS: dict[City, list[str]] = {
    City.DA_NANG: [r"da\s*nang", r"дананг", r"đà\s*nẵng", r"danang"],
    City.NHA_TRANG: [r"nha\s*trang", r"нячанг"],
    City.HO_CHI_MINH: [r"ho\s*chi\s*minh", r"хошимин", r"saigon", r"сайгон", r"hcmc"],
    City.HANOI: [r"hanoi", r"ханой", r"hà\s*nội"],
    City.HOI_AN: [r"hoi\s*an", r"хойан", r"хой\s*ан", r"hội\s*an"],
}

_PROPERTY_TYPE_PATTERNS: dict[PropertyType, list[str]] = {
    PropertyType.VILLA: [r"villa", r"вилл[ауы]"],
    PropertyType.HOUSE: [r"\bhouse\b", r"\bдом\b", r"nhà\s*nguyên\s*căn", r"townhouse"],
    PropertyType.ROOM: [r"\broom\b", r"комнат[а-я]*\s+(?:в\s+аренду|сдам|сдаётся)", r"\bphòng\b"],
    PropertyType.APARTMENT: [
        r"apartment", r"квартир", r"studio", r"студи", r"căn\s*hộ", r"апартамент", r"\bflat\b", r"\bcondo\b",
    ],
}

_RENOVATION_PATTERNS: dict[RenovationQuality, list[str]] = {
    RenovationQuality.PREMIUM: [r"premium", r"люкс", r"элитн", r"роскошн", r"luxury"],
    RenovationQuality.GOOD: [
        r"хорош[а-я]*\s+ремонт", r"good\s+condition", r"свежий\s+ремонт",
        r"brand[\s-]*new", r"новые\s+апартамент", r"новостройк", r"fully\s+renovated", r"новый\s+ремонт",
    ],
    RenovationQuality.NEEDS_REPAIR: [r"требует\s+ремонта", r"needs?\s+repair", r"без\s+ремонта"],
}

_PETS_ALLOWED_PATTERNS = [
    r"pets?\s+(?:are\s+)?(?:ok|okay|allowed|welcome)", r"с\s+животными\s+можно",
    r"можно\s+с\s+(?:питомц|животн)", r"pet[\s-]*friendly", r"животные\s+разреш",
]
_PETS_NOT_ALLOWED_PATTERNS = [
    r"no\s+pets", r"без\s+животных", r"нельзя\s+с\s+(?:питомц|животн)",
    r"питомцы\s+запрещ", r"животные\s+запрещ", r"pets?\s+(?:are\s+)?not\s+allowed",
]

_STUDIO_PATTERNS = [r"\bstudio\b", r"студи"]
# "4 bedrooms", "1 спальня", "#3BR", "2BR", "Rooms: 2"
_ROOMS_PATTERN = re.compile(
    r"(\d)\s*(?:br\b|bhk\b|bed\s*room|bedroom|спальн|комнатн)", re.IGNORECASE
)
_ROOMS_LABELLED_PATTERN = re.compile(r"(?:rooms?|комнат[а-я]*)\s*[:\-]\s*(\d)", re.IGNORECASE)

_AREA_PATTERN = re.compile(
    r"(\d{1,4})\s*(?:m2|m²|м2|м²|sqm|кв\.?\s*м)", re.IGNORECASE
)

# USD written either way round: "540 USD", "$540", "1,400 USD/month".
_USD_PATTERNS = [
    re.compile(rf"({_NUM})\s*(?:usd|\$|долл|dollars?)", re.IGNORECASE),
    re.compile(rf"(?:usd|\$)\s*({_NUM})", re.IGNORECASE),
]
# VND in millions: "37 million VND", "22 млн VND", "21 triệu".
_VND_MILLIONS_PATTERNS = [
    re.compile(rf"({_NUM})\s*(?:million|млн|мил|triệu|tr\b)", re.IGNORECASE),
]
# Plain VND amounts: "21,000,000 VND", "15000000 đ".
_VND_PLAIN_PATTERNS = [
    re.compile(rf"({_NUM})\s*(?:vnd|vnđ|₫|đồng|донг)", re.IGNORECASE),
]


@dataclass
class ExtractedListing:
    city: City = City.OTHER
    property_type: PropertyType | None = None
    renovation_quality: RenovationQuality | None = None
    pets_policy: PetsPolicy = PetsPolicy.UNKNOWN
    rooms: str | None = None
    price_min_usd: float | None = None
    price_max_usd: float | None = None
    area_sqm: float | None = None
    confidence_notes: list[str] = field(default_factory=list)


def _match_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _to_number(raw: str) -> float | None:
    """Parse a figure that may use , . space or nbsp as a thousands separator."""
    cleaned = re.sub(r"[.,  ]", "", raw)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _plausible(usd: float | None) -> float | None:
    if usd is None:
        return None
    return usd if MIN_PLAUSIBLE_USD <= usd <= MAX_PLAUSIBLE_USD else None


def _collect_usd_amounts(text: str) -> list[float]:
    """All monthly-rent-shaped USD figures in the post, in reading order."""
    found: list[tuple[int, float]] = []
    for pattern in _USD_PATTERNS:
        for m in pattern.finditer(text):
            # Skip figures inside hashtags (#700m) — they are tags, not prices.
            if m.start() > 0 and text[m.start() - 1] == "#":
                continue
            usd = _plausible(_to_number(m.group(1)))
            if usd is not None:
                found.append((m.start(), usd))
    return [usd for _, usd in sorted(set(found))]


def _collect_vnd_as_usd(text: str) -> list[float]:
    found: list[tuple[int, float]] = []
    for pattern in _VND_MILLIONS_PATTERNS:
        for m in pattern.finditer(text):
            millions = _to_number(m.group(1))
            if millions is None or millions > 999:  # "22 млн" yes, "22000 млн" no
                continue
            usd = _plausible(millions * 1_000_000 / VND_PER_USD)
            if usd is not None:
                found.append((m.start(), _round_converted(usd)))
    for pattern in _VND_PLAIN_PATTERNS:
        for m in pattern.finditer(text):
            dong = _to_number(m.group(1))
            if dong is None or dong < 1_000_000:  # below a million it's not a rent
                continue
            usd = _plausible(dong / VND_PER_USD)
            if usd is not None:
                found.append((m.start(), _round_converted(usd)))
    return [usd for _, usd in sorted(set(found))]


def _round_converted(usd: float) -> float:
    """Round a converted figure to the nearest $10.

    The exchange rate is approximate, so printing "$846.15" would imply a
    precision the number does not have.
    """
    return round(usd / 10) * 10.0


def extract(text: str, default_city: City | None = None) -> ExtractedListing:
    """Pull structured fields out of a post.

    `default_city` is the city a source channel is known to cover; it fills in
    when the post itself names only a district ("Brand-New 1BR in Khue My"),
    which is common because a channel's readers already know the city.
    """
    result = ExtractedListing()
    if not text:
        result.confidence_notes.append("empty text, nothing extracted")
        return result

    for city, patterns in _CITY_PATTERNS.items():
        if _match_any(text, patterns):
            result.city = city
            break
    else:
        if default_city:
            result.city = default_city
            result.confidence_notes.append(f"city not named in post, assumed {default_city.value} from source")
        else:
            result.confidence_notes.append("city not detected, defaulted to OTHER")

    for ptype, patterns in _PROPERTY_TYPE_PATTERNS.items():
        if _match_any(text, patterns):
            result.property_type = ptype
            break

    for quality, patterns in _RENOVATION_PATTERNS.items():
        if _match_any(text, patterns):
            result.renovation_quality = quality
            break

    if _match_any(text, _PETS_NOT_ALLOWED_PATTERNS):
        result.pets_policy = PetsPolicy.NOT_ALLOWED
    elif _match_any(text, _PETS_ALLOWED_PATTERNS):
        result.pets_policy = PetsPolicy.ALLOWED

    rooms_match = _ROOMS_PATTERN.search(text) or _ROOMS_LABELLED_PATTERN.search(text)
    if rooms_match:
        result.rooms = rooms_match.group(1)
    elif _match_any(text, _STUDIO_PATTERNS):
        # Checked after the digit patterns so "1BR studio apartment" reads as 1.
        result.rooms = "studio"

    area_match = _AREA_PATTERN.search(text)
    if area_match:
        result.area_sqm = float(area_match.group(1))

    # Prefer USD figures; fall back to converting a VND-only price. Posts that
    # quote both ("37 million VND/month (1,400 USD/month)") should use the USD
    # one rather than a converted approximation.
    amounts = _collect_usd_amounts(text)
    if not amounts:
        amounts = _collect_vnd_as_usd(text)
        if amounts:
            result.confidence_notes.append("price converted from VND, verify before publishing")

    if amounts:
        result.price_min_usd = min(amounts)
        result.price_max_usd = max(amounts)
    else:
        result.confidence_notes.append("price not detected — needs manual entry")

    return result
