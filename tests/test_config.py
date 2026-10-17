import pytest

from app.config import ConfigError, Settings


def test_missing_required_env_names_the_variable(monkeypatch):
    for name in (
        "PUBLIC_BASE_URL",
        "DATABASE_URL",
        "MQTT_USERNAME",
        "MQTT_PASSWORD",
        "BARK_DEVICE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://teslaslip_ro:x@127.0.0.1/teslamate")
    monkeypatch.setenv("MQTT_USERNAME", "user")
    monkeypatch.setenv("MQTT_PASSWORD", "pw")
    monkeypatch.setenv("BARK_DEVICE_KEY", "key")
    with pytest.raises(ConfigError, match="PUBLIC_BASE_URL"):
        Settings.from_env()


def test_defaults_and_blank_ttl(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://192.168.1.223:8791/")
    monkeypatch.setenv("DATABASE_URL", "postgresql://teslaslip_ro:x@192.168.1.223:45432/teslamate")
    monkeypatch.setenv("MQTT_USERNAME", "user")
    monkeypatch.setenv("MQTT_PASSWORD", "pw")
    monkeypatch.setenv("BARK_DEVICE_KEY", "key")
    monkeypatch.setenv("TRIP_LINK_TTL_DAYS", "")
    monkeypatch.delenv("AMAP_JS_KEY", raising=False)
    settings = Settings.from_env()
    assert settings.public_base_url == "http://192.168.1.223:8791"
    assert settings.mqtt_host == "192.168.1.217"
    assert settings.mqtt_port == 1883
    assert settings.mqtt_client_id == "teslaslip"
    assert settings.car_id == 1
    assert settings.bark_url == "http://192.168.1.223:21850"
    assert settings.bind_port == 8791
    assert settings.trip_link_ttl_days == 0
    assert settings.amap_js_key == ""
    assert settings.sqlite_path == "/data/teslaslip.sqlite"
