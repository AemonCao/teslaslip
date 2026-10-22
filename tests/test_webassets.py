import asyncio
import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

import app.webassets as webassets
from app.main import create_app, templates
from app.webassets import STATIC_DIR, WebAssets
from tests.fakes import boot


def _write_assets(static_dir, *, css: str, boot_js: str, manifest: dict) -> None:
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "app.css").write_text(css, encoding="utf-8")
    (static_dir / "boot.js").write_text(boot_js, encoding="utf-8")
    vite_dir = static_dir / ".vite"
    vite_dir.mkdir()
    (vite_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_head_inlines_css_and_escapes_closing_tags(tmp_path, monkeypatch):
    monkeypatch.delenv("TESLASLIP_VITE_DEV", raising=False)
    _write_assets(
        tmp_path,
        css="body{content:'</style>'}",
        boot_js="var s = '</script>'",
        manifest={"src/entries/drive-map.ts": {"file": "assets/drive-map-abc.js", "isEntry": True}},
    )
    assets = WebAssets(tmp_path)
    head = str(assets.head())
    style = head.split("<style>", 1)[1].rsplit("</style>", 1)[0]
    script = head.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    assert "</style>" not in style
    assert "</script>" not in script
    assert 'src="/static/assets/drive-map-abc.js"' in str(assets.island("drive-map"))
    assert str(assets.island("other")) == ""


def test_vite_dev_points_at_the_dev_server(tmp_path, monkeypatch):
    monkeypatch.setenv("TESLASLIP_VITE_DEV", "1")
    assets = WebAssets(tmp_path)
    head = str(assets.head())
    assert "http://127.0.0.1:5173/@vite/client" in head
    assert "http://127.0.0.1:5173/src/boot.ts" in head
    assert "<style>" not in head
    assert 'src="http://127.0.0.1:5173/src/entries/settings.ts"' in str(assets.island("settings"))


def test_missing_build_logs_once_and_settings_still_open(tmp_path, caplog, monkeypatch):
    monkeypatch.delenv("TESLASLIP_VITE_DEV", raising=False)
    webassets._missing_logged = False
    caplog.set_level(logging.INFO, logger="teslaslip")
    missing = WebAssets(tmp_path / "missing")
    assert str(missing.head()) == ""
    assert "前端构建产物缺失" in caplog.text

    caplog.clear()
    WebAssets(tmp_path / "missing-again")
    assert "前端构建产物缺失" not in caplog.text

    async def body():
        runtime, _tm, _bark, _clock = await boot(tmp_path / "app")
        app = create_app(runtime)
        previous = (
            templates.env.globals.get("assets"),
            templates.env.globals.get("island"),
        )
        templates.env.globals["assets"] = missing
        templates.env.globals["island"] = missing.island
        try:
            with TestClient(app) as client:
                response = client.get("/settings")
        finally:
            templates.env.globals["assets"] = previous[0]
            templates.env.globals["island"] = previous[1]
        assert response.status_code == 200
        assert "<h1>设置</h1>" in response.text
        assert 'id="settings-app"' in response.text

    asyncio.run(body())


def test_settings_response_includes_island_script(tmp_path, monkeypatch):
    monkeypatch.delenv("TESLASLIP_VITE_DEV", raising=False)
    static_dir = tmp_path / "static"
    _write_assets(
        static_dir,
        css="/* marker-css */",
        boot_js="/* marker-boot */",
        manifest={"src/entries/settings.ts": {"file": "assets/settings-abc.js"}},
    )
    assets = WebAssets(static_dir)

    async def body():
        runtime, _tm, _bark, _clock = await boot(tmp_path / "app")
        app = create_app(runtime)
        previous = (
            templates.env.globals.get("assets"),
            templates.env.globals.get("island"),
        )
        templates.env.globals["assets"] = assets
        templates.env.globals["island"] = assets.island
        try:
            with TestClient(app) as client:
                response = client.get("/settings")
        finally:
            templates.env.globals["assets"] = previous[0]
            templates.env.globals["island"] = previous[1]
        assert response.status_code == 200
        assert "marker-css" in response.text
        assert "marker-boot" in response.text
        assert 'src="/static/assets/settings-abc.js"' in response.text

    asyncio.run(body())


@pytest.mark.skipif(not (STATIC_DIR / "boot.js").is_file(), reason="前端尚未构建")
def test_built_frontend_is_linked_from_settings(tmp_path):
    async def body():
        runtime, _tm, _bark, _clock = await boot(tmp_path)
        with TestClient(create_app(runtime)) as client:
            page = client.get("/settings")
            assert page.status_code == 200
            assert "<style>" in page.text
            assert "teslaslip-ui" in page.text
            match = re.search(r'src="(/static/assets/settings-[^"]+\.js)"', page.text)
            assert match
            script = client.get(match.group(1))
            assert script.status_code == 200
            assert "恢复默认" in script.text
            chunk = re.search(r'from"(\./[^"]+\.js)"', script.text)
            assert chunk
            shared = client.get("/static/assets/" + chunk.group(1).removeprefix("./"))
            assert shared.status_code == 200

    asyncio.run(body())
