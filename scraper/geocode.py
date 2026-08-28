"""Turn the addresses written in listings into real coordinates.

The hand-written gazetteer in parser/places.py only knows the districts and
complexes someone thought to add. Posts name real streets far beyond that list
— "Truong Dang Que", "Le Hy Cat", "25 Phan Chu Trinh", "Khue My Dong 7" — and
every address it does not recognise ends up stacked on one fallback point,
which is exactly what makes a map with a thousand listings useless.

So addresses are geocoded properly, through OpenStreetMap's Nominatim:

- It runs in the scraper, not the server: Nominatim asks for at most one
  request per second, and PythonAnywhere's free tier cannot reach it at all.
- Every result is cached server-side by query string, so a street is resolved
  once no matter how many listings mention it, and failures are remembered too
  rather than retried forever.
- Results are checked against the city's bounding box before being accepted:
  Nominatim will happily return a same-named street on the other side of the
  country, and a confidently wrong pin is worse than an honest approximate one.

Usage policy: https://operations.osmfoundation.org/policies/nominatim/
"""
from __future__ import annotations

import re
import time

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# The policy requires a real identifying User-Agent.
HEADERS = {"User-Agent": "DanangApartmentsBot/1.0 (telegram rental listing map)"}
MIN_INTERVAL_SECONDS = 1.1

# City name to append to a bare street, plus the box a result must fall inside.
CITY_CONTEXT: dict[str, tuple[str, tuple[float, float, float, float]]] = {
    "da_nang":     ("Da Nang, Vietnam",           (15.90, 16.20, 108.05, 108.35)),
    "nha_trang":   ("Nha Trang, Vietnam",         (12.15, 12.35, 109.10, 109.28)),
    "ho_chi_minh": ("Ho Chi Minh City, Vietnam",  (10.65, 10.95, 106.55, 106.90)),
    "hanoi":       ("Hanoi, Vietnam",             (20.90, 21.15, 105.65, 106.00)),
    "hoi_an":      ("Hoi An, Vietnam",            (15.83, 15.98, 108.25, 108.45)),
}

_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    wait = MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


# Sales talk that gets appended to an address and stops it resolving:
# "Truong Dang Que- near the beach, Sea view".
_NOISE = re.compile(
    r"\b(near|close\s+to|sea\s*view|city\s*view|river\s*view|beach\s*front|"
    r"рядом|вид\s+на|у\s+моря|недалеко|walking\s+distance|full\s+furniture|"
    r"fully\s+furnished|new|brand[\s-]*new)\b.*$",
    re.IGNORECASE,
)


def address_candidates(address: str) -> list[str]:
    """Progressively shorter forms of an address to try, best first.

    A geocoder wants a street, not a sentence, so descriptive tails are trimmed
    and the leading segment is kept as a last resort. Ordering matters: the
    fullest form is most specific, the bare street the most likely to resolve.
    """
    address = (address or "").strip()
    if not address:
        return []

    forms = [address]
    trimmed = _NOISE.sub("", address).strip(" ,.-–—•")
    if trimmed and trimmed != address:
        forms.append(trimmed)
    # First segment before a dash or comma is usually the street itself.
    head = re.split(r"[,\-–—•|]", trimmed or address)[0].strip()
    if head and head not in forms:
        forms.append(head)

    seen, out = set(), []
    for f in forms:
        f = f.strip(" ,.-–—•")
        if len(f) >= 4 and f.lower() not in seen:
            seen.add(f.lower())
            out.append(f)
    return out


def build_query(address: str, city: str) -> str | None:
    """The string to look up, or None if the address is too vague to try."""
    address = (address or "").strip(" ,.-–—•")
    if len(address) < 4:
        return None
    context = CITY_CONTEXT.get(city)
    if not context:
        return None
    suffix = context[0]
    # Don't repeat the city if the address already names it.
    city_word = suffix.split(",")[0].lower()
    if city_word in address.lower():
        return f"{address}, Vietnam"
    return f"{address}, {suffix}"


def geocode_address(address: str, city: str) -> tuple[float, float, str] | None:
    """Resolve an address written in a listing, trying shorter forms as needed."""
    for candidate in address_candidates(address):
        query = build_query(candidate, city)
        if not query:
            continue
        found = geocode(query, city)
        if found:
            return found
    return None


def _within_city(city: str, lat: float, lng: float) -> bool:
    context = CITY_CONTEXT.get(city)
    if not context:
        return False
    lo_la, hi_la, lo_ln, hi_ln = context[1]
    return lo_la <= lat <= hi_la and lo_ln <= lng <= hi_ln


def geocode(query: str, city: str, timeout: int = 20) -> tuple[float, float, str] | None:
    """Resolve one query. Returns (lat, lng, display_name) or None."""
    _throttle()
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 3, "countrycodes": "vn"},
            headers=HEADERS,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        results = resp.json()
    except Exception:
        return None

    for item in results:
        try:
            lat, lng = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        # A street of the same name elsewhere in Vietnam is a wrong answer, not
        # a near miss — reject rather than pin the listing in another province.
        if _within_city(city, lat, lng):
            return lat, lng, item.get("display_name", "")[:500]
    return None
