"""Bark 文案和 HTTP 推送。正文只有数字，标题最多用围栏名。"""

from __future__ import annotations

from typing import Protocol

import httpx

from app.formatutil import fmt_minutes, fmt_number, fmt_signed
from app.models import Charge, Drive


class BarkError(RuntimeError):
    pass


def page_url(base: str, kind: str, token: str) -> str:
    return f"{base.rstrip('/')}/{kind}/{token}"


def drive_title(start_geofence: str | None, end_geofence: str | None, distance_km: float | None) -> str:
    start = (start_geofence or "").strip()
    end = (end_geofence or "").strip()
    if start and end:
        return f"{start} → {end}"
    if start or end:
        return start or end
    return f"出门 {fmt_number(distance_km)} km"


def drive_body(
    distance_km: float | None,
    duration_min: float | None,
    rated_delta_km: float | None,
    rated_end_km: float | None,
) -> str:
    return (
        f"{fmt_number(distance_km)} km，{fmt_minutes(duration_min)}，"
        f"额定续航 {fmt_signed(rated_delta_km)} km，结束 {fmt_number(rated_end_km)} km"
    )


def charge_title(geofence: str | None) -> str:
    name = (geofence or "").strip()
    return name or "充电完成"


def charge_body(energy_kwh: float | None, duration_min: float | None, end_battery: int | None) -> str:
    battery = "—" if end_battery is None else str(int(end_battery))
    return f"加入 {fmt_number(energy_kwh)} kWh，{fmt_minutes(duration_min)}，结束电量 {battery}%"


def summarize_drive_notification(drives: list[Drive]) -> tuple[str, str]:
    ordered = sorted(drives, key=lambda drive: (drive.end_date, drive.id))
    distance = _sum(drive.distance_km for drive in ordered)
    duration = _sum(drive.duration_min for drive in ordered)
    rated_start = ordered[0].start_rated_range_km
    rated_end = ordered[-1].end_rated_range_km
    rated_delta = None
    if rated_start is not None and rated_end is not None:
        rated_delta = float(rated_end) - float(rated_start)
    title = drive_title(ordered[0].start_geofence, ordered[-1].end_geofence, distance)
    body = drive_body(distance, duration, rated_delta, rated_end)
    return title, body


def summarize_charge_notification(charge: Charge) -> tuple[str, str]:
    return (
        charge_title(charge.geofence),
        charge_body(charge.charge_energy_added, charge.duration_min, charge.end_battery_level),
    )


def envelope(device_key: str, title: str, body: str, url: str) -> dict[str, object]:
    return {
        "device_key": device_key,
        "title": title,
        "body": body,
        "url": url,
        "group": "teslaslip",
        "isArchive": 1,
        "level": "active",
    }


def notification_text(title: str, body: str) -> str:
    return f"{title}\n{body}"


def _sum(values) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return sum(present)


class BarkSender(Protocol):
    async def push(self, payload: dict[str, object]) -> None: ...


class HttpBark:
    def __init__(self, base_url: str) -> None:
        self._url = f"{base_url.rstrip('/')}/push"

    async def push(self, payload: dict[str, object]) -> None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(self._url, json=payload)
        except httpx.HTTPError as exc:
            raise BarkError(type(exc).__name__) from None
        if response.status_code >= 300:
            raise BarkError(f"http {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            return
        code = body.get("code") if isinstance(body, dict) else None
        if code not in (None, 200, "200"):
            raise BarkError("bark rejected")
