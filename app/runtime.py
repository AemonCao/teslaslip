"""MQTT 沿、安静定时器和 Bark 发件箱。定时器不扫库。"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timedelta

from app.bark import (
    BarkError,
    envelope,
    page_url,
    summarize_charge_notification,
    summarize_drive_notification,
)
from app.clock import SystemClock
from app.config import Settings
from app.db import Store
from app.models import Charge, Drive, Link
from app.mqtt import (
    CLOSE_CHECK_OFFSETS_S,
    CancelQuiet,
    CatchUp,
    LookForCharge,
    MqttSession,
    WaitForDriveClose,
    next_backoff_seconds,
    topic_map,
)
from app.outing import NEED_MORE, advance_watermark, is_pure_shuffle, plan_outings, select_preview
from app.pages import expiry_from
from app.redact import SecretVault, log_event

preview_logger = logging.getLogger("teslaslip.preview")
preview_logger.setLevel(logging.INFO)

BARK_RETRY = (
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
)


class Runtime:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        teslamate,
        bark,
        clock: SystemClock | None = None,
        vault: SecretVault | None = None,
        *,
        enable_tasks: bool = True,
    ) -> None:
        self.settings = settings
        self.store = store
        self.teslamate = teslamate
        self.bark = bark
        self.clock = clock or SystemClock()
        self.vault = vault or SecretVault()
        self.enable_tasks = enable_tasks
        self.mqtt_connected = False
        self.preview_url: str | None = None
        self._session = MqttSession()
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._guard_lock: asyncio.Lock | None = None
        self._guard_loop = None

    def _guard(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._guard_loop is not loop:
            self._guard_lock = asyncio.Lock()
            self._guard_loop = loop
        return self._guard_lock

    async def startup(self) -> None:
        self.store.init()
        drive_was_new = self._ensure_watermark("drive_watermark", self.teslamate.max_ended_drive_id)
        self._ensure_watermark("charge_watermark", self.teslamate.max_ended_charge_id)
        self._session = MqttSession(
            state=_blank_to_none(self.store.get_meta("mqtt_state")),
            charging_state=_blank_to_none(self.store.get_meta("mqtt_charging_state")),
            destination=_blank_to_none(self.store.get_meta("active_route_destination")),
        )
        if drive_was_new:
            self._create_preview()
        log_event("teslaslip 启动", car_id=self.settings.car_id, port=self.settings.bind_port)
        await self.on_timer()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def on_timer(self) -> None:
        async with self._guard():
            await self._on_timer_locked()

    async def on_mqtt_connect(self) -> None:
        async with self._guard():
            self.mqtt_connected = True
            self._session.begin_baseline()

    async def on_mqtt_disconnect(self) -> None:
        async with self._guard():
            self.mqtt_connected = False

    async def on_mqtt_message(self, topic: str, payload: str) -> None:
        async with self._guard():
            kind = topic_map(self.settings.car_id, self.settings.mqtt_namespace).get(topic)
            if kind is None:
                return
            actions = self._session.on_message(kind, payload)
            self._persist_session()
            for action in actions:
                await self._apply(action)

    async def mqtt_loop(self) -> None:
        import aiomqtt

        delay = 1.0
        topics = list(topic_map(self.settings.car_id, self.settings.mqtt_namespace))
        while not self._stop.is_set():
            try:
                async with aiomqtt.Client(
                    hostname=self.settings.mqtt_host,
                    port=self.settings.mqtt_port,
                    username=self.settings.mqtt_username,
                    password=self.settings.mqtt_password,
                    identifier=self.settings.mqtt_client_id,
                ) as client:
                    for topic in topics:
                        await client.subscribe(topic, qos=1)
                    await self.on_mqtt_connect()
                    delay = 1.0
                    async for message in client.messages:
                        if self._stop.is_set():
                            break
                        payload = message.payload
                        if isinstance(payload, bytes):
                            payload = payload.decode("utf-8", errors="replace")
                        await self.on_mqtt_message(str(message.topic), str(payload))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.mqtt_connected = False
                log_event("mqtt 断开，稍后重连", delay_s=delay, error_type=type(exc).__name__)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                    return
                except TimeoutError:
                    pass
                delay = next_backoff_seconds(delay)

    async def timer_loop(self) -> None:
        while not self._stop.is_set():
            await self.on_timer()
            delay = self._seconds_until_next()
            self._wake.clear()
            try:
                if delay is None:
                    await self._wake.wait()
                else:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                pass

    def _seconds_until_next(self) -> float | None:
        moments: list[datetime] = []
        pending = self.store.get_pending_outing()
        if pending and pending.next_check_at:
            moments.append(pending.next_check_at)
        charge = self.store.get_pending_charge()
        if charge and charge.next_check_at:
            moments.append(charge.next_check_at)
        outbox_at = self.store.next_outbox_at()
        if outbox_at:
            moments.append(outbox_at)
        if not moments:
            return None
        return max((min(moments) - self.clock.now()).total_seconds(), 0.0)

    def _signal(self) -> None:
        self._wake.set()

    async def _apply(self, action) -> None:
        if isinstance(action, CatchUp):
            await self._catch_up()
        elif isinstance(action, WaitForDriveClose):
            self._arm_close_wait()
        elif isinstance(action, CancelQuiet):
            self._cancel_quiet()
        elif isinstance(action, LookForCharge):
            await self._on_charge_wake()

    async def _catch_up(self) -> None:
        pending = self.store.get_pending_outing()
        if pending is not None and pending.phase == "waiting_close":
            ended = self.teslamate.ended_drives_after(self.settings.car_id, self._drive_watermark())
            if self._fresh_drives(ended):
                await self._decide_ready_drives(ended)
        else:
            await self._decide_ready_drives()
        await self._push_ended_charges()

    async def _on_timer_locked(self) -> None:
        now = self.clock.now()
        pending = self.store.get_pending_outing()
        if pending and pending.next_check_at and pending.next_check_at <= now:
            if pending.phase == "waiting_close":
                await self._check_drive_closed()
            elif pending.phase == "quiet":
                await self._quiet_elapsed()
        charge = self.store.get_pending_charge()
        if charge and charge.next_check_at and charge.next_check_at <= now:
            await self._charge_retry()
        await self._flush_outbox()

    def _arm_close_wait(self) -> None:
        now = self.clock.now()
        self.store.set_pending_outing(
            phase="waiting_close",
            armed_at=now,
            deadline_at=None,
            next_check_at=now + timedelta(seconds=CLOSE_CHECK_OFFSETS_S[0]),
            attempts_done=0,
        )
        self._signal()

    def _cancel_quiet(self) -> None:
        watermark = self._drive_watermark()
        ended = self.teslamate.ended_drives_after(self.settings.car_id, watermark)
        seen = max((drive.id for drive in ended), default=watermark)
        self.store.set_meta("awaiting_drive_after_id", str(seen))
        self.store.clear_pending_outing()

    async def _check_drive_closed(self) -> None:
        if self._is_driving():
            self._cancel_quiet()
            return
        ended = self.teslamate.ended_drives_after(self.settings.car_id, self._drive_watermark())
        if not self._fresh_drives(ended):
            self._bump_close_attempt()
            return
        await self._decide_ready_drives(ended)

    def _fresh_drives(self, ended: list[Drive]) -> list[Drive]:
        threshold = max(self._awaiting_after_id(), self._drive_watermark())
        return [drive for drive in ended if drive.id > threshold]

    def _bump_close_attempt(self) -> None:
        pending = self.store.get_pending_outing()
        if pending is None or pending.armed_at is None:
            return
        attempts = pending.attempts_done + 1
        if attempts >= len(CLOSE_CHECK_OFFSETS_S):
            self.store.set_pending_outing(
                phase="waiting_close",
                armed_at=pending.armed_at,
                deadline_at=None,
                next_check_at=None,
                attempts_done=attempts,
            )
            return
        self.store.set_pending_outing(
            phase="waiting_close",
            armed_at=pending.armed_at,
            deadline_at=None,
            next_check_at=pending.armed_at + timedelta(seconds=CLOSE_CHECK_OFFSETS_S[attempts]),
            attempts_done=attempts,
        )
        self._signal()

    async def _quiet_elapsed(self) -> None:
        if self._is_driving():
            self.store.clear_pending_outing()
            return
        await self._decide_ready_drives()

    async def _decide_ready_drives(self, drives: list[Drive] | None = None) -> None:
        if drives is None:
            drives = self.teslamate.ended_drives_after(self.settings.car_id, self._drive_watermark())
        plan = plan_outings(
            drives,
            gap_minutes=self.settings.outing_gap_minutes,
            now=self.clock.now(),
            is_driving=self._is_driving(),
        )
        for outing in plan.ready:
            await self._decide_outing(list(outing))
        if plan.waiting_deadline is not None:
            self.store.set_pending_outing(
                phase="quiet",
                armed_at=self.clock.now(),
                deadline_at=plan.waiting_deadline,
                next_check_at=plan.waiting_deadline,
                attempts_done=0,
            )
            self._signal()
            return
        pending = self.store.get_pending_outing()
        if pending is not None and pending.phase == "waiting_close":
            return
        self.store.clear_pending_outing()

    async def _decide_outing(self, drives: list[Drive]) -> None:
        ids = [drive.id for drive in drives]
        if is_pure_shuffle(
            drives,
            self.settings.min_push_distance_km,
            self.settings.min_push_duration_min,
        ):
            if not self.store.drives_decided(ids):
                self.store.mark_drives(ids, None, "shuffle")
                self._advance_drive_watermark()
                self._clear_destination()
            return
        if self.store.drives_decided(ids):
            return
        existing = self.store.find_drive_link(ids)
        if existing is not None:
            self.store.ensure_outbox(existing.token, self.clock.now())
            await self._flush_outbox()
            return
        token = self._mint_token()
        now = self.clock.now()
        destination = self._session.destination or _blank_to_none(
            self.store.get_meta("active_route_destination")
        )
        self.store.insert_link(
            token=token,
            kind="drive",
            drive_ids=ids,
            charge_id=None,
            destination=destination,
            created_at=now,
            expires_at=expiry_from(now, self.settings.trip_link_ttl_days),
            preview=False,
        )
        self._clear_destination()
        self.store.ensure_outbox(token, now)
        await self._flush_outbox()

    async def _on_charge_wake(self) -> None:
        if await self._push_ended_charges():
            self.store.clear_pending_charge()
            return
        now = self.clock.now()
        self.store.set_pending_charge(
            armed_at=now,
            next_check_at=now + timedelta(seconds=CLOSE_CHECK_OFFSETS_S[0]),
            attempts_done=0,
        )
        self._signal()

    async def _charge_retry(self) -> None:
        if await self._push_ended_charges():
            self.store.clear_pending_charge()
            return
        pending = self.store.get_pending_charge()
        if pending is None or pending.armed_at is None:
            return
        attempts = pending.attempts_done + 1
        if attempts >= len(CLOSE_CHECK_OFFSETS_S):
            self.store.set_pending_charge(
                armed_at=pending.armed_at,
                next_check_at=None,
                attempts_done=attempts,
            )
            return
        self.store.set_pending_charge(
            armed_at=pending.armed_at,
            next_check_at=pending.armed_at + timedelta(seconds=CLOSE_CHECK_OFFSETS_S[attempts]),
            attempts_done=attempts,
        )
        self._signal()

    async def _push_ended_charges(self) -> bool:
        charges = self.teslamate.ended_charges_after(self.settings.car_id, self._charge_watermark())
        if not charges:
            return False
        for charge in sorted(charges, key=lambda item: (item.end_date, item.id)):
            await self._decide_charge(charge)
        return True

    async def _decide_charge(self, charge: Charge) -> None:
        if self.store.charge_decided(charge.id):
            return
        existing = self.store.find_charge_link(charge.id)
        if existing is not None:
            self.store.ensure_outbox(existing.token, self.clock.now())
            await self._flush_outbox()
            return
        token = self._mint_token()
        now = self.clock.now()
        self.store.insert_link(
            token=token,
            kind="charge",
            drive_ids=[],
            charge_id=charge.id,
            destination=None,
            created_at=now,
            expires_at=expiry_from(now, self.settings.trip_link_ttl_days),
            preview=False,
        )
        self.store.ensure_outbox(token, now)
        await self._flush_outbox()

    async def _flush_outbox(self) -> None:
        for item in self.store.due_outbox(self.clock.now()):
            link = self.store.get_link(item.token)
            if link is None or link.preview:
                self.store.delete_outbox(item.token)
                continue
            try:
                payload = self._payload_for(link)
                await self.bark.push(payload)
            except Exception as exc:
                delay = BARK_RETRY[min(item.attempts, len(BARK_RETRY) - 1)]
                self.store.schedule_outbox(item.token, self.clock.now() + delay, item.attempts + 1)
                log_event("bark 发送失败", attempt=item.attempts + 1, error_type=type(exc).__name__)
                self._signal()
                continue
            if link.kind == "drive":
                self.store.mark_drives(link.drive_ids, link.token, "pushed")
                self._advance_drive_watermark()
            elif link.charge_id is not None:
                self.store.mark_charge(link.charge_id, link.token)
                self._advance_charge_watermark()
            self.store.delete_outbox(item.token)
            log_event("bark 已发送", kind=link.kind)

    def _payload_for(self, link: Link) -> dict[str, object]:
        base = self.settings.public_base_url
        if link.kind == "drive":
            drives = self.teslamate.drives_by_ids(self.settings.car_id, link.drive_ids)
            if len(drives) != len(link.drive_ids):
                raise BarkError("missing drive")
            title, body = summarize_drive_notification(drives)
            url = page_url(base, "d", link.token)
        else:
            charge = self.teslamate.charge_by_id(self.settings.car_id, int(link.charge_id or 0))
            if charge is None:
                raise BarkError("missing charge")
            title, body = summarize_charge_notification(charge)
            url = page_url(base, "c", link.token)
        return envelope(self.settings.bark_device_key, title, body, url)

    def _advance_drive_watermark(self) -> None:
        current = self._drive_watermark()
        ended = self.teslamate.ended_drives_after(self.settings.car_id, current)
        updated = advance_watermark(
            current,
            self.store.decided_drive_ids(),
            [drive.id for drive in ended],
        )
        self.store.set_meta("drive_watermark", str(updated))

    def _advance_charge_watermark(self) -> None:
        current = self._charge_watermark()
        charges = self.teslamate.ended_charges_after(self.settings.car_id, current)
        updated = advance_watermark(
            current,
            self.store.decided_charge_ids(),
            [charge.id for charge in charges],
        )
        self.store.set_meta("charge_watermark", str(updated))

    def _create_preview(self) -> None:
        watermark = self._drive_watermark()
        rows: list[Drive] = []
        cursor_end = None
        cursor_id = None
        while True:
            batch = self.teslamate.recent_ended_drives(
                self.settings.car_id,
                watermark,
                limit=50,
                cursor_end=cursor_end,
                cursor_id=cursor_id,
            )
            rows.extend(batch)
            exhausted = len(batch) < 50
            selected = select_preview(
                rows,
                gap_minutes=self.settings.outing_gap_minutes,
                min_distance_km=self.settings.min_push_distance_km,
                min_duration_min=self.settings.min_push_duration_min,
                exhausted=exhausted,
            )
            if selected is NEED_MORE:
                if exhausted or not batch:
                    return
                cursor_end = batch[-1].end_date
                cursor_id = batch[-1].id
                continue
            if not selected:
                return
            token = secrets.token_urlsafe(16)
            now = self.clock.now()
            self.store.insert_link(
                token=token,
                kind="drive",
                drive_ids=[drive.id for drive in selected],
                charge_id=None,
                destination=None,
                created_at=now,
                expires_at=expiry_from(now, self.settings.trip_link_ttl_days),
                preview=True,
            )
            url = page_url(self.settings.public_base_url, "d", token)
            self.preview_url = url
            preview_logger.info("预览链接 %s", url)
            self.vault.add(token)
            return

    def _mint_token(self) -> str:
        token = secrets.token_urlsafe(16)
        self.vault.add(token)
        return token

    def _ensure_watermark(self, key: str, loader) -> bool:
        if self.store.get_meta(key) is not None:
            return False
        self.store.set_meta(key, str(int(loader(self.settings.car_id))))
        return True

    def _drive_watermark(self) -> int:
        return int(self.store.get_meta("drive_watermark") or 0)

    def _charge_watermark(self) -> int:
        return int(self.store.get_meta("charge_watermark") or 0)

    def _awaiting_after_id(self) -> int:
        raw = self.store.get_meta("awaiting_drive_after_id")
        if not raw:
            return self._drive_watermark()
        return int(raw)

    def _is_driving(self) -> bool:
        return (self._session.state or "") == "driving"

    def _persist_session(self) -> None:
        self.store.set_meta("mqtt_state", self._session.state or "")
        self.store.set_meta("mqtt_charging_state", self._session.charging_state or "")
        self.store.set_meta("active_route_destination", self._session.destination or "")

    def _clear_destination(self) -> None:
        self._session.destination = None
        self.store.set_meta("active_route_destination", "")


def _blank_to_none(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value
