"""Recognise where a listing is, from the way posts actually write it.

Almost no post carries a postal address. What they carry is a district or a
named residential complex — "2BR apartment in My An", "ЖК Скения Бай",
"Marina Suites", "Address: Phu Gia Compound" — and those repeat constantly
across every channel. A gazetteer of those names is therefore far more
effective than geocoding free text, and it costs no network call, so it works
from PythonAnywhere, whose free tier cannot reach a geocoding API anyway.

Coordinates were resolved once via OpenStreetMap Nominatim and are baked in
here; scripts/probe_sources.py's sibling build step is documented in the
README. Accuracy is neighbourhood-level, which is what these listings support:
a complex name pins a building, a district name pins the district.

Names are matched in Vietnamese, English and Russian transliteration, because
the same place is spelled all three ways depending on who wrote the post.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from db.models import CITY_CENTERS, City


@dataclass(frozen=True)
class Place:
    name: str          # canonical display name
    lat: float
    lng: float
    city: City
    precise: bool      # a specific building, as opposed to a whole district


# Each entry: canonical name, coords, city, is-a-building, and the spellings
# seen in posts. Patterns are matched case-insensitively on word boundaries.
_PLACES: list[tuple[Place, list[str]]] = [
    # --- Da Nang: districts and areas ---
    (Place("My An", 16.02510, 108.25953, City.DA_NANG, False),
     [r"my\s*an\b", r"мыан", r"май\s*ан", r"mỹ\s*an"]),
    (Place("Khue My", 16.03702, 108.24667, City.DA_NANG, False),
     [r"khue\s*my", r"кхюэ\s*ми", r"khuê\s*mỹ"]),
    (Place("An Thuong", 16.04953, 108.24439, City.DA_NANG, False),
     [r"an\s*thuong", r"ан\s*тхыонг", r"ан\s*туонг", r"an\s*thượng"]),
    (Place("My Khe", 16.07563, 108.24688, City.DA_NANG, False),
     [r"my\s*khe", r"ми\s*кхе", r"мыкхе", r"mỹ\s*khê"]),
    (Place("Son Tra", 16.10217, 108.24858, City.DA_NANG, False),
     [r"son\s*tra", r"шон\s*ча", r"сон\s*тра", r"sơn\s*trà"]),
    (Place("Ngu Hanh Son", 16.01080, 108.25318, City.DA_NANG, False),
     [r"ngu\s*hanh\s*son", r"нгу\s*хань\s*шон", r"ngũ\s*hành\s*sơn"]),
    (Place("Hai Chau", 16.05895, 108.21948, City.DA_NANG, False),
     [r"hai\s*chau", r"хай\s*тяу", r"хайчау", r"hải\s*châu"]),
    (Place("Thanh Khe", 16.06645, 108.18234, City.DA_NANG, False),
     [r"thanh\s*khe", r"тхань\s*кхе", r"thanh\s*khê"]),
    (Place("Lien Chieu", 16.07671, 108.13924, City.DA_NANG, False),
     [r"lien\s*chieu", r"льен\s*тьеу", r"liên\s*chiểu"]),
    (Place("Cam Le", 16.01530, 108.20131, City.DA_NANG, False),
     [r"cam\s*le\b", r"кам\s*ле", r"cẩm\s*lệ"]),
    (Place("Hoa Xuan", 16.00620, 108.21464, City.DA_NANG, False),
     [r"hoa\s*xuan", r"хоа\s*суан", r"hòa\s*xuân"]),
    (Place("Han River", 16.06442, 108.22699, City.DA_NANG, False),
     [r"han\s*river", r"река\s*хан", r"sông\s*hàn"]),
    # --- Da Nang: named buildings ---
    (Place("Phu Gia Compound", 16.07789, 108.21200, City.DA_NANG, True),
     [r"phu\s*gia\s*compound", r"phú\s*gia\s*compound"]),

    # --- Nha Trang: named complexes (these dominate the Nha Trang channels) ---
    (Place("Marina Suites", 12.25202, 109.19294, City.NHA_TRANG, True),
     [r"marina\s*suites?", r"марина\s*сьюит", r"марина\s*суит"]),
    (Place("Scenia Bay", 12.28117, 109.20154, City.NHA_TRANG, True),
     [r"scenia\s*bay", r"scenia", r"скения\s*бай", r"скения"]),
    (Place("Oceanus", 12.27378, 109.20209, City.NHA_TRANG, True),
     [r"oceanus", r"океанус"]),
    (Place("Muong Thanh", 12.27378, 109.20209, City.NHA_TRANG, True),
     [r"muong\s*thanh", r"мыонг\s*тхань", r"мунь\s*тань", r"мыньтань",
      r"мунь\s*тхань", r"mường\s*thanh"]),
    (Place("Gold Coast", 12.24823, 109.19512, City.NHA_TRANG, True),
     [r"gold\s*coast", r"голд\s*кост"]),
    (Place("Panorama", 12.23913, 109.19542, City.NHA_TRANG, True),
     [r"panorama\b", r"панорама\b"]),
    # --- Nha Trang: districts and landmarks ---
    (Place("Phuoc Long", 12.20967, 109.19125, City.NHA_TRANG, False),
     [r"phuoc\s*long", r"фыок\s*лонг", r"фуок\s*лонг", r"phước\s*long"]),
    (Place("Hon Chong", 12.27284, 109.20656, City.NHA_TRANG, False),
     [r"hon\s*chong", r"хон\s*чонг", r"хончонг", r"hòn\s*chồng"]),
    (Place("Vinh Hai", 12.27863, 109.19403, City.NHA_TRANG, False),
     [r"vinh\s*hai", r"винь\s*хай", r"vĩnh\s*hải"]),
    (Place("Loc Tho", 12.26132, 109.16931, City.NHA_TRANG, False),
     [r"loc\s*tho", r"лок\s*тхо", r"lộc\s*thọ"]),
    (Place("Tan Lap", 12.24214, 109.18993, City.NHA_TRANG, False),
     [r"tan\s*lap", r"тан\s*лап", r"tân\s*lập"]),
    (Place("Vinh Truong", 12.20369, 109.17830, City.NHA_TRANG, False),
     [r"vinh\s*truong", r"винь\s*чыонг", r"vĩnh\s*trường"]),
    (Place("Phan Chu Trinh", 12.25463, 109.19569, City.NHA_TRANG, False),
     [r"phan\s*chu\s*trinh", r"фан\s*чу\s*чинь"]),
    (Place("Tran Phu", 12.24685, 109.19637, City.NHA_TRANG, False),
     [r"tran\s*phu", r"чан\s*фу", r"trần\s*phú"]),
    (Place("Dam Market", 12.25545, 109.19414, City.NHA_TRANG, False),
     [r"dam\s*market", r"рынок\s*дам", r"чợ\s*đầm", r"chợ\s*đầm"]),

    # --- Ho Chi Minh City ---
    (Place("Thao Dien", 10.80051, 106.73365, City.HO_CHI_MINH, False),
     [r"thao\s*dien", r"тхао\s*дьен", r"thảo\s*điền"]),
    (Place("District 1", 10.77539, 106.69963, City.HO_CHI_MINH, False),
     [r"district\s*1\b", r"quận\s*1\b", r"район\s*1\b", r"1[-\s]?й\s+район"]),
    (Place("District 7", 10.73785, 106.72970, City.HO_CHI_MINH, False),
     [r"district\s*7\b", r"quận\s*7\b", r"район\s*7\b", r"7[-\s]?й\s+район"]),
    (Place("Binh Thanh", 10.81118, 106.70339, City.HO_CHI_MINH, False),
     [r"binh\s*thanh", r"бинь\s*тхань", r"bình\s*thạnh"]),
    (Place("Phu Nhuan", 10.79545, 106.67547, City.HO_CHI_MINH, False),
     [r"phu\s*nhuan", r"фу\s*нюан", r"phú\s*nhuận"]),

    # --- Hanoi ---
    (Place("Tay Ho", 21.06094, 105.82407, City.HANOI, False),
     [r"tay\s*ho\b", r"тай\s*хо", r"tây\s*hồ"]),
    (Place("Hoan Kiem", 21.03230, 105.85069, City.HANOI, False),
     [r"hoan\s*kiem", r"хоан\s*кьем", r"hoàn\s*kiếm"]),
    (Place("Ba Dinh", 21.03953, 105.83642, City.HANOI, False),
     [r"ba\s*dinh", r"ба\s*динь", r"ba\s*đình"]),
    (Place("Cau Giay", 21.02922, 105.80336, City.HANOI, False),
     [r"cau\s*giay", r"кау\s*зай", r"cầu\s*giấy"]),

    # --- Hoi An ---
    (Place("An Bang", 15.91418, 108.33966, City.HOI_AN, False),
     [r"an\s*bang", r"ан\s*банг", r"an\s*bàng"]),
    (Place("Cam Chau", 15.88280, 108.34289, City.HOI_AN, False),
     [r"cam\s*chau", r"кам\s*тяу", r"cẩm\s*châu"]),
]

_COMPILED: list[tuple[Place, list[re.Pattern]]] = [
    (place, [re.compile(p, re.IGNORECASE) for p in patterns])
    for place, patterns in _PLACES
]

# An explicitly labelled address line, which beats any guess from the body.
_ADDRESS_LABEL = re.compile(
    r"(?:address|адрес|địa\s*chỉ|расположение|location)\s*[:：]\s*(.{3,80})",
    re.IGNORECASE,
)


def find_place(text: str, city_hint: City | None = None) -> Place | None:
    """Best known place mentioned in the post.

    A named building wins over a district, since it pins an actual address.
    When the post's city is known, places in other cities are ignored — a
    Nha Trang post mentioning "Panorama" must not land in Da Nang.
    """
    if not text:
        return None

    matches: list[Place] = []
    for place, patterns in _COMPILED:
        if city_hint and place.city != city_hint:
            continue
        if any(p.search(text) for p in patterns):
            matches.append(place)

    if not matches:
        return None
    # Buildings first; among equals, the first listed wins deterministically.
    matches.sort(key=lambda p: (not p.precise,))
    return matches[0]


def find_address_text(text: str) -> str | None:
    """The human-readable address to show on the card, if the post labels one."""
    m = _ADDRESS_LABEL.search(text or "")
    if not m:
        return None
    value = m.group(1).strip().strip("-–—•").strip()
    # A label followed by nothing useful ("Address: #SonTra") is not an address.
    value = re.sub(r"\s+", " ", value)
    return value if len(value) >= 3 and not value.startswith("#") else None


def fallback_coords(city: City, seed: str) -> tuple[float | None, float | None]:
    """A rough position for a listing whose location could not be identified.

    Placing every such listing on one exact point would stack the markers so
    only the top one is clickable, so each is nudged by a small deterministic
    offset (about +/-250m, derived from the listing itself so it never moves
    between requests). Anything positioned this way is flagged
    location_is_approximate and drawn differently on the map.
    """
    center = CITY_CENTERS.get(city)
    if not center:
        return None, None
    digest = hashlib.sha256(seed.encode()).digest()
    lat_offset = (digest[0] / 255 - 0.5) * 0.0045
    lng_offset = (digest[1] / 255 - 0.5) * 0.0045
    return center[0] + lat_offset, center[1] + lng_offset
