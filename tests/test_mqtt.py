import json

from app.mqtt import MqttSession, WaitForDriveClose, topic_map


def test_topics_skip_high_frequency_and_honor_namespace():
    plain = topic_map(1, "")
    assert set(plain) == {
        "teslamate/cars/1/state",
        "teslamate/cars/1/charging_state",
        "teslamate/cars/1/active_route",
    }
    namespaced = topic_map(1, "prod")
    assert "teslamate/prod/cars/1/state" in namespaced
    joined = " ".join(plain)
    assert "speed" not in joined
    assert "power" not in joined
    assert "latitude" not in joined


def test_retained_baseline_is_not_an_edge_and_later_change_is():
    session = MqttSession()
    session.begin_baseline()
    assert session.on_message("state", "online") == []
    actions = session.on_message("charging_state", "Disconnected")
    assert WaitForDriveClose not in [type(item) for item in actions]
    assert session.on_message("state", "driving") 
    parked = session.on_message("state", "online")
    assert any(isinstance(item, WaitForDriveClose) for item in parked)


def test_route_is_remembered_only_while_driving_and_error_null():
    session = MqttSession(state="online")
    session.on_message("active_route", json.dumps({"destination": "朝阳路10号", "error": None}))
    assert session.destination is None
    session.on_message("state", "driving")
    session.on_message("active_route", json.dumps({"destination": "朝阳路10号", "error": "no_route"}))
    assert session.destination is None
    session.on_message("active_route", json.dumps({"destination": "朝阳路10号", "error": None}))
    assert session.destination == "朝阳路10号"
    session.on_message("active_route", json.dumps({"destination": "", "error": None}))
    assert session.destination == "朝阳路10号"


def test_stopped_is_a_charge_edge_after_baseline():
    session = MqttSession()
    session.begin_baseline()
    session.on_message("state", "charging")
    session.on_message("charging_state", "Charging")
    actions = session.on_message("charging_state", "Stopped")
    assert actions
