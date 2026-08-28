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


# Approximate city-center coordinates, used as a fallback marker position when
# a listing has no precise address geocoded yet (moderators can refine lat/lng
# via PATCH /admin/listings/<id> once they know the exact building).
CITY_CENTERS: dict[City, tuple[float, float]] = {
    City.DA_NANG: (16.0544, 108.2022),
    City.NHA_TRANG: (12.2388, 109.1967),
    City.HO_CHI_MINH: (10.7769, 106.7009),
    City.HANOI: (21.0278, 105.8342),
    City.HOI_AN: (15.8801, 108.3380),
}


# A channel that only ever posts about one city lets us fill in the city for
# posts that name just a district ("Brand-New 1BR in Khue My"), which is common
# because the channel's own readers already know which city it covers.
CHANNEL_DEFAULT_CITY: dict[str, City] = {
    "danangrentaflat": City.DA_NANG,
    "onewaydanang": City.DA_NANG,
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

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # исходный текст поста, для отладки экстрактора
    contact: Mapped[str | None] = mapped_column(String(255), nullable=True)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    saved_filters: Mapped[list["SavedFilter"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class SavedFilter(Base):
    """A filter subscription — user gets notified when a matching listing is approved."""

    __tablename__ = "saved_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("tg_users.id"))

    city: Mapped[City | None] = mapped_column(Enum(City), nullable=True)
    price_min_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_max_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    rooms: Mapped[str | None] = mapped_column(String(16), nullable=True)
    property_type: Mapped[PropertyType | None] = mapped_column(Enum(PropertyType), nullable=True)
    pets_policy: Mapped[PetsPolicy | None] = mapped_column(Enum(PetsPolicy), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[TgUser] = relationship(back_populates="saved_filters")


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
