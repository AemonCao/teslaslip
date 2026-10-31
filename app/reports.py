"""自然周、月、年报告：只读汇总、固定快照和持久化调度。"""

from __future__ import annotations

import math
import secrets
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.config import Settings
from app.db import Store
from app.formatutil import SHANGHAI, as_utc, fmt_local, fmt_minutes, fmt_number
from app.models import Drive
from app.outing import group_outings, is_pure_shuffle
from app.pages import expiry_from

KINDS = {"week": "周报", "month": "月报", "year": "年报"}


@dataclass(frozen=True)
class Period:
    kind: str
    start: datetime
    end: datetime

    @property
    def title(self) -> str:
        start = self.start.astimezone(SHANGHAI)
        if self.kind == "year":
            return f"{start.year} 年用车年报"
        if self.kind == "month":
            return f"{start.year} 年 {start.month} 月用车月报"
        last = (self.end.astimezone(SHANGHAI) - timedelta(days=1)).date()
        return f"{start:%m.%d}–{last:%m.%d} 用车周报"


def period_for(kind: str, value: datetime | date) -> Period:
    if kind not in KINDS:
        raise ValueError("报告类型必须为 week、month 或 year")
    local = as_utc(value).astimezone(SHANGHAI).date() if isinstance(value, datetime) else value
    if kind == "week":
        first = local - timedelta(days=local.weekday())
        last = first + timedelta(days=7)
    elif kind == "month":
        first = local.replace(day=1)
        last = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    else:
        first = date(local.year, 1, 1)
        last = date(local.year + 1, 1, 1)
    return Period(
        kind,
        datetime.combine(first, datetime.min.time(), SHANGHAI).astimezone(timezone.utc),
        datetime.combine(last, datetime.min.time(), SHANGHAI).astimezone(timezone.utc),
    )


def previous_period(period: Period) -> Period:
    return period_for(period.kind, period.start - timedelta(microseconds=1))


def latest_due_period(kind: str, now: datetime, hour: int) -> Period:
    previous = previous_period(period_for(kind, now))
    if previous.end + timedelta(hours=hour) > now:
        return previous_period(previous)
    return previous


def _valid(value) -> bool:
    return value is not None and math.isfinite(float(value)) and float(value) >= 0


def _total(values) -> tuple[float | None, int]:
    rows = list(values)
    present = [float(value) for value in rows if _valid(value)]
    return (sum(present) if present or not rows else None), len(rows) - len(present)


def _unit(value, unit: str) -> str:
    return f"{fmt_number(value)} {unit}" if value is not None else "未知"


def _summary(drives: list[Drive], charges: list[dict], settings: Settings) -> dict:
    distance, distance_missing = _total(drive.distance_km for drive in drives)
    duration, duration_missing = _total(drive.duration_min for drive in drives)
    energy, energy_missing = _total(charge["charge_energy_added"] for charge in charges)
    cost, cost_missing = _total(charge["cost"] for charge in charges)
    charge_duration, charge_duration_missing = _total(charge["duration_min"] for charge in charges)
    outings = [
        group for group in group_outings(drives, settings.outing_gap_minutes)
        if not is_pure_shuffle(group, settings.min_push_distance_km, settings.min_push_duration_min)
    ]
    return {
        "distance": distance, "distance_missing": distance_missing,
        "duration": duration, "duration_missing": duration_missing,
        "energy": energy, "energy_missing": energy_missing,
        "cost": cost, "cost_missing": cost_missing,
        "charge_duration": charge_duration, "charge_duration_missing": charge_duration_missing,
        "charge_count": len(charges), "drive_count": len(drives),
        "outing_count": len(outings),
        "active_days": len({drive.end_date.astimezone(SHANGHAI).date() for drive in drives}),
        "outings": outings,
    }


def _comparison(current: dict, previous: dict, allowed: bool) -> list[dict]:
    rows = []
    for key, label, unit in (("distance", "行驶里程", "km"), ("duration", "驾驶时长", "分钟"), ("energy", "充入电量", "kWh")):
        before, after = previous[key], current[key]
        if not allowed:
            text = "记录起点不足以覆盖两个完整周期"
        elif current[f"{key}_missing"] or previous[f"{key}_missing"]:
            text = "存在缺失记录，暂不比较"
        elif before == 0:
            text = "均为 0" if after == 0 else "上期为 0，不计算百分比"
        else:
            change = (after - before) / before * 100
            text = "与上期持平" if abs(change) < 0.05 else f"比上期{'增加' if change > 0 else '减少'} {abs(change):.1f}%"
        rows.append({"label": label, "previous": _unit(before, unit), "text": text})
    return rows


def _trend(period: Period, drives: list[Drive], charges: list[dict]) -> list[dict]:
    buckets = {}
    local_start = period.start.astimezone(SHANGHAI).date()
    local_end = period.end.astimezone(SHANGHAI).date()
    current = local_start
    while current < local_end:
        label = f"{current.month} 月" if period.kind == "year" else f"{current.month}/{current.day}"
        buckets[current] = {"label": label, "distance_values": [], "energy_values": []}
        if period.kind == "year":
            current = date(current.year + current.month // 12, current.month % 12 + 1, 1)
        else:
            current += timedelta(days=1)
    for drive in drives:
        day = drive.end_date.astimezone(SHANGHAI).date()
        key = day.replace(day=1) if period.kind == "year" else day
        buckets[key]["distance_values"].append(drive.distance_km)
    for charge in charges:
        day = charge["end_date"].astimezone(SHANGHAI).date()
        key = day.replace(day=1) if period.kind == "year" else day
        buckets[key]["energy_values"].append(charge["charge_energy_added"])
    rows = []
    for bucket in buckets.values():
        distance, distance_missing = _total(bucket["distance_values"])
        energy, energy_missing = _total(bucket["energy_values"])
        rows.append({
            "label": bucket["label"], "distance": distance, "energy": energy,
            "distance_text": _unit(distance, "km") + ("（有缺失）" if distance_missing else ""),
            "energy_text": _unit(energy, "kWh") + ("（有缺失）" if energy_missing else ""),
            "has_records": bool(bucket["distance_values"] or bucket["energy_values"]),
        })
    for key in ("distance", "energy"):
        maximum = max((row[key] or 0 for row in rows), default=0) or 1
        for index, row in enumerate(rows):
            row[f"{key}_height"] = round((row[key] or 0) / maximum * 120, 2)
            row["x"] = round(32 + index * 496 / len(rows), 2)
            row["width"] = round(496 / len(rows) * 0.65, 2)
            row["show_label"] = len(rows) <= 12 or index % 5 == 0 or index == len(rows) - 1
    return rows


def build_report(period: Period, drives: list[Drive], charges: list[dict], first_record: datetime | None, settings: Settings, now: datetime) -> dict:
    previous = previous_period(period)
    current_drives = [drive for drive in drives if period.start <= drive.end_date < period.end]
    previous_drives = [drive for drive in drives if previous.start <= drive.end_date < previous.end]
    current_charges = [charge for charge in charges if period.start <= charge["end_date"] < period.end]
    previous_charges = [charge for charge in charges if previous.start <= charge["end_date"] < previous.end]
    summary = _summary(current_drives, current_charges, settings)
    before = _summary(previous_drives, previous_charges, settings)
    trend = _trend(period, current_drives, current_charges)
    notes = [
        "仅反映数据库已记录数据；没有记录不代表没有开车，无法据此判断采集是否连续。",
        "按上海时间统计，行程与充电整条归入结束日；跨周期出门在各周期内分别合并，纯挪车不计出门次数，里程仍计入。",
        "充入电量不等于行驶耗电；费用沿用原记录币种，缺失费用不按免费计算。",
    ]
    if first_record is None:
        coverage = "暂无行程或充电记录"
    else:
        coverage = f"数据库最早记录：{fmt_local(first_record)}"
        if first_record > period.start:
            coverage += " · 本期记录范围不完整"
    for key, count in (("里程", summary["distance_missing"]), ("驾驶时长", summary["duration_missing"]), ("充入电量", summary["energy_missing"]), ("充电时长", summary["charge_duration_missing"])):
        if count:
            notes.append(f"{count} 条{key}记录缺失或无效，合计仅包含已知值。")
    cost_known = summary["charge_count"] - summary["cost_missing"]
    cost_coverage = f"{cost_known}/{summary['charge_count']} 次有费用记录"
    highlights = []
    if current_drives:
        daily = Counter()
        for drive in current_drives:
            if _valid(drive.distance_km):
                daily[drive.end_date.astimezone(SHANGHAI).strftime("%m-%d")] += drive.distance_km
        if daily:
            day, distance = daily.most_common(1)[0]
            highlights.append(f"已知里程最多的一天：{day}，{distance:.1f} km。")
        candidates = [(group, _total(drive.distance_km for drive in group)[0]) for group in summary["outings"]]
        candidates = [(group, distance) for group, distance in candidates if distance is not None]
        if candidates:
            group, distance = max(candidates, key=lambda item: item[1])
            highlights.append(f"已知里程最长的一次出门：{fmt_local(group[0].start_date)}，{distance:.1f} km。")
    places = Counter(drive.end_geofence or drive.end_address.city or drive.end_address.name for drive in current_drives)
    places.pop(None, None)
    places.pop("", None)
    if places:
        name, count = places.most_common(1)[0]
        highlights.append(f"最常到达的地点：{name}，共 {count} 段行程。")
    cities = sorted({drive.end_address.city for drive in current_drives if drive.end_address.city})
    metrics = [
        {"label": "行驶里程", "value": _unit(summary["distance"], "km"), "note": "已知值合计" if summary["distance_missing"] else "已记录行程合计"},
        {"label": "驾驶时长", "value": fmt_minutes(summary["duration"]), "note": "已知值合计" if summary["duration_missing"] else "行程记录时长"},
        {"label": "出门次数", "value": f"{summary['outing_count']} 次", "note": f"{summary['drive_count']} 段已结束行程"},
        {"label": "用车天数", "value": f"{summary['active_days']} 天", "note": "有已结束行程的日期"},
        {"label": "充入电量", "value": _unit(summary["energy"], "kWh"), "note": "已知值合计" if summary["energy_missing"] else "已结束充电合计"},
        {"label": "已记录充电费用", "value": fmt_number(summary["cost"], 2) if cost_known else "未知", "note": cost_coverage},
    ]
    opening = f"本期记录了 {_unit(summary['distance'], 'km')}，用车 {summary['active_days']} 天，出门 {summary['outing_count']} 次。"
    if not current_drives and not current_charges:
        opening = "本期没有已结束的行程或充电记录。"
    return {
        "version": 1, "kind": period.kind, "title": period.title, "kind_label": KINDS[period.kind],
        "period_text": f"{fmt_local(period.start)} 至 {fmt_local(period.end)}（不含结束时刻）",
        "generated_text": fmt_local(now), "coverage": coverage, "opening": opening,
        "metrics": metrics, "trend": trend, "highlights": highlights,
        "comparisons": _comparison(summary, before, first_record is not None and first_record <= previous.start),
        "previous_text": f"{fmt_local(previous.start)} 至 {fmt_local(previous.end)}",
        "charge_count": summary["charge_count"], "charge_duration": fmt_minutes(summary["charge_duration"]),
        "cost_coverage": cost_coverage, "places": [{"name": name, "count": count} for name, count in places.most_common(5)],
        "cities": cities if period.kind == "year" else [], "notes": notes,
        "notification_body": f"{_unit(summary['distance'], 'km')}，用车 {summary['active_days']} 天，出门 {summary['outing_count']} 次，充入 {_unit(summary['energy'], 'kWh')}",
    }


class ReportService:
    def __init__(self, settings: Settings, store: Store, teslamate) -> None:
        self.settings = settings
        self.store = store
        self.teslamate = teslamate

    def _cursor_key(self, kind: str) -> str:
        return f"report_cursor_{self.settings.car_id}_{kind}"

    def initialize(self, now: datetime) -> None:
        if not self.settings.reports_enabled:
            return
        for kind in KINDS:
            key = self._cursor_key(kind)
            if self.store.get_meta(key) is None:
                self.store.set_meta(key, latest_due_period(kind, now, self.settings.report_hour).start.isoformat())

    def generate(self, period: Period, now: datetime, *, enqueue: bool = False, cursor_key: str | None = None) -> str:
        if period.end > now:
            raise ValueError("只能生成已经结束的自然周期报告")
        existing = self.store.find_report(self.settings.car_id, period.kind, period.start)
        snapshot = {}
        if existing is None:
            drives, charges, first = self.teslamate.report_records(self.settings.car_id, previous_period(period).start, period.end)
            snapshot = build_report(period, drives, charges, first, self.settings, now)
        return self.store.save_report(
            token=existing or secrets.token_urlsafe(16), car_id=self.settings.car_id, kind=period.kind,
            start=period.start, end=period.end, snapshot=snapshot, created_at=now,
            expires_at=expiry_from(now, self.settings.trip_link_ttl_days), enqueue=enqueue, cursor_key=cursor_key,
        )

    def generate_due(self, now: datetime) -> None:
        if not self.settings.reports_enabled:
            return
        for kind in KINDS:
            period = latest_due_period(kind, now, self.settings.report_hour)
            key = self._cursor_key(kind)
            cursor = self.store.get_meta(key)
            if cursor is not None and datetime.fromisoformat(cursor) < period.start:
                self.generate(period, now, enqueue=True, cursor_key=key)

    def next_due(self, now: datetime) -> datetime:
        moments = []
        for kind in KINDS:
            latest = latest_due_period(kind, now, self.settings.report_hour)
            following = period_for(kind, latest.end)
            moments.append(following.end + timedelta(hours=self.settings.report_hour))
        return min(moments)
