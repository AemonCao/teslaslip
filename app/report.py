"""生成已结束周期的私有报告链接；默认不推送。"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

from app.bark import page_url
from app.config import Settings
from app.db import Store
from app.reports import KINDS, ReportService, period_for
from app.teslamate import PostgresTeslaMate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=KINDS)
    parser.add_argument("date", type=date.fromisoformat, help="周期内任一天，格式 YYYY-MM-DD")
    parser.add_argument("--push", action="store_true", help="加入 Bark 发件箱，由运行中的服务发送")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    period = period_for(args.kind, args.date)
    if period.end > now:
        parser.error("只能生成已经结束的自然周期报告")
    settings = Settings.from_env()
    store = Store(settings.sqlite_path)
    store.init()
    service = ReportService(settings, store, PostgresTeslaMate(settings.database_url))
    token = service.generate(period, now, enqueue=args.push)
    print(page_url(settings.public_base_url, "r", token))


if __name__ == "__main__":
    main()
