from __future__ import annotations

import hmac
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import Auth, CSRF_COOKIE, SESSION_COOKIE, SESSION_DAYS, digest_token, safe_next, verify_password
from app.formatutil import SHANGHAI, fmt_local, fmt_minutes, fmt_number
from app.pages import build_charge_page, build_drive_page, expiry_from, link_expired
from app.redact import log_event

PAGE_SIZE = 20


async def read_form(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise HTTPException(413)
    try:
        fields = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=8)
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(400) from None
    if any(len(values) != 1 for values in fields.values()):
        raise HTTPException(400)
    return {key: values[0] for key, values in fields.items()}


class Portal:
    def __init__(self, runtime, templates) -> None:
        self.runtime = runtime
        self.templates = templates
        self.auth = Auth(runtime)

    def authenticated(self, request: Request) -> bool:
        return self.auth.session(request) is not None

    def login_redirect(self, request: Request):
        target = request.url.path
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse("/login?" + urlencode({"next": safe_next(target)}), status_code=303)

    def login_page(self, request: Request, *, error: str = "", status: int = 200, target: str | None = None):
        csrf = request.cookies.get(CSRF_COOKIE, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", csrf):
            csrf = secrets.token_urlsafe(32)
        if not self.auth.enabled:
            error = "历史总览尚未启用，请先配置管理员账号。"
            status = 503
        response = self.templates.TemplateResponse(request, "login.html", {
            "error": error, "csrf": csrf, "enabled": self.auth.enabled,
            "next": safe_next(target if target is not None else request.query_params.get("next", "/")),
        }, status_code=status)
        self.auth.set_cookie(response, CSRF_COOKIE, csrf, 600)
        return response

    def check_origin(self, request: Request) -> None:
        origin = request.headers.get("origin")
        allowed = {self.runtime.settings.public_base_url.rstrip("/"), str(request.base_url).rstrip("/")}
        if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin not in allowed):
            raise HTTPException(403)

    def check_csrf(self, request: Request, form: dict[str, str], token: str) -> None:
        self.check_origin(request)
        if not hmac.compare_digest(form.get("csrf", "").encode(), self.auth.csrf(token).encode()):
            raise HTTPException(403)

    def expired_page(self, request: Request):
        return self.templates.TemplateResponse(request, "expired.html", {
            "login_url": "/login?" + urlencode({"next": safe_next(request.url.path)}),
        }, status_code=404)

    def detail_context(self, request: Request, share_action: str = "") -> dict:
        session = self.auth.session(request)
        return {
            "portal_authenticated": session is not None,
            "share_action": share_action,
            "csrf": self.auth.csrf(session) if session else "",
            "share_days": self.runtime.settings.trip_link_ttl_days,
        }

    def render_drive(self, request: Request, drive_ids: list[int], destination: str | None = None, share_action: str = ""):
        runtime = self.runtime
        drives = runtime.teslamate.drives_by_ids(runtime.settings.car_id, drive_ids)
        if not drives or len(drives) != len(drive_ids):
            raise HTTPException(404)
        if any(drive.end_date is None for drive in drives):
            raise HTTPException(404)
        ordered = sorted(drives, key=lambda drive: (drive.end_date, drive.id))
        tracks = [runtime.teslamate.track(drive.id, drive.start_date, drive.end_date) for drive in ordered]
        try:
            car = runtime.teslamate.car(runtime.settings.car_id)
        except Exception as exc:
            log_event("车辆型号读取失败", error_type=type(exc).__name__)
            car = None
        page = build_drive_page(ordered, tracks, destination, runtime.settings, car)
        return self.templates.TemplateResponse(request, "drive.html", {
            "v": page, **self.detail_context(request, share_action),
        })

    def render_charge(self, request: Request, charge_id: int, share_action: str = ""):
        runtime = self.runtime
        charge = runtime.teslamate.charge_by_id(runtime.settings.car_id, charge_id)
        if charge is None or charge.end_date is None:
            raise HTTPException(404)
        return self.templates.TemplateResponse(request, "charge.html", {
            "v": build_charge_page(charge, runtime.settings), **self.detail_context(request, share_action),
        })

    def install(self, app) -> None:
        @app.get("/login", response_class=HTMLResponse)
        def login(request: Request):
            if self.authenticated(request):
                return RedirectResponse(safe_next(request.query_params.get("next", "/")), status_code=303)
            return self.login_page(request)

        @app.post("/login")
        async def authenticate(request: Request):
            if not self.auth.enabled:
                return self.login_page(request)
            form = await read_form(request)
            try:
                self.check_origin(request)
            except HTTPException:
                log_event("登录校验拒绝", reason="origin_rejected")
                return self.login_page(
                    request, error="登录请求来源校验失败，请从本站登录页重新提交。", status=403,
                    target=form.get("next", "/"),
                )
            csrf = request.cookies.get(CSRF_COOKIE, "")
            if len(csrf) != 43 or not hmac.compare_digest(form.get("csrf", "").encode(), csrf.encode()):
                reason = "csrf_mismatch" if csrf else "csrf_cookie_missing"
                log_event("登录校验拒绝", reason=reason)
                error = "登录页面已失效，请在当前页面重新输入账号和密码。" if csrf else "浏览器未保存登录校验信息，请允许本站 Cookie 后重新登录。"
                return self.login_page(request, error=error, status=403, target=form.get("next", "/"))
            now = self.runtime.clock.now().timestamp()
            if not self.runtime.store.allow_login_attempt(now):
                response = self.login_page(request, error="尝试次数过多，请在 5 分钟后重试。", status=429, target=form.get("next", "/"))
                response.headers["Retry-After"] = "300"
                return response
            from starlette.concurrency import run_in_threadpool

            valid = await run_in_threadpool(verify_password, form.get("password", ""), self.runtime.settings.admin_password_hash)
            username_ok = hmac.compare_digest(form.get("username", "").encode(), self.runtime.settings.admin_username.encode())
            if not valid or not username_ok:
                return self.login_page(request, error="账号或密码错误。", status=401, target=form.get("next", "/"))
            previous = self.auth.session(request)
            if previous:
                self.runtime.store.delete_session(digest_token(previous))
            session = secrets.token_urlsafe(32)
            duration = SESSION_DAYS * 86400
            self.runtime.store.create_session(digest_token(session), self.auth.fingerprint, now + duration, now)
            response = RedirectResponse(safe_next(form.get("next", "/")), status_code=303)
            self.auth.set_cookie(response, SESSION_COOKIE, session, duration)
            self.auth.clear_cookie(response, CSRF_COOKIE)
            return response

        @app.post("/logout")
        async def logout(request: Request):
            session = self.auth.session(request)
            if not session:
                return RedirectResponse("/login", status_code=303)
            form = await read_form(request)
            self.check_csrf(request, form, session)
            self.runtime.store.delete_session(digest_token(session))
            response = RedirectResponse("/login", status_code=303)
            self.auth.clear_cookie(response, SESSION_COOKIE)
            return response

        @app.get("/", response_class=HTMLResponse)
        def history(request: Request):
            session = self.auth.session(request)
            if not session:
                return self.login_redirect(request)
            try:
                view = self.history_view(request)
            except (ValueError, OverflowError):
                return self.templates.TemplateResponse(request, "history.html", {
                    "error": "筛选条件无效，请检查日期范围和页码。", "tab": "reports", "items": [],
                    "filters": {}, "page": 1, "csrf": self.auth.csrf(session),
                }, status_code=400)
            return self.templates.TemplateResponse(request, "history.html", {**view, "csrf": self.auth.csrf(session)})

        @app.get("/history/{kind}/{record_id}", response_class=HTMLResponse)
        def historical_detail(request: Request, kind: str, record_id: int):
            if not self.authenticated(request):
                return self.login_redirect(request)
            action = f"/history/{kind}/{record_id}/share"
            if kind == "drives":
                return self.render_drive(request, [record_id], share_action=action)
            if kind == "charges":
                return self.render_charge(request, record_id, share_action=action)
            raise HTTPException(404)

        @app.post("/history/{kind}/{record_id}/share")
        async def share(request: Request, kind: str, record_id: int):
            session = self.auth.session(request)
            if not session:
                return self.login_redirect(request)
            form = await read_form(request)
            self.check_csrf(request, form, session)
            from starlette.concurrency import run_in_threadpool

            return await run_in_threadpool(self.create_share, kind, record_id)

    def create_share(self, kind: str, record_id: int):
        runtime = self.runtime
        if kind == "drives":
            records = runtime.teslamate.drives_by_ids(runtime.settings.car_id, [record_id])
            exists = bool(records) and records[0].end_date is not None
            link_kind, prefix = "drive", "d"
        elif kind == "charges":
            record = runtime.teslamate.charge_by_id(runtime.settings.car_id, record_id)
            exists = record is not None and record.end_date is not None
            link_kind, prefix = "charge", "c"
        else:
            raise HTTPException(404)
        if not exists:
            raise HTTPException(404)
        token = secrets.token_urlsafe(16)
        runtime.vault.add(token)
        now = runtime.clock.now()
        runtime.store.insert_link(
            token=token, kind=link_kind, drive_ids=[record_id] if kind == "drives" else None,
            charge_id=record_id if kind == "charges" else None, destination=None,
            created_at=now, expires_at=expiry_from(now, runtime.settings.trip_link_ttl_days), preview=True,
        )
        return RedirectResponse(f"/{prefix}/{token}", status_code=303)

    def history_view(self, request: Request) -> dict:
        params = request.query_params
        tab = params.get("tab", "reports")
        kind = params.get("kind", "")
        page = int(params.get("page", "1"))
        if tab not in {"reports", "drives", "charges"} or kind not in {"", "week", "month", "year"} or not 1 <= page <= 10000:
            raise ValueError("无效筛选")
        filters = {key: params.get(key, "") for key in ("kind", "year", "start", "end")}
        first = date.fromisoformat(filters["start"]) if filters["start"] else date(1970, 1, 1)
        last = date.fromisoformat(filters["end"]) + timedelta(days=1) if filters["end"] else date(9999, 1, 1)
        if filters["year"]:
            year = int(filters["year"])
            first, last = max(first, date(year, 1, 1)), min(last, date(year + 1, 1, 1))
        if first >= last or first.year < 1970:
            raise ValueError("无效日期")
        start = datetime.combine(first, datetime.min.time(), SHANGHAI).astimezone(timezone.utc)
        end = datetime.combine(last, datetime.min.time(), SHANGHAI).astimezone(timezone.utc)
        offset = (page - 1) * PAGE_SIZE
        runtime = self.runtime
        if tab == "reports":
            records = runtime.store.list_reports(runtime.settings.car_id, kind, start, end, PAGE_SIZE + 1, offset)
            items = [{
                "url": "/r/" + row["token"], "title": row["snapshot"]["title"],
                "time": row["snapshot"]["period_text"], "summary": row["snapshot"]["opening"],
                "badge": "分享已过期" if link_expired(row["expires_at"], runtime.clock.now()) else "可分享",
            } for row in records[:PAGE_SIZE]]
        else:
            records = runtime.teslamate.history_records(tab, runtime.settings.car_id, start, end, PAGE_SIZE + 1, offset)
            items = []
            for record in records[:PAGE_SIZE]:
                if tab == "drives":
                    title = f"{record.start_geofence or '起点'} → {record.end_geofence or '终点'}"
                    summary = f"{fmt_number(record.distance_km)} km · {fmt_minutes(record.duration_min)}"
                else:
                    title = record.geofence or "充电记录"
                    summary = f"{fmt_number(record.charge_energy_added)} kWh · {fmt_minutes(record.duration_min)}"
                items.append({"url": f"/history/{tab}/{record.id}", "title": title,
                              "time": f"{fmt_local(record.start_date)} – {fmt_local(record.end_date)}",
                              "summary": summary, "badge": "仅登录可见"})
        def page_url(number: int) -> str:
            return "/?" + urlencode({"tab": tab, **filters, "page": number})

        return {
            "tab": tab, "filters": filters, "page": page, "items": items,
            "previous": page_url(page - 1) if page > 1 else "",
            "next": page_url(page + 1) if len(records) > PAGE_SIZE and page < 10000 else "",
        }
