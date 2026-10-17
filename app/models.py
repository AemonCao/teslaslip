"""TeslaMate 只读行，以及本服务自己的链接行。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Address:
    display_name: str | None = None
    road: str | None = None
    house_number: str | None = None
    neighbourhood: str | None = None
    city: str | None = None
    name: str | None = None

    def parts(self) -> list[str]:
        found: list[str] = []
        for item in (
            self.display_name,
            self.name,
            self.road,
            self.house_number,
            self.neighbourhood,
            self.city,
        ):
            text = (item or "").strip()
            if text and text not in found:
                found.append(text)
        return found


@dataclass
class Drive:
    id: int
    start_date: datetime
    end_date: datetime
    distance_km: float | None
    duration_min: float | None
    speed_max: float | None = None
    power_max: float | None = None
    power_min: float | None = None
    start_km: float | None = None
    end_km: float | None = None
    start_rated_range_km: float | None = None
    end_rated_range_km: float | None = None
    start_ideal_range_km: float | None = None
    end_ideal_range_km: float | None = None
    outside_temp_avg: float | None = None
    inside_temp_avg: float | None = None
    ascent: float | None = None
    descent: float | None = None
    start_geofence: str | None = None
    end_geofence: str | None = None
    start_address: Address = field(default_factory=Address)
    end_address: Address = field(default_factory=Address)


@dataclass
class TrackPoint:
    date: datetime
    latitude: float
    longitude: float
    battery_level: int | None = None
    usable_battery_level: int | None = None
    tpms_fl: float | None = None
    tpms_fr: float | None = None
    tpms_rl: float | None = None
    tpms_rr: float | None = None
    climate_on: bool | None = None
    driver_temp: float | None = None
    passenger_temp: float | None = None
    front_defrost: bool | None = None
    rear_defrost: bool | None = None


@dataclass
class ChargeSample:
    date: datetime
    charger_power: float | None = None
    charger_voltage: float | None = None
    charger_current: float | None = None
    charger_phases: int | None = None
    cable: str | None = None
    fast_charger_brand: str | None = None
    fast_charger_type: str | None = None
    fast_charger_present: bool | None = None


@dataclass
class Charge:
    id: int
    start_date: datetime
    end_date: datetime
    duration_min: float | None
    charge_energy_added: float | None
    charge_energy_used: float | None
    cost: float | None
    start_battery_level: int | None
    end_battery_level: int | None
    start_rated_range_km: float | None
    end_rated_range_km: float | None
    start_ideal_range_km: float | None
    end_ideal_range_km: float | None
    outside_temp_avg: float | None
    geofence: str | None
    address: Address
    latitude: float | None
    longitude: float | None
    samples: list[ChargeSample] = field(default_factory=list)


@dataclass
class Link:
    token: str
    kind: str
    drive_ids: list[int]
    charge_id: int | None
    destination: str | None
    created_at: datetime
    expires_at: datetime | None
    preview: bool


@dataclass
class PendingOuting:
    phase: str
    armed_at: datetime | None
    deadline_at: datetime | None
    next_check_at: datetime | None
    attempts_done: int


@dataclass
class PendingCharge:
    armed_at: datetime | None
    next_check_at: datetime | None
    attempts_done: int


@dataclass
class OutboxItem:
    token: str
    next_attempt_at: datetime
    attempts: int
