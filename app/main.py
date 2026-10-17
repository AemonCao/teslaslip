"""一个进程：页面、健康检查、MQTT 和安静定时器。"""

from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, secret_values
from app.db import Store
from app.pages import build_charge_page, build_drive_page, link_expired
from app.redact import SecretVault, log_event, setup_logging
from app.runtime import Runtime
from app.teslamate import PostgresTeslaMate

NOT_FOUND = "未找到"
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(runtime: Runtime) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.startup()
        tasks = []
        if runtime.enable_tasks:
            import asyncio

            tasks.append(asyncio.create_task(runtime.mqtt_loop()))
            tasks.append(asyncio.create_task(runtime.timer_loop()))
        try:
            yield
        finally:
            runtime.stop()
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(Exception):
                    await task

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return HTMLResponse(NOT_FOUND, status_code=404)
        return HTMLResponse("页面暂时无法打开", status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        if isinstance(exc, StarletteHTTPException):
            return HTMLResponse(NOT_FOUND if exc.status_code == 404 else "页面暂时无法打开", status_code=exc.status_code)
        log_event("请求失败", error_type=type(exc).__name__)
        return HTMLResponse("页面暂时无法打开", status_code=500)

    @app.get("/healthz")
    def healthz():
        sqlite_ok = runtime.store.probe_writable()
        postgres_ok = runtime.teslamate.ping()
        mqtt_ok = runtime.mqtt_connected
        body = {"ok": bool(sqlite_ok and postgres_ok and mqtt_ok)}
        if body["ok"]:
            return body
        return JSONResponse(body, status_code=503)

    @app.get("/d/{token}", response_class=HTMLResponse)
    def drive_page(request: Request, token: str):
        link = runtime.store.get_link(token)
        if link is None or link.kind != "drive" or link_expired(link.expires_at, runtime.clock.now()):
            return HTMLResponse(NOT_FOUND, status_code=404)
        drives = runtime.teslamate.drives_by_ids(runtime.settings.car_id, link.drive_ids)
        if len(drives) != len(link.drive_ids):
            return HTMLResponse(NOT_FOUND, status_code=404)
        ordered = sorted(drives, key=lambda drive: (drive.end_date, drive.id))
        tracks = [
            runtime.teslamate.track(drive.id, drive.start_date, drive.end_date) for drive in ordered
        ]
        page = build_drive_page(ordered, tracks, link.destination, runtime.settings)
        return templates.TemplateResponse(request, "drive.html", {"v": page})

    @app.get("/c/{token}", response_class=HTMLResponse)
    def charge_page(request: Request, token: str):
        link = runtime.store.get_link(token)
        if link is None or link.kind != "charge" or link.charge_id is None:
            return HTMLResponse(NOT_FOUND, status_code=404)
        if link_expired(link.expires_at, runtime.clock.now()):
            return HTMLResponse(NOT_FOUND, status_code=404)
        charge = runtime.teslamate.charge_by_id(runtime.settings.car_id, link.charge_id)
        if charge is None:
            return HTMLResponse(NOT_FOUND, status_code=404)
        page = build_charge_page(charge, runtime.settings)
        return templates.TemplateResponse(request, "charge.html", {"v": page})

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(NOT_FOUND, status_code=404)

    return app


def build_runtime(settings: Settings, *, enable_tasks: bool = True) -> Runtime:
    vault = SecretVault(secret_values(settings))
    return Runtime(
        settings,
        Store(settings.sqlite_path),
        PostgresTeslaMate(settings.database_url),
        _bark(settings),
        vault=vault,
        enable_tasks=enable_tasks,
    )


def _bark(settings: Settings):
    from app.bark import HttpBark

    return HttpBark(settings.bark_url)


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    vault = SecretVault(secret_values(settings))
    setup_logging(settings.log_level, vault)
    logging_quiet()
    runtime = Runtime(
        settings,
        Store(settings.sqlite_path),
        PostgresTeslaMate(settings.database_url),
        _bark(settings),
        vault=vault,
        enable_tasks=True,
    )
    uvicorn.run(
        create_app(runtime),
        host=settings.bind_host,
        port=settings.bind_port,
        access_log=False,
        log_level=settings.log_level,
    )


def logging_quiet() -> None:
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiomqtt").setLevel(logging.WARNING)
