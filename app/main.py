"""一个进程：页面、健康检查、MQTT 和安静定时器。"""

from __future__ import annotations

import contextlib
import asyncio
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, secret_values
from app.db import Store
from app.pages import link_expired
from app.portal import Portal
from app.redact import SecretVault, log_event, setup_logging
from app.runtime import Runtime
from app.teslamate import PostgresTeslaMate
from app.webassets import STATIC_DIR, WebAssets

NOT_FOUND = "未找到"
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(runtime: Runtime) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.startup()
        tasks = []
        if runtime.enable_tasks:
            tasks.append(asyncio.create_task(runtime.mqtt_loop()))
            tasks.append(asyncio.create_task(runtime.timer_loop()))
            if runtime.settings.reports_enabled:
                tasks.append(asyncio.create_task(runtime.report_loop()))
        try:
            yield
        finally:
            runtime.stop()
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await task

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime
    portal = Portal(runtime, templates)
    portal.install(app)

    @app.middleware("http")
    async def private_headers(request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Vary"] = "Cookie"
        return response

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
        if link is None or link.kind != "drive":
            return HTMLResponse(NOT_FOUND, status_code=404)
        if link_expired(link.expires_at, runtime.clock.now()) and not portal.authenticated(request):
            return portal.expired_page(request)
        return portal.render_drive(request, link.drive_ids, link.destination)

    @app.get("/c/{token}", response_class=HTMLResponse)
    def charge_page(request: Request, token: str):
        link = runtime.store.get_link(token)
        if link is None or link.kind != "charge" or link.charge_id is None:
            return HTMLResponse(NOT_FOUND, status_code=404)
        if link_expired(link.expires_at, runtime.clock.now()) and not portal.authenticated(request):
            return portal.expired_page(request)
        return portal.render_charge(request, link.charge_id)

    @app.get("/r/{token}", response_class=HTMLResponse)
    def report_page(request: Request, token: str):
        link = runtime.store.get_link(token)
        if link is None or link.kind != "report":
            return HTMLResponse(NOT_FOUND, status_code=404)
        if link_expired(link.expires_at, runtime.clock.now()) and not portal.authenticated(request):
            return portal.expired_page(request)
        report = runtime.store.get_report(token)
        if report is None:
            return HTMLResponse(NOT_FOUND, status_code=404)
        return templates.TemplateResponse(
            request, "report.html", {"v": report, **portal.detail_context(request)},
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"},
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return templates.TemplateResponse(request, "settings.html", {})

    assets = WebAssets()
    templates.env.globals["assets"] = assets
    templates.env.globals["island"] = assets.island
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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
