from __future__ import annotations

from datetime import UTC, date, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def today_utc() -> date:
    return utcnow().date()


def parse_dt(value) -> datetime | None:
    """Parse ISO strings / epoch seconds / epoch millis into aware datetimes; None on failure."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            v = float(value)
            if v > 1e11:  # millis
                v /= 1000.0
            return datetime.fromtimestamp(v, UTC)
        s = str(value).strip().replace("Z", "+00:00")
        if s.isdigit():
            return parse_dt(int(s))
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except (ValueError, OverflowError, OSError):
        return None
