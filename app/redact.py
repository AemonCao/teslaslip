"""日志脱敏。预览 URL 走单独的 logger，其余日志去掉 token 路径。"""

from __future__ import annotations

import logging
import re
from typing import Iterable

SENSITIVE_FIELDS = {
    "latitude",
    "longitude",
    "lat",
    "lng",
    "lon",
    "token",
    "device_key",
    "password",
    "mqtt_password",
    "database_url",
    "destination",
    "secret",
    "authorization",
}

_COORD = re.compile(
    r'(?i)("?(?:latitude|longitude|lat|lng|lon)"?\s*[:=]\s*)-?\d+(?:\.\d+)?'
)
_TOKEN_PATH = re.compile(r"/(d|c)/[A-Za-z0-9_-]{8,}")


class SecretVault:
    def __init__(self, secrets: Iterable[str] | None = None) -> None:
        self.secrets: list[str] = []
        for secret in secrets or []:
            self.add(secret)

    def add(self, secret: str | None) -> None:
        if secret and len(secret) >= 4 and secret not in self.secrets:
            self.secrets.append(secret)


class RedactFilter(logging.Filter):
    def __init__(self, vault: SecretVault, *, redact_tokens: bool) -> None:
        super().__init__()
        self.vault = vault
        self.redact_tokens = redact_tokens

    def filter(self, record: logging.LogRecord) -> bool:
        message = redact_text(
            record.getMessage(),
            self.vault.secrets,
            redact_tokens=self.redact_tokens,
        )
        record.msg = message
        record.args = ()
        return True


def redact_text(message: str, secrets: Iterable[str], *, redact_tokens: bool) -> str:
    redacted = _COORD.sub(r"\1[redacted]", message)
    if redact_tokens:
        redacted = _TOKEN_PATH.sub(r"/\1/[redacted]", redacted)
    for secret in secrets:
        if secret and len(secret) >= 4 and secret in redacted:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def log_event(message: str, **fields: object) -> None:
    safe: dict[str, object] = {}
    for key, value in fields.items():
        if key.lower() in SENSITIVE_FIELDS:
            safe[key] = "[redacted]"
        else:
            safe[key] = value
    logging.getLogger("teslaslip").info("%s %s", message, safe)


def setup_logging(level: str, vault: SecretVault) -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    app_logger = logging.getLogger("teslaslip")
    app_logger.setLevel(numeric)
    app_logger.propagate = False
    app_logger.handlers.clear()
    app_logger.filters.clear()
    app_handler = logging.StreamHandler()
    app_handler.setFormatter(formatter)
    app_handler.addFilter(RedactFilter(vault, redact_tokens=True))
    app_logger.addFilter(RedactFilter(vault, redact_tokens=True))
    app_logger.addHandler(app_handler)

    preview = logging.getLogger("teslaslip.preview")
    preview.setLevel(logging.INFO)
    preview.propagate = False
    preview.handlers.clear()
    preview.filters.clear()
    preview_handler = logging.StreamHandler()
    preview_handler.setFormatter(formatter)
    preview_handler.addFilter(RedactFilter(vault, redact_tokens=False))
    preview.addHandler(preview_handler)

    root = logging.getLogger()
    root.setLevel(numeric)
    if not any(isinstance(item, RedactFilter) for item in root.filters):
        root.addFilter(RedactFilter(vault, redact_tokens=True))
