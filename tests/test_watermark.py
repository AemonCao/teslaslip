from app.outing import advance_watermark, initial_watermark


def test_initial_watermark_uses_ended_ids_only():
    assert initial_watermark([]) == 0
    assert initial_watermark([1, 3]) == 3


def test_advance_does_not_skip_undecided_ended_drive():
    assert advance_watermark(10, {11, 12}, [11, 12]) == 12
    assert advance_watermark(10, {12}, [11, 12]) == 10
    assert advance_watermark(10, {11}, [11, 12]) == 11


def test_open_drive_above_initial_watermark_stays_eligible():
    ended_ids = [1, 3]
    watermark = initial_watermark(ended_ids)
    open_id = 4
    assert open_id > watermark
    assert 2 not in ended_ids
    assert 2 < watermark
