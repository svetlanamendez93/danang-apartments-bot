"""Graduated flood/spam protection for the bot.

Two independent checks are used (see server/app.py):

- "message" — a loose debounce against accidental rapid-fire taps (double
  /start, mashing a button). Excess messages within a short window are just
  silently dropped, no warning, no penalty — a few accidental clicks in a row
  should never look like abuse.
- "submit" — a stricter throttle on /submit specifically, since it writes to
  the DB and pings every admin. Exceeding it gets a polite "slow down"
  message, not a ban. Only *repeated* abuse across several separate windows
  escalates to a longer cooldown, and violations decay after a day of good
  behaviour — there's no automatic permanent ban anywhere in here. A real ban
  is a manual admin decision, not something this code does on its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from db.models import RateLimitState

VIOLATION_DECAY = timedelta(hours=24)


def _get_or_create(session: Session, telegram_id: int, action: str) -> RateLimitState:
    state = (
        session.query(RateLimitState)
        .filter_by(telegram_id=telegram_id, action=action)
        .first()
    )
    if not state:
        state = RateLimitState(telegram_id=telegram_id, action=action, window_start=datetime.utcnow())
        session.add(state)
        session.flush()
    return state


def check(
    session: Session,
    telegram_id: int,
    action: str,
    limit: int,
    window_seconds: int,
    block_durations_seconds: list[int] | None = None,
) -> tuple[bool, str | None]:
    """Returns (allowed, message).

    `message` is non-None only the moment a new block/warning starts (so the
    caller sends it once, not on every ignored message afterwards).
    If `block_durations_seconds` is None, exceeding the limit just silently
    drops the message with no block and no reply at all (used for "message").
    """
    now = datetime.utcnow()
    state = _get_or_create(session, telegram_id, action)

    if state.violation_count and state.last_violation_at and now - state.last_violation_at > VIOLATION_DECAY:
        state.violation_count = 0

    if state.blocked_until and state.blocked_until > now:
        return False, None  # already warned when the block started; stay quiet now

    if now - state.window_start > timedelta(seconds=window_seconds):
        state.window_start = now
        state.window_count = 0

    state.window_count += 1

    if state.window_count <= limit:
        session.commit()
        return True, None

    if not block_durations_seconds:
        session.commit()
        return False, None

    state.violation_count += 1
    state.last_violation_at = now
    duration = block_durations_seconds[min(state.violation_count - 1, len(block_durations_seconds) - 1)]
    state.blocked_until = now + timedelta(seconds=duration)
    state.window_count = 0
    session.commit()

    minutes = max(1, duration // 60)
    return False, (
        f"Слишком много запросов подряд. Пожалуйста, подождите {minutes} мин. и попробуйте снова."
    )
