"""环境变量。密钥只从这里读，不写进仓库。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


class ConfigError(RuntimeError):
    pass


def _raw(name: str) -> str | None:
    if name not in os.environ:
        return None
    return os.environ[name]


def _required(name: str) -> str:
    value = _raw(name)
    if value is None or value.strip() == "":
        raise ConfigError(f"缺少环境变量 {name}")
    return value.strip()


def _text(name: str, default: str) -> str:
    value = _raw(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _int(name: str, default: int) -> int:
    value = _raw(name)
    if value is None or value.strip() == "":
        return default
    return int(value.strip())


def _float(name: str, default: float) -> float:
    value = _raw(name)
    if value is None or value.strip() == "":
        return default
    return float(value.strip())


@dataclass(frozen=True)
class Settings:
    public_base_url: str
    database_url: str
    mqtt_username: str
    mqtt_password: str
    bark_device_key: str
    bind_host: str = "0.0.0.0"
    bind_port: int = 8791
    mqtt_host: str = "192.168.1.217"
    mqtt_port: int = 1883
    mqtt_client_id: str = "teslaslip"
    mqtt_namespace: str = ""
    car_id: int = 1
    bark_url: str = "http://192.168.1.223:21850"
    amap_js_key: str = ""
    amap_security_js_code: str = ""
    outing_gap_minutes: float = 10
    min_push_distance_km: float = 0.3
    min_push_duration_min: float = 2
    trip_link_ttl_days: int = 0
    sqlite_path: str = "/data/teslaslip.sqlite"
    log_level: str = "info"
    tz: str = "Asia/Shanghai"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            public_base_url=_required("PUBLIC_BASE_URL").rstrip("/"),
            database_url=_required("DATABASE_URL"),
            mqtt_username=_required("MQTT_USERNAME"),
            mqtt_password=_required("MQTT_PASSWORD"),
            bark_device_key=_required("BARK_DEVICE_KEY"),
            bind_host=_text("BIND_HOST", "0.0.0.0"),
            bind_port=_int("BIND_PORT", 8791),
            mqtt_host=_text("MQTT_HOST", "192.168.1.217"),
            mqtt_port=_int("MQTT_PORT", 1883),
            mqtt_client_id=_text("MQTT_CLIENT_ID", "teslaslip"),
            mqtt_namespace=_text("MQTT_NAMESPACE", ""),
            car_id=_int("CAR_ID", 1),
            bark_url=_text("BARK_URL", "http://192.168.1.223:21850").rstrip("/"),
            amap_js_key=_text("AMAP_JS_KEY", ""),
            amap_security_js_code=_text("AMAP_SECURITY_JS_CODE", ""),
            outing_gap_minutes=_float("OUTING_GAP_MINUTES", 10),
            min_push_distance_km=_float("MIN_PUSH_DISTANCE_KM", 0.3),
            min_push_duration_min=_float("MIN_PUSH_DURATION_MIN", 2),
            trip_link_ttl_days=_int("TRIP_LINK_TTL_DAYS", 0),
            sqlite_path=_text("SQLITE_PATH", "/data/teslaslip.sqlite"),
            log_level=_text("LOG_LEVEL", "info").lower(),
            tz=_text("TZ", "Asia/Shanghai"),
        )


def secret_values(settings: Settings) -> list[str]:
    values = [
        settings.database_url,
        settings.mqtt_password,
        settings.bark_device_key,
        settings.amap_js_key,
        settings.amap_security_js_code,
    ]
    password = urlsplit(settings.database_url).password
    if password:
        values.append(password)
    return [item for item in values if item]
