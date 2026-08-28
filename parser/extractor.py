"""Best-effort regex extraction of structured fields from a free-text listing post.

Posts are written by humans in Russian/English/Vietnamese, often inconsistently,
so this is intentionally a *draft* extractor: every field it fills in is a guess
that a moderator can fix before the listing is published. Never trust its output
as final without the moderation step in bot/moderation.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from db.models import City, PetsPolicy, PropertyType, RenovationQuality

_CITY_PATTERNS: dict[City, list[str]] = {
    City.DA_NANG: [r"da\s*nang", r"дананг", r"đà\s*nẵng"],
    City.NHA_TRANG: [r"nha\s*trang", r"нячанг"],
    City.HO_CHI_MINH: [r"ho\s*chi\s*minh", r"хошимин", r"saigon", r"сайгон", r"hcmc"],
    City.HANOI: [r"hanoi", r"ханой", r"hà\s*nội"],
    City.HOI_AN: [r"hoi\s*an", r"хойан", r"хой\s*ан", r"hội\s*an"],
}

_PROPERTY_TYPE_PATTERNS: dict[PropertyType, list[str]] = {
    PropertyType.VILLA: [r"villa", r"вилла"],
    PropertyType.HOUSE: [r"\bhouse\b", r"\bдом\b", r"nhà\s*nguyên\s*căn"],
    PropertyType.ROOM: [r"\broom\b", r"комнат[а-я]*\s+(?:в\s+аренду|сдам|сдаётся)", r"phòng\b"],
    PropertyType.APARTMENT: [r"apartment", r"квартир", r"studio", r"студи", r"căn\s*hộ"],
}

_RENOVATION_PATTERNS: dict[RenovationQuality, list[str]] = {
    RenovationQuality.PREMIUM: [r"premium", r"люкс", r"элитн", r"новостройк"],
    RenovationQuality.GOOD: [r"хорош[а-я]* ремонт", r"good\s+condition", r"свежий\s+ремонт"],
    RenovationQuality.NEEDS_REPAIR: [r"требует\s+ремонта", r"needs?\s+repair", r"без\s+ремонта"],
}

_PETS_ALLOWED_PATTERNS = [r"pets?\s+(?:are\s+)?(?:ok|okay|allowed|welcome)", r"с\s+животными", r"можно\s+с\s+питомц"]
_PETS_NOT_ALLOWED_PATTERNS = [r"no\s+pets", r"без\s+животных", r"нельзя\s+с\s+питомц", r"питомцы\s+запрещ"]

_STUDIO_PATTERNS = [r"\bstudio\b", r"студи"]
_ROOMS_PATTERN = re.compile(r"(\d)\s*(?:br\b|bed\s*room|спальн|комнат)", re.IGNORECASE)

# $400, 400$, 400 usd, 400usd/month
_PRICE_USD_RANGE_PATTERN = re.compile(
    r"\$?\s*(\d{2,4}(?:[.,]\d{3})?)\s*(?:-|–|—|до)\s*\$?\s*(\d{2,4}(?:[.,]\d{3})?)\s*\$?\s*(?:usd)?",
    re.IGNORECASE,
)
_PRICE_USD_SINGLE_PATTERN = re.compile(
    r"(?:\$\s*(\d{2,4}(?:[.,]\d{3})?)|(\d{2,4}(?:[.,]\d{3})?)\s*(?:\$|usd\b))",
    re.IGNORECASE,
)


@dataclass
class ExtractedListing:
    city: City = City.OTHER
    property_type: PropertyType | None = None
    renovation_quality: RenovationQuality | None = None
    pets_policy: PetsPolicy = PetsPolicy.UNKNOWN
    rooms: str | None = None
    price_min_usd: float | None = None
    price_max_usd: float | None = None
    address_text: str | None = None
    confidence_notes: list[str] = field(default_factory=list)


def _match_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _parse_number(raw: str) -> float:
    return float(raw.replace(",", "").replace(".", "") if len(raw.split(".")[-1]) == 3 else raw.replace(",", ""))


def extract(text: str) -> ExtractedListing:
    result = ExtractedListing()
    if not text:
        result.confidence_notes.append("empty text, nothing extracted")
        return result

    for city, patterns in _CITY_PATTERNS.items():
        if _match_any(text, patterns):
            result.city = city
            break
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

    if _match_any(text, _STUDIO_PATTERNS):
        result.rooms = "studio"
    else:
        rooms_match = _ROOMS_PATTERN.search(text)
        if rooms_match:
            result.rooms = rooms_match.group(1)

    range_match = _PRICE_USD_RANGE_PATTERN.search(text)
    if range_match:
        result.price_min_usd = _parse_number(range_match.group(1))
        result.price_max_usd = _parse_number(range_match.group(2))
    else:
        single_match = _PRICE_USD_SINGLE_PATTERN.search(text)
        if single_match:
            raw = single_match.group(1) or single_match.group(2)
            price = _parse_number(raw)
            result.price_min_usd = price
            result.price_max_usd = price
        else:
            result.confidence_notes.append("price not detected — needs manual entry")

    return result
