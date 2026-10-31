"""用虚构采样记录绘图整理时间与响应体积：python -m tests.benchmark_charts。"""

import json
import math
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.charts import safe_json
from app.models import ChargeSample, TrackPoint
from app.pages import build_charge_page, build_drive_page
from tests.fakes import T0, make_charge, make_drive, make_settings


def long_drive(segment_count=1):
    drives = []
    tracks = []
    for segment in range(segment_count):
        start = T0 + timedelta(seconds=segment * 7500)
        drives.append(make_drive(segment + 1, start, start + timedelta(hours=2)))
        tracks.append([
            TrackPoint(
                date=start + timedelta(seconds=index / 4),
                latitude=30 + index / 1000000,
                longitude=120 + index / 1000000,
                speed=round(60 + 40 * math.sin(index / 100)),
                power=round(50 * math.sin(index / 13)),
                battery_level=80 - index // 2000,
                inside_temp=22 + math.sin(index / 1000) if index % 200 == 0 else None,
            )
            for index in range(28801)
        ])
    return drives, tracks


def main():
    templates = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "app" / "templates"), autoescape=select_autoescape(["html"]))
    with TemporaryDirectory() as directory:
        settings = make_settings(Path(directory))
        for segments in (1, 2):
            drives, tracks = long_drive(segments)
            started = perf_counter()
            page = build_drive_page(drives, tracks, None, settings)
            elapsed = perf_counter() - started
            chart = json.loads(page["chart_json"])
            print(safe_json({"scenario": f"drive_{segments}_segments", "rawSamples": sum(map(len, tracks)), "buildPageSeconds": round(elapsed, 3), "chartJsonBytes": len(page["chart_json"].encode()), "htmlBytes": len(templates.get_template("drive.html").render(v=page).encode()), "pointsPerCoreMetric": {metric["key"]: sum(len(chunk["points"]) for chunk in metric["chunks"]) for metric in chart["metrics"] if not metric["extra"]}}))
        charge = make_charge(1, T0, T0 + timedelta(hours=8), samples=[ChargeSample(date=T0 + timedelta(seconds=index * 10), charger_power=7, battery_level=20 + index // 50, charge_energy_added=index / 100, charger_voltage=220, charger_current=32) for index in range(2881)])
        started = perf_counter()
        page = build_charge_page(charge, settings)
        elapsed = perf_counter() - started
        print(safe_json({"scenario": "charge_8h", "rawSamples": len(charge.samples), "buildPageSeconds": round(elapsed, 3), "chartJsonBytes": len(page["chart_json"].encode()), "htmlBytes": len(templates.get_template("charge.html").render(v=page).encode())}))


if __name__ == "__main__":
    main()
