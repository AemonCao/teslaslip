from datetime import datetime, timedelta, timezone

from app.bark import (
    drive_body,
    drive_title,
    envelope,
    notification_text,
    page_url,
    summarize_charge_notification,
    summarize_drive_notification,
)
from app.models import Address
from tests.fakes import NAV_DEST, STREET, make_charge, make_drive

T0 = datetime(2026, 10, 17, 2, 0, tzinfo=timezone.utc)


def test_drive_title_uses_fences_or_distance():
    assert drive_title("家", "公司", 12) == "家 → 公司"
    assert drive_title("公司", None, 12) == "公司"
    assert drive_title(None, None, 12.4) == "出门 12.4 km"


def test_bark_text_has_fences_and_numbers_but_not_street_or_destination():
    start = T0
    end = T0 + timedelta(minutes=28)
    row = make_drive(
        1,
        start,
        end,
        start_geofence="🏠",
        end_geofence="公司",
        start_address=Address(display_name=STREET, road="中关村大街", house_number="88号"),
        end_address=Address(display_name="陆家嘴环路1号", road="陆家嘴环路"),
    )
    title, body = summarize_drive_notification([row])
    text = notification_text(title, body)
    assert "🏠" in title
    assert "公司" in title
    assert "12.4 km" in body
    assert "28 分钟" in body
    assert "-10.0" in body
    assert "410.0" in body
    assert STREET not in text
    assert "中关村大街" not in text
    assert "陆家嘴" not in text
    assert NAV_DEST not in text
    payload = envelope("bark-device-secret", title, body, page_url("http://192.168.1.223:8791/", "d", "tok"))
    assert payload["group"] == "teslaslip"
    assert payload["isArchive"] == 1
    assert payload["level"] == "active"
    assert payload["url"] == "http://192.168.1.223:8791/d/tok"
    assert STREET not in payload["title"]
    assert STREET not in payload["body"]


def test_charge_bark_omits_street():
    charge = make_charge(
        3,
        T0,
        T0 + timedelta(minutes=42),
        geofence="地下车库",
        address=Address(display_name=STREET, road="中关村大街", house_number="88号"),
    )
    title, body = summarize_charge_notification(charge)
    assert title == "地下车库"
    assert "18.5 kWh" in body
    assert "42 分钟" in body
    assert "80%" in body
    assert STREET not in notification_text(title, body)
    untitled = make_charge(4, T0, T0 + timedelta(minutes=5), geofence=None)
    assert summarize_charge_notification(untitled)[0] == "充电完成"


def test_body_builder_has_no_place_argument():
    assert "路" not in drive_body(1, 2, -1, 3)
