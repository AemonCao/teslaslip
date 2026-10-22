"""把 Vite 产物交给模板。本机开发可以改指向 Vite。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from markupsafe import Markup

from app.redact import log_event

VITE_ORIGIN = "http://127.0.0.1:5173"
STATIC_DIR = Path(__file__).resolve().parent / "static"
_ISLANDS = {"settings", "drive-map", "charge-map"}
_missing_logged = False


def _escape_script(text: str) -> str:
    return text.replace("</script", "<\\/script")


def _escape_style(text: str) -> str:
    return text.replace("</style", "<\\/style")


class WebAssets:
    def __init__(self, static_dir: Path | None = None) -> None:
        self.static_dir = STATIC_DIR if static_dir is None else static_dir
        self.vite_dev = os.environ.get("TESLASLIP_VITE_DEV") == "1"
        self.css = ""
        self.boot_js = ""
        self.manifest: dict[str, object] = {}
        if self.vite_dev:
            return
        self._load()

    def _load(self) -> None:
        css_path = self.static_dir / "app.css"
        boot_path = self.static_dir / "boot.js"
        manifest_path = self.static_dir / ".vite" / "manifest.json"
        if css_path.is_file():
            self.css = _escape_style(css_path.read_text(encoding="utf-8"))
        if boot_path.is_file():
            self.boot_js = _escape_script(boot_path.read_text(encoding="utf-8"))
        if manifest_path.is_file():
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.manifest = loaded
        if not self.css or not self.boot_js or not self.manifest:
            _warn_missing()

    def head(self) -> Markup:
        if self.vite_dev:
            return Markup(
                f'<script type="module" src="{VITE_ORIGIN}/@vite/client"></script>\n'
                f'<script type="module" src="{VITE_ORIGIN}/src/boot.ts"></script>'
            )
        parts: list[str] = []
        if self.css:
            parts.append(f"<style>{self.css}</style>")
        if self.boot_js:
            parts.append(f"<script>{self.boot_js}</script>")
        return Markup("\n".join(parts))

    def island(self, name: str) -> Markup:
        if name not in _ISLANDS:
            return Markup("")
        if self.vite_dev:
            return Markup(f'<script type="module" src="{VITE_ORIGIN}/src/entries/{name}.ts"></script>')
        entry = _manifest_entry(self.manifest, name)
        if entry is None:
            return Markup("")
        file = entry.get("file")
        if not isinstance(file, str) or not file:
            return Markup("")
        src = file if file.startswith("/") else f"/static/{file}"
        return Markup(f'<script type="module" src="{src}"></script>')


def _manifest_entry(manifest: dict[str, object], name: str) -> dict[str, object] | None:
    suffix = f"entries/{name}.ts"
    for key, value in manifest.items():
        if not isinstance(value, dict):
            continue
        normalized = str(key).replace("\\", "/")
        if normalized == f"src/{suffix}" or normalized.endswith(suffix):
            return value
    return None


def _warn_missing() -> None:
    global _missing_logged
    if _missing_logged:
        return
    _missing_logged = True
    log_event("前端构建产物缺失")
