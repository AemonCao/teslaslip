"""WGS-84 转 GCJ-02。中国境外的点原样返回，纬度和经度不对调。"""

from __future__ import annotations

import math
from typing import TypeVar

# 公开的 GCJ-02 椭球参数。
A = 6378245.0
EE = 0.00669342162296594323

T = TypeVar("T")


def out_of_china(latitude: float, longitude: float) -> bool:
    return not (73.66 < longitude < 135.05 and 3.86 < latitude < 53.55)


def wgs84_to_gcj02(latitude: float, longitude: float) -> tuple[float, float]:
    """返回 (纬度, 经度)。"""
    lat = float(latitude)
    lng = float(longitude)
    if out_of_china(lat, lng):
        return lat, lng
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrt_magic) * math.pi)
    dlng = (dlng * 180.0) / (A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lat + dlat, lng + dlng


def _transform_lat(lng: float, lat: float) -> float:
    ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi) + 320.0 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(lng: float, lat: float) -> float:
    ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * math.pi) + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * math.pi) + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def downsample(points: list[T], limit: int = 2000) -> list[T]:
    """均匀抽稀，并保留首尾点。"""
    count = len(points)
    if limit < 1 or count <= limit:
        return list(points)
    if limit == 1:
        return [points[0]]
    selected = _uniform_indices(count, limit)
    return [points[index] for index in selected]


def _uniform_indices(count: int, limit: int) -> list[int]:
    last = count - 1
    chosen: list[int] = []
    seen: set[int] = set()
    for step in range(limit):
        index = round(step * last / (limit - 1))
        if index not in seen:
            seen.add(index)
            chosen.append(index)
    if not chosen or chosen[0] != 0:
        chosen.insert(0, 0)
    if chosen[-1] != last:
        chosen.append(last)
    if len(chosen) <= limit:
        return chosen
    middle = chosen[1:-1]
    budget = limit - 2
    if budget <= 0:
        return [0, last]
    picked: list[int] = []
    used: set[int] = set()
    span = max(len(middle) - 1, 1)
    for step in range(budget):
        index = 0 if budget == 1 else round(step * span / (budget - 1))
        index = min(index, len(middle) - 1)
        if index not in used:
            used.add(index)
            picked.append(middle[index])
    return [0, *picked, last]
