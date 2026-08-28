"""Shared SQLAlchemy models used by parser, api and bot services."""
from __future__ import annotations

import enum
import os
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./danang_apartments.db")


class Base(DeclarativeBase):
    pass


class ListingStatus(str, enum.Enum):
    PENDING = "pending"       # только что распарсено / прислано вручную, ждёт модерации
    APPROVED = "approved"     # видно в Mini App
    REJECTED = "rejected"     # отклонено модератором (дубль, спам, мошенник)
    EXPIRED = "expired"       # помечено неактуальным (жалоба пользователей или TTL)


class SourceType(str, enum.Enum):
    TELEGRAM = "telegram"
    FACEBOOK = "facebook"
    MANUAL = "manual"
    CHOTOT = "chotot"   # Vietnam's main classifieds site, via its public API


class PropertyType(str, enum.Enum):
    APARTMENT = "apartment"
    ROOM = "room"
    HOUSE = "house"
    VILLA = "villa"


class RenovationQuality(str, enum.Enum):
    NEEDS_REPAIR = "needs_repair"
    STANDARD = "standard"
    GOOD = "good"
    PREMIUM = "premium"


class PetsPolicy(str, enum.Enum):
    ALLOWED = "allowed"
    NOT_ALLOWED = "not_allowed"
    UNKNOWN = "unknown"


class City(str, enum.Enum):
    DA_NANG = "da_nang"
    NHA_TRANG = "nha_trang"
    HO_CHI_MINH = "ho_chi_minh"
    HANOI = "hanoi"
    HOI_AN = "hoi_an"
    OTHER = "other"


# Where to put a listing whose location could not be identified from its text.
#
# These are the districts people actually rent in, not each city's geometric
# centre: Da Nang's centroid falls in Hòa Cường right beside the airport, so
# fallback pins were landing on the runway and reading as a broken address
# parser. A pin here is still a guess — listings placed this way are flagged
# approximate and drawn differently on the map.
CITY_CENTERS: dict[City, tuple[float, float]] = {
    City.DA_NANG: (16.04953, 108.24439),    # An Thuong, the beachside rental area
    City.NHA_TRANG: (12.24685, 109.19637),  # Tran Phu, the beach strip
    City.HO_CHI_MINH: (10.77539, 106.69963),  # District 1
    City.HANOI: (21.03230, 105.85069),      # Hoan Kiem
    City.HOI_AN: (15.88280, 108.34289),     # Cam Chau, between the old town and the beach
}


# A channel that only ever posts about one city lets us fill in the city for
# posts that name just a district ("Brand-New 1BR in Khue My"), which is common
# because the channel's own readers already know which city it covers.
CHANNEL_DEFAULT_CITY: dict[str, City] = {
    "danangrentaflat": City.DA_NANG,
    "onewaydanang": City.DA_NANG,
    "danang_housing": City.DA_NANG,
    "danangmls": City.DA_NANG,
    "danang_rent": City.DA_NANG,
    "arenda_nyachang_zhilye": City.NHA_TRANG,
    "viet_life_niachang": City.NHA_TRANG,
    "arenda_nhatrang": City.NHA_TRANG,
    "nyachang_arenda_kvartir": City.NHA_TRANG,
    # Deliberately absent: arenda_vietnam covers the whole country, so a post
    # that names no city there must stay OTHER rather than be guessed at.
}


class Source(Base):
    """A monitored Telegram channel / Facebook group."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[SourceType] = mapped_column(Enum(SourceType), default=SourceType.TELEGRAM)
    channel_username: Mapped[str] = mapped_column(String(255), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Curated channels publish straight to the map: holding a rental listing in
    # a review queue makes it stale, which defeats the point of scraping every
    # few minutes. Quality is defended by the automatic checks in
    # server/quality.py plus after-the-fact review, not by a human gate.
    # User submissions (SourceType.MANUAL/FACEBOOK) are never auto-published.
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=True)


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    status: Mapped[ListingStatus] = mapped_column(Enum(ListingStatus), default=ListingStatus.PENDING)

    # Источник — ссылка на оригинал всегда хранится и всегда показывается в Mini App.
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType))
    source_channel: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str] = mapped_column(String(1024))
    source_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Локация
    city: Mapped[City] = mapped_column(Enum(City), default=City.OTHER)
    address_text: Mapped[str | None] = mapped_column(String(512), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Цена
    price_min_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_max_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Характеристики
    rooms: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "studio", "1", "2", "3", "4+"
    property_type: Mapped[PropertyType | None] = mapped_column(Enum(PropertyType), nullable=True)
    renovation_quality: Mapped[RenovationQuality | None] = mapped_column(Enum(RenovationQuality), nullable=True)
    pets_policy: Mapped[PetsPolicy] = mapped_column(Enum(PetsPolicy), default=PetsPolicy.UNKNOWN)
    area_sqm: Mapped[float | None] = mapped_column(Float, nullable=True)
    floor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    furnished: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_parking: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_pool: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    deposit_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    min_lease_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Metres to the sea, when the post states it. A decision factor people
    # actually filter on in these cities, and the channels quote it constantly.
    sea_distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # исходный текст поста, для отладки экстрактора
    contact: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Fingerprint of the normalised text, used to spot the same flat reposted
    # later or cross-posted to another channel. Indexed because every ingested
    # post is looked up by it.
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    # Set when the automatic checks published something they weren't sure about
    # (no price, implausibly cheap). Surfaced to admins via /review.
    quality_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    # True when lat/lng is a city-area guess rather than a recognised district
    # or building. Drawn differently on the map, because a precise-looking pin
    # for an unknown address is worse than an obviously vague one.
    location_is_approximate: Mapped[bool] = mapped_column(Boolean, default=True)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    photos: Mapped[list["Photo"]] = relationship(back_populates="listing", cascade="all, delete-orphan")


class Photo(Base):
    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    url: Mapped[str] = mapped_column(String(1024))
    position: Mapped[int] = mapped_column(Integer, default=0)

    listing: Mapped[Listing] = relationship(back_populates="photos")


class TgUser(Base):
    __tablename__ = "tg_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # BigInteger: Telegram user ids have outgrown the 32-bit signed range.
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Seeded from the client's Telegram language on first contact, then
    # overridden by an explicit choice via /language.
    lang: Mapped[str] = mapped_column(String(8), default="ru")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    saved_filters: Mapped[list["SavedFilter"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class SavedFilter(Base):
    """A standing subscription: new matching listings are pushed to the user.

    Some people would rather not open the map at all and just be told when
    something matching turns up, so this is offered alongside the Mini App
    rather than instead of it. Multi-value fields are stored comma-separated
    to mirror the multi-select filters in the Mini App.
    """

    __tablename__ = "saved_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("tg_users.id"))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    cities: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price_min_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_max_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    rooms: Mapped[str | None] = mapped_column(String(64), nullable=True)
    property_types: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pets_policy: Mapped[PetsPolicy | None] = mapped_column(Enum(PetsPolicy), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Ensures a listing is pushed at most once per subscription, even if the
    # ingest that created it is retried.
    last_sent_listing_id: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[TgUser] = relationship(back_populates="saved_filters")

    def matches(self, listing: "Listing") -> bool:
        if self.cities and listing.city.value not in self.cities.split(","):
            return False
        if self.rooms and (listing.rooms or "") not in self.rooms.split(","):
            return False
        if self.property_types:
            ptype = listing.property_type.value if listing.property_type else ""
            if ptype not in self.property_types.split(","):
                return False
        if self.pets_policy and listing.pets_policy != self.pets_policy:
            return False
        # Budget overlap, matching the semantics of the map's price filter.
        if self.price_min_usd is not None:
            if listing.price_max_usd is None or listing.price_max_usd < self.price_min_usd:
                return False
        if self.price_max_usd is not None:
            if listing.price_min_usd is None or listing.price_min_usd > self.price_max_usd:
                return False
        return True


class GeocodeCache(Base):
    """One resolved address, kept so it is never looked up twice.

    Geocoding is the slow, rate-limited part of the pipeline (Nominatim asks for
    at most one request a second), and the same streets and complexes recur
    across thousands of listings. Caching turns that from a per-listing cost
    into a per-distinct-address one.

    A row with lat/lng NULL records a failed lookup, so a hopeless address is
    not retried on every run.
    """

    __tablename__ = "geocode_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserListingState(str, enum.Enum):
    SAVED = "saved"    # shortlisted to come back to
    VIEWED = "viewed"  # already looked at, dimmed so the list stays scannable


class UserListing(Base):
    """One person's relationship to one listing.

    Strictly per-user: whether Свет has looked at a flat says nothing about
    anyone else, so this is keyed on the viewer as well as the listing and is
    never exposed on the public feed.
    """

    __tablename__ = "user_listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    state: Mapped[UserListingState] = mapped_column(Enum(UserListingState))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("telegram_id", "listing_id", "state", name="uq_user_listing_state"),
    )


class RateLimitState(Base):
    """Per-user, per-action sliding-window counter used by server/ratelimit.py.

    One row per (telegram_id, action) — e.g. action="message" for the generic
    anti-burst debounce, action="submit" for the stricter /submit throttle.
    """

    __tablename__ = "rate_limit_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger)  # see TgUser.telegram_id
    action: Mapped[str] = mapped_column(String(32))

    window_start: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    window_count: Mapped[int] = mapped_column(Integer, default=0)

    blocked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    violation_count: Mapped[int] = mapped_column(Integer, default=0)
    last_violation_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("telegram_id", "action", name="uq_ratelimit_user_action"),)


engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
