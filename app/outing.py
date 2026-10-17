"""出门分组、挪车判断、安静期计划。纯函数，不查库。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, Sequence


class DriveLike(Protocol):
    id: int
    start_date: datetime
    end_date: datetime
    distance_km: float | None
    duration_min: float | None


def is_shuffle(drive: DriveLike, min_distance_km: float, min_duration_min: float) -> bool:
    """两道门槛都要满足。时长为空时不是挪车。"""
    if drive.duration_min is None or drive.distance_km is None:
        return False
    return float(drive.distance_km) < float(min_distance_km) and float(drive.duration_min) < float(
        min_duration_min
    )


def is_pure_shuffle(drives: Sequence[DriveLike], min_distance_km: float, min_duration_min: float) -> bool:
    if not drives:
        return False
    return all(is_shuffle(drive, min_distance_km, min_duration_min) for drive in drives)


def group_outings(drives: Sequence[DriveLike], gap_minutes: float) -> list[list[DriveLike]]:
    """按结束时间从旧到新分组。间隔小于阈值才并进同一串。"""
    ordered = sorted(drives, key=lambda drive: (drive.end_date, drive.id))
    if not ordered:
        return []
    gap = timedelta(minutes=gap_minutes)
    groups: list[list[DriveLike]] = [[ordered[0]]]
    for drive in ordered[1:]:
        previous = groups[-1][-1]
        if drive.start_date - previous.end_date < gap:
            groups[-1].append(drive)
        else:
            groups.append([drive])
    return groups


@dataclass(frozen=True)
class OutingPlan:
    ready: list[list[DriveLike]]
    waiting_deadline: datetime | None


def plan_outings(
    drives: Sequence[DriveLike],
    *,
    gap_minutes: float,
    now: datetime,
    is_driving: bool,
) -> OutingPlan:
    """从旧到新决定。安静期未到的那一串只给截止时间，后面的先不动。

    正在开车时，最后一串可能还没结束，不关单，也不另设定时器。
    """
    ready: list[list[DriveLike]] = []
    gap = timedelta(minutes=gap_minutes)
    groups = group_outings(drives, gap_minutes)
    for index, group in enumerate(groups):
        deadline = group[-1].end_date + gap
        is_last = index == len(groups) - 1
        if is_last and is_driving:
            return OutingPlan(ready=ready, waiting_deadline=None)
        if now < deadline:
            return OutingPlan(ready=ready, waiting_deadline=deadline)
        ready.append(group)
    return OutingPlan(ready=ready, waiting_deadline=None)


NEED_MORE = object()


def select_preview(
    drives_newest_first: Sequence[DriveLike],
    *,
    gap_minutes: float,
    min_distance_km: float,
    min_duration_min: float,
    exhausted: bool,
) -> list[DriveLike] | object | None:
    """最近一串不合格为纯挪车的已结束出门。数据被截断时返回 NEED_MORE。"""
    gap = timedelta(minutes=gap_minutes)
    chain: list[DriveLike] = []
    for older in drives_newest_first:
        if not chain:
            chain.append(older)
            continue
        if chain[-1].start_date - older.end_date < gap:
            chain.append(older)
            continue
        qualified = _qualified(chain, min_distance_km, min_duration_min)
        if qualified is not None:
            return qualified
        chain = [older]
    if not exhausted:
        return NEED_MORE
    return _qualified(chain, min_distance_km, min_duration_min)


def _qualified(
    chain_newest_first: Sequence[DriveLike],
    min_distance_km: float,
    min_duration_min: float,
) -> list[DriveLike] | None:
    chrono = list(reversed(chain_newest_first))
    if chrono and not is_pure_shuffle(chrono, min_distance_km, min_duration_min):
        return chrono
    return None


def initial_watermark(ended_ids: Sequence[int]) -> int:
    """已结束记录的最大 id。没有结束记录时是 0。"""
    if not ended_ids:
        return 0
    return max(ended_ids)


def advance_watermark(current: int, decided_ids: set[int], ended_ids_above: Sequence[int]) -> int:
    """只越过已经决定的连续 id，不跳过还没决定的已结束记录。"""
    watermark = current
    for drive_id in sorted(ended_ids_above):
        if drive_id not in decided_ids:
            break
        watermark = drive_id
    return watermark
