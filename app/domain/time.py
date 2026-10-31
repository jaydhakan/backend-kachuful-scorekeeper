"""Time on the wire is epoch milliseconds; in the database it is timestamptz. Days are APP_TZ days."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DAY_MS = 86_400_000


def to_ms(moment: datetime) -> int:
    """Epoch milliseconds of an aware datetime."""
    return int(moment.timestamp() * 1000)


def to_ms_or_none(moment: datetime | None) -> int | None:
    """`to_ms`, passing None through."""
    return None if moment is None else to_ms(moment)


def from_ms(ms: int) -> datetime:
    """Aware UTC datetime of epoch milliseconds."""
    return datetime.fromtimestamp(ms / 1000, UTC)


def now_utc() -> datetime:
    """The current time, aware, in UTC."""
    return datetime.now(UTC)


def day_of(ms: int, tz: ZoneInfo) -> date:
    """The calendar day of an instant in a timezone."""
    return from_ms(ms).astimezone(tz).date()


def day_start_ms(day: date, tz: ZoneInfo) -> int:
    """Epoch ms of midnight at the start of a day in a timezone."""
    return to_ms(datetime.combine(day, time.min, tz))


def day_range_ms(day: date, tz: ZoneInfo) -> tuple[int, int]:
    """Half-open [start, end) of a calendar day in a timezone."""
    return day_start_ms(day, tz), day_start_ms(day + timedelta(days=1), tz)


def month_range_ms(month: str, tz: ZoneInfo) -> tuple[int, int]:
    """Half-open [start, end) of a 'YYYY-MM' month in a timezone."""
    year, mon = (int(part) for part in month.split("-"))
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return day_start_ms(start, tz), day_start_ms(end, tz)


def month_of(ms: int, tz: ZoneInfo) -> str:
    """'YYYY-MM' of an instant in a timezone."""
    return day_of(ms, tz).strftime("%Y-%m")


def previous_month(month: str) -> str:
    """The 'YYYY-MM' before this one."""
    year, mon = (int(part) for part in month.split("-"))
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


def hour_of(ms: int, tz: ZoneInfo) -> int:
    """Hour (0-23) of an instant in a timezone."""
    return from_ms(ms).astimezone(tz).hour
