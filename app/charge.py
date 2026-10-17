"""充电采样汇总。一次充电一张页，不合并。"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import ChargeSample


@dataclass(frozen=True)
class ChargeSampleSummary:
    power_max: float | None
    voltage_max: float | None
    current_max: float | None
    phases_max: int | None
    cable: str | None
    fast_brand: str | None
    fast_type: str | None
    fast_present: bool
    sample_count: int


def summarize_samples(samples: list[ChargeSample]) -> ChargeSampleSummary:
    return ChargeSampleSummary(
        power_max=_max_attr(samples, "charger_power"),
        voltage_max=_max_attr(samples, "charger_voltage"),
        current_max=_max_attr(samples, "charger_current"),
        phases_max=_max_attr(samples, "charger_phases"),
        cable=_first_text(samples, "cable"),
        fast_brand=_first_text(samples, "fast_charger_brand"),
        fast_type=_first_text(samples, "fast_charger_type"),
        fast_present=any(bool(sample.fast_charger_present) for sample in samples),
        sample_count=len(samples),
    )


def _max_attr(samples: list[ChargeSample], name: str):
    values = [getattr(sample, name) for sample in samples if getattr(sample, name) is not None]
    if not values:
        return None
    return max(values)


def _first_text(samples: list[ChargeSample], name: str) -> str | None:
    for sample in samples:
        value = getattr(sample, name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
