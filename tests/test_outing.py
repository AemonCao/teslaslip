from datetime import datetime, timedelta, timezone

from app.models import Drive
from app.outing import (
    NEED_MORE,
    group_outings,
    is_pure_shuffle,
    is_shuffle,
    plan_outings,
    select_preview,
)

T0 = datetime(2026, 10, 17, 2, 0, tzinfo=timezone.utc)


def drive(drive_id, start, end, distance=5, duration=10):
    return Drive(
        id=drive_id,
        start_date=start,
        end_date=end,
        distance_km=distance,
        duration_min=duration,
    )


def test_gap_nine_groups_and_eleven_splits():
    first_end = T0
    nine = [
        drive(1, T0 - timedelta(minutes=20), first_end),
        drive(2, first_end + timedelta(minutes=9), first_end + timedelta(minutes=20)),
    ]
    eleven = [
        drive(1, T0 - timedelta(minutes=20), first_end),
        drive(2, first_end + timedelta(minutes=11), first_end + timedelta(minutes=20)),
    ]
    assert len(group_outings(nine, 10)) == 1
    assert len(group_outings(eleven, 10)) == 2


def test_gap_of_exactly_ten_minutes_splits():
    first_end = T0
    drives = [
        drive(1, T0 - timedelta(minutes=20), first_end),
        drive(2, first_end + timedelta(minutes=10), first_end + timedelta(minutes=20)),
    ]
    assert len(group_outings(drives, 10)) == 2


def test_shuffle_needs_both_thresholds_and_duration():
    short = drive(1, T0, T0 + timedelta(minutes=1), distance=0.2, duration=1)
    long_time = drive(2, T0, T0 + timedelta(minutes=5), distance=0.2, duration=5)
    long_distance = drive(3, T0, T0 + timedelta(minutes=1), distance=2, duration=1)
    missing_duration = drive(4, T0, T0 + timedelta(minutes=1), distance=0.1, duration=None)
    exact = drive(5, T0, T0 + timedelta(minutes=2), distance=0.3, duration=2)
    assert is_shuffle(short, 0.3, 2)
    assert not is_shuffle(long_time, 0.3, 2)
    assert not is_shuffle(long_distance, 0.3, 2)
    assert not is_shuffle(missing_duration, 0.3, 2)
    assert not is_shuffle(exact, 0.3, 2)


def test_pure_shuffle_requires_every_segment():
    shuffle = drive(1, T0, T0 + timedelta(minutes=1), distance=0.1, duration=1)
    real = drive(2, T0, T0 + timedelta(minutes=5), distance=0.1, duration=5)
    assert is_pure_shuffle([shuffle, shuffle], 0.3, 2)
    assert not is_pure_shuffle([shuffle, real], 0.3, 2)


def test_plan_waits_for_quiet_and_skips_while_driving():
    end = T0
    rows = [drive(1, end - timedelta(minutes=15), end)]
    waiting = plan_outings(rows, gap_minutes=10, now=end + timedelta(minutes=9), is_driving=False)
    assert waiting.ready == []
    assert waiting.waiting_deadline == end + timedelta(minutes=10)
    ready = plan_outings(rows, gap_minutes=10, now=end + timedelta(minutes=10), is_driving=False)
    assert len(ready.ready) == 1
    held = plan_outings(rows, gap_minutes=10, now=end + timedelta(minutes=30), is_driving=True)
    assert held.ready == []
    assert held.waiting_deadline is None


def test_preview_skips_newer_shuffle():
    real = drive(1, T0, T0 + timedelta(minutes=20), distance=8, duration=20)
    shuffle = drive(3, T0 + timedelta(hours=2), T0 + timedelta(hours=2, minutes=1), distance=0.1, duration=1)
    selected = select_preview(
        [shuffle, real],
        gap_minutes=10,
        min_distance_km=0.3,
        min_duration_min=2,
        exhausted=True,
    )
    assert [item.id for item in selected] == [1]


def test_preview_asks_for_more_when_truncated():
    only = drive(1, T0, T0 + timedelta(minutes=20), distance=8, duration=20)
    assert (
        select_preview(
            [only],
            gap_minutes=10,
            min_distance_km=0.3,
            min_duration_min=2,
            exhausted=False,
        )
        is NEED_MORE
    )
