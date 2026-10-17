"""MQTT 主题和状态机。连上后的第一条保留消息不是沿。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

CHARGE_TERMINAL = {"Complete", "Disconnected", "Stopped", "NoPower"}
CLOSE_CHECK_OFFSETS_S = (15, 60, 180)


def next_backoff_seconds(current: float, cap: float = 60) -> float:
    return min(max(current, 1.0) * 2, cap)


def topic_map(car_id: int, namespace: str = "") -> dict[str, str]:
    middle = f"{namespace.strip('/')}/" if namespace.strip("/") else ""
    base = f"teslamate/{middle}cars/{car_id}"
    return {
        f"{base}/state": "state",
        f"{base}/charging_state": "charging_state",
        f"{base}/active_route": "active_route",
    }


def parse_destination(payload: str) -> str | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("error") is not None:
        return None
    destination = data.get("destination")
    if not isinstance(destination, str):
        return None
    text = destination.strip()
    return text or None


@dataclass(frozen=True)
class CatchUp:
    pass


@dataclass(frozen=True)
class WaitForDriveClose:
    pass


@dataclass(frozen=True)
class CancelQuiet:
    pass


@dataclass(frozen=True)
class LookForCharge:
    pass


Action = CatchUp | WaitForDriveClose | CancelQuiet | LookForCharge


@dataclass
class MqttSession:
    state: str | None = None
    charging_state: str | None = None
    destination: str | None = None
    baselining: bool = False
    _seen: set[str] = field(default_factory=set)
    _queued: list[tuple[str, str]] = field(default_factory=list)

    def begin_baseline(self) -> None:
        self.baselining = True
        self._seen = set()
        self._queued = []

    def on_message(self, kind: str, payload: str) -> list[Action]:
        if kind not in {"state", "charging_state", "active_route"}:
            return []
        text = payload.strip()
        if self.baselining and kind in {"state", "charging_state"} and kind not in self._seen:
            self._seen.add(kind)
            self._assign_baseline(kind, text)
            if self._seen == {"state", "charging_state"}:
                return self._finish_baseline()
            return []
        if self.baselining:
            self._queued.append((kind, text))
            return []
        return self._handle_edge(kind, text)

    def _finish_baseline(self) -> list[Action]:
        self.baselining = False
        actions: list[Action] = [CatchUp()]
        queued = list(self._queued)
        self._queued = []
        for kind, payload in queued:
            actions.extend(self._handle_edge(kind, payload))
        return actions

    def _assign_baseline(self, kind: str, payload: str) -> None:
        if kind == "state":
            self.state = payload
        elif kind == "charging_state":
            self.charging_state = payload

    def _handle_edge(self, kind: str, payload: str) -> list[Action]:
        if kind == "state":
            return self._state_edge(payload)
        if kind == "charging_state":
            return self._charge_edge(payload)
        if kind == "active_route":
            self._remember_route(payload)
        return []

    def _state_edge(self, new_state: str) -> list[Action]:
        old = self.state
        self.state = new_state
        actions: list[Action] = []
        if new_state == "driving" and old != "driving":
            actions.append(CancelQuiet())
        if old == "driving" and new_state != "driving":
            actions.append(WaitForDriveClose())
        if old == "charging" and new_state != "charging":
            actions.append(LookForCharge())
        return actions

    def _charge_edge(self, new_state: str) -> list[Action]:
        old = self.charging_state
        self.charging_state = new_state
        if new_state in CHARGE_TERMINAL and new_state != old:
            return [LookForCharge()]
        return []

    def _remember_route(self, payload: str) -> None:
        if self.state != "driving":
            return
        destination = parse_destination(payload)
        if destination:
            self.destination = destination
