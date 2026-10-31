"""有范围的只读采样整理；统计先于绘图抽稀。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from statistics import median

from app.formatutil import as_utc
from app.models import Charge, ChargeSample, Drive, TrackPoint

POINT_LIMIT = 2000
Sample = TrackPoint | ChargeSample
Point = list[int | float]


@dataclass(frozen=True)
class Metric:
    key: str
    name: str
    unit: str
    group: str
    style: str = "line"
    extra: bool = False
    note: str = ""
    range_mode: str | None = None


DRIVE_METRICS = (
    Metric("speed", "速度", "km/h", "speed"),
    Metric("power", "输出／回收功率", "kW", "power", "power", note="正值为输出，负值为回收；不能据此判断机械刹车。"),
    Metric("battery_level", "电量", "%", "battery", "step"),
    Metric("elevation", "海拔", "m", "elevation", extra=True, note="TeslaMate 位置采样；缺口断线，不用于反推高精度坡度。"),
    Metric("inside_temp", "车内温度", "°C", "temperature", extra=True),
    Metric("outside_temp", "车外温度", "°C", "temperature", extra=True),
    Metric("tpms_fl", "左前胎压", "bar", "tires", extra=True),
    Metric("tpms_fr", "右前胎压", "bar", "tires", extra=True),
    Metric("tpms_rl", "左后胎压", "bar", "tires", extra=True),
    Metric("tpms_rr", "右后胎压", "bar", "tires", extra=True),
    Metric("rated_range", "额定续航", "km", "range", extra=True, range_mode="rated"),
    Metric("ideal_range", "理想续航", "km", "range", extra=True, range_mode="ideal"),
    Metric("climate_on", "空调", "状态", "climate_on", "state", True),
    Metric("front_defrost", "前除霜", "状态", "front_defrost", "state", True),
    Metric("rear_defrost", "后除霜", "状态", "rear_defrost", "state", True),
    Metric("battery_heater_on", "电池加热", "状态", "battery_heater_on", "state", True),
)

CHARGE_METRICS = (
    Metric("charger_power", "充电功率", "kW", "charger_power"),
    Metric("battery_level", "电量", "%", "battery", "step"),
    Metric("charge_energy_added", "累计充入电量", "kWh", "energy", note="原始累计采样，不等同于会话汇总或充电损耗；下降／重置时断线并标注异常。"),
    Metric("charger_voltage", "充电电压", "V", "voltage", extra=True),
    Metric("charger_current", "实际电流", "A", "current", extra=True),
    Metric("outside_temp", "车外温度", "°C", "temperature", extra=True),
    Metric("battery_heater_on", "电池加热", "状态", "battery_heater_on", "state", True),
)


def millis(value: datetime) -> int:
    return round(as_utc(value).timestamp() * 1000)


def safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace(
        "<", "\\u003c"
    ).replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _value(sample: Sample, metric: Metric) -> float | None:
    value = getattr(sample, metric.key, None)
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if metric.key == "speed" and value < 0:
        return None
    if metric.key == "battery_level" and not 0 <= value <= 100:
        return None
    return float(value)


def _series(samples: list[Sample], metric: Metric) -> tuple[list[list[Point]], list[int], int]:
    values: dict[int, float] = {}
    missing_samples: list[Sample] = []
    for sample in samples:
        value = _value(sample, metric)
        if value is not None:
            values[millis(sample.date)] = value
        elif metric.style == "state":
            missing_samples.append(sample)
    if not values:
        return [], [], 30000
    ordered = [[stamp, value] for stamp, value in sorted(values.items())]
    intervals = [right[0] - left[0] for left, right in zip(ordered, ordered[1:])]
    threshold = max(30000, round(median(intervals) * 5)) if intervals else 30000
    parts: list[list[Point]] = []
    anomalies: list[int] = []
    unknown = sorted({millis(sample.date) for sample in missing_samples} - values.keys())
    missing_index = 0
    for point in ordered:
        previous = parts[-1][-1] if parts else None
        reset = previous is not None and metric.key == "charge_energy_added" and point[1] < previous[1]
        state_gap = False
        if previous is not None and metric.style == "state":
            while missing_index < len(unknown) and unknown[missing_index] <= previous[0]:
                missing_index += 1
            state_gap = missing_index < len(unknown) and unknown[missing_index] < point[0]
        if previous is None or point[0] - previous[0] > threshold or reset or state_gap:
            parts.append([])
        if reset:
            anomalies.append(int(point[0]))
        parts[-1].append(point)
    return parts, anomalies, threshold


def _thin(points: list[Point], budget: int) -> list[Point]:
    if len(points) <= budget:
        return points
    bucket_count = max(1, (budget - 2) // 4)
    start, end = points[0][0], points[-1][0]
    buckets: dict[int, list[Point]] = {}
    for point in points[1:-1]:
        index = min(bucket_count - 1, int((point[0] - start) / max(1, end - start) * bucket_count))
        buckets.setdefault(index, []).append(point)
    kept = {points[0][0]: points[0], points[-1][0]: points[-1]}
    for bucket in buckets.values():
        for point in (bucket[0], min(bucket, key=lambda point: point[1]), max(bucket, key=lambda point: point[1]), bucket[-1]):
            kept[point[0]] = point
    return [kept[stamp] for stamp in sorted(kept)]


def _metric(metric: Metric, segments: list[dict], samples: list[list[Sample]]) -> dict:
    chunks = []
    anomalies = []
    raw = []
    for segment, rows in zip(segments, samples, strict=True):
        parts, resets, threshold = _series(rows, metric)
        anomalies.extend(resets)
        for part in parts:
            raw.extend(part)
            chunks.append({"segment": segment["index"], "threshold": threshold, "points": part})
    total = len(raw)
    for chunk in chunks:
        points = chunk["points"]
        thinned = _thin(points, max(6, int(POINT_LIMIT * len(points) / max(1, total))))
        if metric.style in {"step", "state"} and len(thinned) < len(points):
            transitions = {point[0]: point for point in thinned}
            for left, right in zip(points, points[1:]):
                if left[1] != right[1]:
                    transitions[left[0]] = left
                    transitions[right[0]] = right
            thinned = [transitions[stamp] for stamp in sorted(transitions)]
        chunk["points"] = thinned
    return {
        "key": metric.key, "name": metric.name, "unit": metric.unit, "group": metric.group,
        "style": metric.style, "extra": metric.extra, "note": metric.note, "rangeMode": metric.range_mode,
        "chunks": chunks, "count": total,
        "min": min((point[1] for point in raw), default=None),
        "max": max((point[1] for point in raw), default=None),
        "anomalies": anomalies,
    }


SPEED_BINS = ("静止 0", "低速 (0, 20)", "[20, 40)", "[40, 60)", "[60, 80)", "[80, 100)", "≥100")


def speed_distribution(segments: list[dict], samples: list[list[Sample]]) -> dict:
    durations = [0.0] * len(SPEED_BINS)
    waiting = []
    total = sum(max(0, segment["end"] - segment["start"]) for segment in segments)
    for segment, rows in zip(segments, samples, strict=True):
        parts, _resets, _threshold = _series(rows, DRIVE_METRICS[0])
        for part in parts:
            wait_start = None
            wait_end = None
            for left, right in zip(part, part[1:]):
                speed = left[1]
                index = 0 if speed == 0 else 1 if speed < 20 else min(6, int(speed // 20) + 1)
                durations[index] += right[0] - left[0]
                if speed == 0 and right[1] == 0:
                    if wait_start is None:
                        wait_start = left[0]
                    wait_end = right[0]
                elif wait_start is not None:
                    if wait_end - wait_start >= 30000:
                        waiting.append({"segment": segment["index"], "start": wait_start, "end": wait_end, "seconds": (wait_end - wait_start) / 1000})
                    wait_start = None
            if wait_start is not None and wait_end - wait_start >= 30000:
                waiting.append({"segment": segment["index"], "start": wait_start, "end": wait_end, "seconds": (wait_end - wait_start) / 1000})
    covered = sum(durations)
    return {
        "bins": [{"label": label, "seconds": duration / 1000, "percent": duration / covered * 100 if covered else 0} for label, duration in zip(SPEED_BINS, durations, strict=True)],
        "coveredSeconds": covered / 1000, "uncoveredSeconds": max(0, total - covered) / 1000,
        "waiting": waiting,
    }


def _payload(kind: str, segments: list[dict], samples: list[list[Sample]], metrics: tuple[Metric, ...]) -> dict:
    return {
        "kind": kind, "start": min(segment["start"] for segment in segments),
        "end": max(segment["end"] for segment in segments), "segments": segments,
        "metrics": [_metric(metric, segments, samples) for metric in metrics],
        "distribution": speed_distribution(segments, samples) if kind == "drive" else None,
    }


def drive_charts(drives: list[Drive], tracks: list[list[TrackPoint]]) -> dict:
    pairs = sorted(zip(drives, tracks, strict=True), key=lambda pair: (pair[0].start_date, pair[0].id))
    segments = [{"index": index, "start": millis(drive.start_date), "end": millis(drive.end_date)} for index, (drive, _rows) in enumerate(pairs, 1)]
    samples = [[point for point in rows if as_utc(drive.start_date) <= as_utc(point.date) <= as_utc(drive.end_date)] for drive, rows in pairs]
    return _payload("drive", segments, samples, DRIVE_METRICS)


def charge_charts(charge: Charge) -> dict:
    segments = [{"index": 1, "start": millis(charge.start_date), "end": millis(charge.end_date)}]
    samples = [[sample for sample in charge.samples if as_utc(charge.start_date) <= as_utc(sample.date) <= as_utc(charge.end_date)]]
    return _payload("charge", segments, samples, CHARGE_METRICS)
