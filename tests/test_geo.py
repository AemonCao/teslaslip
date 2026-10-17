from app.geo import downsample, wgs84_to_gcj02


def test_outside_china_is_unchanged():
    lat, lng = wgs84_to_gcj02(40.7128, -74.0060)
    assert lat == 40.7128
    assert lng == -74.0060


def test_inside_china_changes_both_axes_without_swapping():
    raw_lat, raw_lng = 31.2304, 121.4737
    lat, lng = wgs84_to_gcj02(raw_lat, raw_lng)
    assert lat != raw_lat
    assert lng != raw_lng
    assert abs(lat - raw_lat) < 0.2
    assert abs(lng - raw_lng) < 0.2
    assert lat < 40
    assert lng > 100


def test_downsample_keeps_ends_and_limit():
    points = list(range(2001))
    sampled = downsample(points, 2000)
    assert len(sampled) <= 2000
    assert sampled[0] == 0
    assert sampled[-1] == 2000
    assert sampled == sorted(sampled)
