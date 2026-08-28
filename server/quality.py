"""Automatic quality gates applied to scraped posts.

Listings from curated channels publish immediately — a rental held back for
review is often gone by the time anyone looks at it. That removes the human
who used to catch junk, so these checks stand in for the parts of that job a
machine can do reliably:

  - not every post in a rental channel is an offer: many are people *looking*
    for a flat, or agency adverts with no property in them
  - the same flat is routinely posted to several channels and reposted weeks
    later, which would otherwise fill the map with duplicates of one apartment
  - a price far below the market for a whole city is the classic bait used in
    deposit scams

Anything rejected here is kept in the database with status REJECTED and a
reason, so it can be reviewed rather than silently lost.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from db.models import City

# Posts from people searching for housing rather than offering it. These read
# very much like real listings (city, budget, rooms) so the extractor happily
# parses them — only the wording gives them away.
_WANTED_PATTERNS = [
    r"\bищу\b", r"\bищем\b", r"ищет\s+(?:квартир|жиль|дом|студи)",
    r"\bsnimu\b", r"сниму\b", r"снимем\b",
    r"\bwanted\b", r"\blooking\s+for\s+(?:a\s+)?(?:flat|apartment|house|room|place)",
    r"\bwtb\b", r"need\s+(?:a\s+)?(?:flat|apartment|room)\s+(?:for|in)",
    r"помогите\s+найти", r"подскажите\s+квартир",
]

# Recruitment, services, and other channel noise that isn't a property at all.
_NOT_A_LISTING_PATTERns = [
    r"\bвакансия\b", r"\bрезюме\b", r"требуется\s+сотрудник",
    r"курс[ыа]?\s+(?:английск|вьетнамск)", r"визаран", r"visa\s*run",
    r"продам\s+(?:байк|скутер|мотоцикл|машину|авто)",
]

# Below these, a whole-flat monthly rent is not credible for the city and is a
# common deposit-scam hook ("$80/month beachfront"). Deliberately generous —
# the aim is to catch bait, not to second-guess a genuinely cheap room.
_MIN_CREDIBLE_USD: dict[City, float] = {
    City.DA_NANG: 100,
    City.NHA_TRANG: 90,
    City.HO_CHI_MINH: 110,
    City.HANOI: 100,
    City.HOI_AN: 90,
}


@dataclass
class QualityVerdict:
    publish: bool
    reason: str | None = None      # why it was held back, for the admin
    needs_review: bool = False     # publish, but flag it as worth a look


def _normalize(text: str) -> str:
    """Strip formatting noise so reposts of one flat hash identically."""
    lowered = text.lower()
    # Drop emoji, punctuation, contact handles and links, which are exactly the
    # parts a reposter tends to change while the property stays the same.
    lowered = re.sub(r"https?://\S+", " ", lowered)
    lowered = re.sub(r"[@#]\w+", " ", lowered)
    lowered = re.sub(r"[^\w\s]", " ", lowered, flags=re.UNICODE)
    lowered = re.sub(r"\d{6,}", " ", lowered)  # phone numbers
    return re.sub(r"\s+", " ", lowered).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode()).hexdigest()


def looks_like_wanted_ad(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in _WANTED_PATTERNS)


def looks_like_non_listing(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in _NOT_A_LISTING_PATTERns)


def assess(text: str, city: City, price_min_usd: float | None, rooms: str | None) -> QualityVerdict:
    """Decide whether a scraped post should go live as-is."""
    if not text or len(text.strip()) < 60:
        return QualityVerdict(False, "слишком короткий текст — вряд ли объявление")

    if looks_like_wanted_ad(text):
        return QualityVerdict(False, "похоже на «ищу жильё», а не на предложение")

    if looks_like_non_listing(text):
        return QualityVerdict(False, "не похоже на объявление о жилье")

    # With neither a price nor a room count there is nothing to filter or sort
    # by, and the map entry would be useless to a searcher.
    if price_min_usd is None and rooms is None:
        return QualityVerdict(False, "не удалось определить ни цену, ни количество комнат")

    if price_min_usd is not None:
        floor = _MIN_CREDIBLE_USD.get(city)
        if floor and price_min_usd < floor:
            # Published anyway — it may be a genuinely cheap single room — but
            # flagged, because this is what bait looks like.
            return QualityVerdict(
                True,
                f"подозрительно низкая цена (${price_min_usd:.0f}) — возможен развод",
                needs_review=True,
            )

    if price_min_usd is None:
        return QualityVerdict(True, "цена не определена автоматически", needs_review=True)

    return QualityVerdict(True)
