"""Tests for one-click self-consistent Chromium presets, custom startup URLs, and tab restore."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import time

import pytest
from fastapi.testclient import TestClient

from chromium import native as chromium_native
from moltskin.services.core import cdp, chromium, chromium_presets, config, db, launcher, main
from moltskin.services.core.security import get_token


@pytest.fixture
def api(data_dir):
    db.init_db()
    with TestClient(
        main.app,
        headers={"Authorization": f"Bearer {get_token()}"},
        raise_server_exceptions=True,
    ) as client:
        yield client


def test_chromium_preset_generator_consistency_across_os_families(api):
    for family in ("auto", "macos", "windows", "linux"):
        for seed in range(1, 12):
            resp = api.post(
                "/api/v1/chromium/fingerprint-preset",
                json={"os_family": family, "seed": seed},
            )
            assert resp.status_code == 200, resp.text
            preset = resp.json()["data"]
            os_family = preset["os_family"]
            assert preset["chromium_platform"].startswith(f"{os_family}:")
            if os_family == "macos":
                assert "Macintosh" in preset["chromium_user_agent"]
                assert "Apple" in preset["chromium_webgl"]
            elif os_family == "windows":
                assert "Windows NT 10.0" in preset["chromium_user_agent"]
                assert "Direct3D11" in preset["chromium_webgl"]
            else:
                assert "X11; Linux x86_64" in preset["chromium_user_agent"]

            # Window outer dimensions must never exceed screen work area.
            win_w, win_h = map(int, preset["browser_window_size"].split("x"))
            avail = config.CHROMIUM_SCREEN_AVAIL[os_family][preset["chromium_screen"]]
            avail_w, avail_h = map(int, avail.split("x"))
            assert win_w <= avail_w
            assert win_h <= avail_h
            assert preset["chromium_canvas_noise"] is True
            assert preset["chromium_audio_noise"] is True
            assert preset["geolocation"] == "auto"
            assert preset["webrtc_policy"] == "proxy"

    # Creating and randomizing an existing stopped Chromium profile via API.
    created = api.post("/api/v1/profiles", json={"name": "preset-target", "engine": "chromium"})
    assert created.status_code == 201
    pid = created.json()["data"]["id"]

    randomized = api.post(
        f"/api/v1/profiles/{pid}/randomize-chromium",
        json={"os_family": "windows", "seed": 42},
    )
    assert randomized.status_code == 200, randomized.text
    updated = randomized.json()["data"]
    assert updated["chromium_platform"] == "windows:10.0.0:x86_64"
    assert "Windows NT 10.0" in updated["chromium_user_agent"]
    assert updated["geolocation"] == "auto"
    assert updated["webrtc_policy"] == "proxy"


def test_prepare_launch_startup_urls_and_restore_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda _ver: chromium_native.InstalledBuild(
            Path("/bin/echo"), "158.0.8127.88", chromium_native.DESKTOP_CAPABILITIES
        ),
    )
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda _dir: False)

    args_restore, _ = chromium.prepare_launch(
        tmp_path / "p1",
        "standard",
        True,
        restore_last_session=True,
    )
    assert "--restore-last-session" in args_restore
    assert "about:blank" not in args_restore

    urls = ["https://example.com/a", "https://example.org/b"]
    args_custom, _ = chromium.prepare_launch(
        tmp_path / "p2",
        "standard",
        True,
        startup_urls=urls,
    )
    assert args_custom[-2:] == urls
    assert "about:blank" not in args_custom

    with pytest.raises(ValueError, match="http://"):
        chromium.prepare_launch(
            tmp_path / "p3",
            "standard",
            True,
            startup_urls=["javascript:alert(1)"],
        )


class _QuietHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = f"<html><head><title>{self.path}</title></head><body>{self.path}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@pytest.mark.skipif(
    not chromium_native.default_binary().exists(),
    reason="Moltskin Chromium binary not installed",
)
def test_live_chromium_custom_startup_urls_and_session_restore(api):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        u1 = f"http://127.0.0.1:{port}/tab-alpha"
        u2 = f"http://127.0.0.1:{port}/tab-beta"
        created = api.post(
            "/api/v1/profiles",
            json={
                "name": "live-startup-tabs",
                "engine": "chromium",
                "headless": False,
                "startup_page": "custom",
                "startup_urls": [u1, u2],
            },
        )
        assert created.status_code == 201, created.text
        profile = created.json()["data"]
        pid = profile["id"]
        profile_dir = Path(profile["user_data_dir"])

        launched = launcher.launch(pid)
        try:
            deadline = time.monotonic() + 10.0
            observed_urls: set[str] = set()
            while time.monotonic() < deadline:
                try:
                    pages = cdp.list_pages(profile_dir)
                    observed_urls = {p["url"] for p in pages}
                    if u1 in observed_urls and u2 in observed_urls:
                        break
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.2)
            assert u1 in observed_urls and u2 in observed_urls, observed_urls
        finally:
            launcher.stop(launched["session_id"])

        # Switch profile to restore_last_session and re-launch; previous tabs must come back!
        patched = api.patch(
            f"/api/v1/profiles/{pid}",
            json={"startup_page": "restore"},
        )
        assert patched.status_code == 200, patched.text

        relaunched = launcher.launch(pid)
        try:
            deadline = time.monotonic() + 10.0
            restored_urls: set[str] = set()
            while time.monotonic() < deadline:
                try:
                    pages = cdp.list_pages(profile_dir)
                    restored_urls = {p["url"] for p in pages}
                    if u1 in restored_urls and u2 in restored_urls:
                        break
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.2)
            assert u1 in restored_urls and u2 in restored_urls, restored_urls
        finally:
            launcher.stop(relaunched["session_id"])
    finally:
        server.shutdown()
        server.server_close()
