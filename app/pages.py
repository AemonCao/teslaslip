"""把只读行收成手机页。坐标在这里转成 GCJ-02。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote

from app.charge import summarize_samples
from app.config import Settings
from app.formatutil import fmt_elapsed, fmt_local, fmt_minutes, fmt_number, fmt_optional_int, fmt_signed
from app.geo import downsample, wgs84_to_gcj02
from app.models import Charge, Drive, TrackPoint
from app.outing import is_shuffle

MAP_POINT_LIMIT = 2000


@dataclass
class MapSegment:
    kind: str
    dashed: bool
    latlng: list[list[float]]
    path: list[list[float]]
    svg_points: str
    label: str
    label_x: float = 0
    label_y: float = 0


@dataclass
class SegmentRow:
    time_text: str
    distance_text: str
    duration_text: str
    start_text: str
    end_text: str
    shuffle_text: str


@dataclass
class Stat:
    label: str
    value: str


def build_drive_page(
    drives: list[Drive],
    tracks: list[list[TrackPoint]],
    destination: str | None,
    settings: Settings,
) -> dict[str, object]:
    pairs = sorted(zip(drives, tracks, strict=True), key=lambda item: (item[0].end_date, item[0].id))
    ordered = [drive for drive, _points in pairs]
    full_lists = [sorted(points, key=lambda point: point.date) for _drive, points in pairs]
    map_lists = [downsample(points, MAP_POINT_LIMIT) for points in full_lists]
    by_id = {drive.id: points for drive, points in zip(ordered, map_lists, strict=True)}
    map_segments = _map_segments(ordered, by_id)
    flat_points = [point for points in full_lists for point in points]
    start_battery = _nearby_battery(full_lists[0] if full_lists else [], first=True)
    end_battery = _nearby_battery(full_lists[-1] if full_lists else [], first=False)
    tires = _last_tires(flat_points)
    climate = _climate(flat_points)
    distance = _sum(drive.distance_km for drive in ordered)
    driving = _sum(drive.duration_min for drive in ordered)
    elapsed = ordered[-1].end_date - ordered[0].start_date
    rated_start = ordered[0].start_rated_range_km
    rated_end = ordered[-1].end_rated_range_km
    ideal_start = ordered[0].start_ideal_range_km
    ideal_end = ordered[-1].end_ideal_range_km
    stats = [
        Stat("总里程", _km(distance)),
        Stat("驾驶时长", fmt_minutes(driving)),
        Stat("出门耗时", fmt_elapsed(elapsed)),
        Stat("最高速度", _unit(_max(drive.speed_max for drive in ordered), "km/h", 0)),
        Stat("最大输出", _unit(_max(drive.power_max for drive in ordered), "kW", 0)),
        Stat("最大回收", _unit(_min(drive.power_min for drive in ordered), "kW", 0)),
        Stat("起点里程表", _km(ordered[0].start_km)),
        Stat("终点里程表", _km(ordered[-1].end_km)),
        Stat("起点额定续航", _km(rated_start)),
        Stat("终点额定续航", _km(rated_end)),
        Stat("额定续航差", _signed_km(_delta(rated_start, rated_end))),
        Stat("起点理想续航", _km(ideal_start)),
        Stat("终点理想续航", _km(ideal_end)),
        Stat("理想续航差", _signed_km(_delta(ideal_start, ideal_end))),
        Stat("起点电量", _percent(start_battery[0])),
        Stat("起点可用电量", _percent(start_battery[1])),
        Stat("终点电量", _percent(end_battery[0])),
        Stat("终点可用电量", _percent(end_battery[1])),
        Stat("车外温度", _temp(_weighted_temp(ordered, "outside_temp_avg"))),
        Stat("车内温度", _temp(_weighted_temp(ordered, "inside_temp_avg"))),
        Stat("爬升", _unit(_sum(drive.ascent for drive in ordered), "m", 0)),
        Stat("下降", _unit(_sum(drive.descent for drive in ordered), "m", 0)),
    ]
    rows = None
    if len(ordered) > 1:
        rows = [
            SegmentRow(
                time_text=f"{fmt_local(drive.start_date)} – {fmt_local(drive.end_date)}",
                distance_text=_km(drive.distance_km),
                duration_text=fmt_minutes(drive.duration_min),
                start_text=_place(drive.start_geofence, drive.start_address.parts()),
                end_text=_place(drive.end_geofence, drive.end_address.parts()),
                shuffle_text="是" if is_shuffle(
                    drive, settings.min_push_distance_km, settings.min_push_duration_min
                ) else "否",
            )
            for drive in ordered
        ]
    return {
        "start_text": fmt_local(ordered[0].start_date),
        "end_text": fmt_local(ordered[-1].end_date),
        "distance_text": _km(distance),
        "driving_text": fmt_minutes(driving),
        "elapsed_text": fmt_elapsed(elapsed),
        "start_geofence": ordered[0].start_geofence or "—",
        "end_geofence": ordered[-1].end_geofence or "—",
        "start_address": _address_text(ordered[0].start_address.parts()),
        "end_address": _address_text(ordered[-1].end_address.parts()),
        "stats": stats,
        "segments": rows,
        "map_segments": map_segments,
        "map_json": json.dumps(
            [
                {"kind": segment.kind, "dashed": segment.dashed, "path": segment.path, "label": segment.label}
                for segment in map_segments
            ],
            ensure_ascii=False,
        ),
        "has_track": any(segment.kind == "drive" and segment.latlng for segment in map_segments),
        "amap_key": settings.amap_js_key,
        "amap_src": _amap_src(settings.amap_js_key),
        "security_js": json.dumps(settings.amap_security_js_code),
        "show_security": bool(settings.amap_security_js_code),
        "destination": destination or None,
        "tire_text": _tire_text(tires),
        "climate_lines": climate,
        "svg_width": 1000,
        "svg_height": 700,
    }


def build_charge_page(charge: Charge, settings: Settings) -> dict[str, object]:
    summary = summarize_samples(charge.samples)
    point = None
    if charge.latitude is not None and charge.longitude is not None:
        lat, lng = wgs84_to_gcj02(charge.latitude, charge.longitude)
        point = {"lat": lat, "lng": lng, "path": [lng, lat]}
    elapsed = charge.end_date - charge.start_date
    duration = charge.duration_min
    if duration is None:
        duration = elapsed.total_seconds() / 60.0
    address_parts = charge.address.parts()
    stats = [
        Stat("开始", fmt_local(charge.start_date)),
        Stat("结束", fmt_local(charge.end_date)),
        Stat("时长", fmt_minutes(duration)),
        Stat("加入电量", _unit(charge.charge_energy_added, "kWh", 1)),
        Stat("充电消耗", _unit(charge.charge_energy_used, "kWh", 1)),
        Stat("起点电量", _percent(charge.start_battery_level)),
        Stat("结束电量", _percent(charge.end_battery_level)),
        Stat("起点额定续航", _km(charge.start_rated_range_km)),
        Stat("终点额定续航", _km(charge.end_rated_range_km)),
        Stat("起点理想续航", _km(charge.start_ideal_range_km)),
        Stat("终点理想续航", _km(charge.end_ideal_range_km)),
        Stat("车外温度", _temp(charge.outside_temp_avg)),
        Stat("地点围栏", charge.geofence or "—"),
        Stat("地址", _address_text(address_parts)),
    ]
    if charge.cost is not None:
        stats.append(Stat("费用", fmt_number(charge.cost, 2)))
    charger = [
        Stat("充电器功率", _sample_unit(summary.power_max, "kW", 0)),
        Stat("电压", _sample_unit(summary.voltage_max, "V", 0)),
        Stat("电流", _sample_unit(summary.current_max, "A", 0)),
        Stat("相数", _sample_unit(summary.phases_max, "", 0)),
        Stat("线缆", summary.cable or "—"),
        Stat("快充", "有" if summary.fast_present else "无"),
        Stat("快充品牌", summary.fast_brand or "—"),
        Stat("快充类型", summary.fast_type or "—"),
    ]
    return {
        "title": charge.geofence or "充电完成",
        "stats": stats,
        "charger": charger,
        "sample_count": summary.sample_count,
        "point": point,
        "point_json": json.dumps(point, ensure_ascii=False),
        "amap_key": settings.amap_js_key,
        "amap_src": _amap_src(settings.amap_js_key),
        "security_js": json.dumps(settings.amap_security_js_code),
        "show_security": bool(settings.amap_security_js_code),
        "address": _address_text(address_parts),
    }


def _map_segments(drives: list[Drive], by_id: dict[int, list[TrackPoint]]) -> list[MapSegment]:
    drive_segments: list[MapSegment] = []
    for drive in drives:
        latlng = []
        path = []
        for point in by_id.get(drive.id, []):
            lat, lng = wgs84_to_gcj02(point.latitude, point.longitude)
            latlng.append([lat, lng])
            path.append([lng, lat])
        drive_segments.append(
            MapSegment(
                kind="drive",
                dashed=False,
                latlng=latlng,
                path=path,
                svg_points="",
                label="",
            )
        )
    visual: list[MapSegment] = []
    for index, segment in enumerate(drive_segments):
        visual.append(segment)
        if index + 1 >= len(drive_segments):
            continue
        nxt = drive_segments[index + 1]
        if not segment.latlng or not nxt.latlng:
            continue
        visual.append(
            MapSegment(
                kind="parking",
                dashed=True,
                latlng=[segment.latlng[-1], nxt.latlng[0]],
                path=[segment.path[-1], nxt.path[0]],
                svg_points="",
                label="停车",
            )
        )
    _project(visual)
    return visual


def _project(segments: list[MapSegment]) -> None:
    points = [pair for segment in segments for pair in segment.latlng]
    if not points:
        return
    lats = [pair[0] for pair in points]
    lngs = [pair[1] for pair in points]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)
    if max_lat == min_lat:
        max_lat += 0.001
        min_lat -= 0.001
    if max_lng == min_lng:
        max_lng += 0.001
        min_lng -= 0.001
    width, height = 1000, 700

    def xy(lat: float, lng: float) -> tuple[float, float]:
        x = (lng - min_lng) / (max_lng - min_lng) * (width - 48) + 24
        y = (max_lat - lat) / (max_lat - min_lat) * (height - 48) + 24
        return x, y

    for segment in segments:
        rendered = [xy(lat, lng) for lat, lng in segment.latlng]
        segment.svg_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in rendered)
        if segment.label and rendered:
            mid = rendered[len(rendered) // 2]
            segment.label_x = mid[0]
            segment.label_y = mid[1]


def _nearby_battery(points: list[TrackPoint], *, first: bool) -> tuple[int | None, int | None]:
    sequence = points if first else list(reversed(points))
    for point in sequence:
        if point.battery_level is not None or point.usable_battery_level is not None:
            return point.battery_level, point.usable_battery_level
    return None, None


def _last_tires(points: list[TrackPoint]) -> dict[str, float | None]:
    last: dict[str, float | None] = {"fl": None, "fr": None, "rl": None, "rr": None}
    for point in points:
        if point.tpms_fl is not None:
            last["fl"] = point.tpms_fl
        if point.tpms_fr is not None:
            last["fr"] = point.tpms_fr
        if point.tpms_rl is not None:
            last["rl"] = point.tpms_rl
        if point.tpms_rr is not None:
            last["rr"] = point.tpms_rr
    return last


def _tire_text(tires: dict[str, float | None]) -> str:
    if all(value is None for value in tires.values()):
        return "本段没有胎压采样"
    names = (("fl", "左前"), ("fr", "右前"), ("rl", "左后"), ("rr", "右后"))
    parts = [f"{label} {fmt_number(tires[key], 2)} bar" if tires[key] is not None else f"{label} —" for key, label in names]
    return " · ".join(parts)


def _climate(points: list[TrackPoint]) -> list[str]:
    used = any(point.climate_on for point in points)
    driver = _range(points, "driver_temp")
    passenger = _range(points, "passenger_temp")
    front = any(point.front_defrost for point in points)
    rear = any(point.rear_defrost for point in points)
    return [
        f"空调：{'开过' if used else '未开'}",
        f"主驾设定：{_temp_range(driver)}",
        f"副驾设定：{_temp_range(passenger)}",
        f"前除霜：{'出现过' if front else '没有'}",
        f"后除霜：{'出现过' if rear else '没有'}",
    ]


def _range(points: list[TrackPoint], name: str) -> tuple[float | None, float | None]:
    values = [getattr(point, name) for point in points if getattr(point, name) is not None]
    if not values:
        return None, None
    return min(values), max(values)


def _temp_range(pair: tuple[float | None, float | None]) -> str:
    low, high = pair
    if low is None or high is None:
        return "—"
    if float(low) == float(high):
        return f"{fmt_number(low)} °C"
    return f"{fmt_number(low)}–{fmt_number(high)} °C"


def _weighted_temp(drives: list[Drive], name: str) -> float | None:
    if len(drives) == 1:
        return getattr(drives[0], name)
    numerator = 0.0
    denominator = 0.0
    for drive in drives:
        value = getattr(drive, name)
        weight = drive.duration_min
        if value is None or weight is None:
            continue
        numerator += float(value) * float(weight)
        denominator += float(weight)
    if denominator == 0:
        return None
    return numerator / denominator


def _place(geofence: str | None, address_parts: list[str]) -> str:
    if geofence:
        return geofence
    if address_parts:
        return " · ".join(address_parts)
    return "—"


def _address_text(parts: list[str]) -> str:
    return " · ".join(parts) if parts else "—"


def _km(value: float | None) -> str:
    return _unit(value, "km", 1)


def _signed_km(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{fmt_signed(value)} km"


def _percent(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{int(value)}%"


def _temp(value: float | None) -> str:
    return _unit(value, "°C", 1)


def _unit(value: float | None, unit: str, digits: int) -> str:
    if value is None:
        return "—"
    number = fmt_number(value, digits) if digits else fmt_optional_int(value)
    return f"{number} {unit}".strip()


def _sample_unit(value: float | None, unit: str, digits: int) -> str:
    rendered = _unit(value, unit, digits)
    if value is None:
        return rendered
    return f"{rendered}（采样值）"


def _sum(values) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return sum(present)


def _max(values) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return max(present)


def _min(values) -> float | None:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return min(present)


def _delta(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return float(end) - float(start)


def _amap_src(key: str) -> str:
    if not key:
        return ""
    return f"https://webapi.amap.com/maps?v=2.0&key={quote(key, safe='')}"


def link_expired(expires_at, now) -> bool:
    if expires_at is None:
        return False
    return now > expires_at


def expiry_from(created, ttl_days: int):
    if ttl_days <= 0:
        return None
    return created + timedelta(days=ttl_days)
