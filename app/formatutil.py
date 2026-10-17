"""展示用的数字和时间。库内时间是 UTC，页面用 Asia/Shanghai。"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo("UTC"))


def as_naive_utc(value: datetime) -> datetime:
    return as_utc(value).replace(tzinfo=None)


def fmt_local(value: datetime) -> str:
    return as_utc(value).astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")


def fmt_number(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def fmt_signed(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{float(value):+.{digits}f}"


def fmt_minutes(value: float | None) -> str:
    if value is None:
        return "—"
    total = int(round(float(value)))
    if total < 0:
        total = 0
    hours, minutes = divmod(total, 60)
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{minutes} 分钟"


def fmt_elapsed(delta: timedelta) -> str:
    return fmt_minutes(delta.total_seconds() / 60.0)


def fmt_optional_int(value: float | None) -> str:
    if value is None:
        return "—"
    return str(int(round(float(value))))
