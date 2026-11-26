"""Per-profile platform and User-Agent Client Hints overrides."""

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from playwright.sync_api import sync_playwright

from chromium.native import PLATFORM_CAPABILITY, default_binary, probe_capabilities
from moltskin.services.core import chromium


def test_launcher_passes_platform_preset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (tmp_path / "Chromium", "158.0.0.0", frozenset({PLATFORM_CAPABILITY})),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        chromium_platform="windows:10.0.0:x86_64",
    )
    assert "--moltskin-platform=windows:10.0.0:x86_64" in args


def test_editor_exposes_platform_field():
    html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="fChromiumPlatform"' in html
    assert 'id="bChromiumPlatform"' in html
    assert 'key: "chromium_platform"' in html


def test_profile_platform_persists_and_exports(data_dir):
    from fastapi.testclient import TestClient

    from moltskin.services.core import main
    from moltskin.services.core.security import get_token

    headers = {"Authorization": f"Bearer {get_token()}"}
    with TestClient(main.app) as client:
        created = client.post(
            "/api/v1/profiles",
            headers=headers,
            json={
                "name": "platform-profile",
                "engine": "chromium",
                "headless": True,
                "chromium_platform": "windows:10.0.0:x86_64",
            },
        )
        assert created.status_code == 201, created.text
        profile = created.json()["data"]
        assert profile["chromium_platform"] == "windows:10.0.0:x86_64"

        updated = client.patch(
            f"/api/v1/profiles/{profile['id']}",
            headers=headers,
            json={"chromium_platform": "linux:6.8.0:arm64"},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["chromium_platform"] == "linux:6.8.0:arm64"

        exported = client.get("/api/v1/export/profiles", headers=headers)
        assert exported.status_code == 200
        assert exported.json()["version"] == 12
        assert exported.json()["profiles"][0]["chromium_platform"] == "linux:6.8.0:arm64"


PLATFORM_CASES = {
    "windows:10.0.0:x86_64": {
        "navigator": "Win32",
        "ua": "(Windows NT 10.0.0; Win64; x64)",
        "platform": "Windows",
        "architecture": "x86",
        "platform_version": "10.0.0",
    },
    "macos:14.6.0:arm64": {
        "navigator": "MacIntel",
        "ua": "(Macintosh; Intel Mac OS X 10_15_7)",
        "platform": "macOS",
        "architecture": "arm",
        "platform_version": "14.6.0",
    },
    "linux:6.8.0:x86_64": {
        "navigator": "Linux x86_64",
        "ua": "(X11; Linux x86_64)",
        "platform": "Linux",
        "architecture": "x86",
        "platform_version": "6.8.0",
    },
}


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
@pytest.mark.parametrize("preset", PLATFORM_CASES)
def test_native_platform_is_consistent(preset):
    expected = PLATFORM_CASES[preset]
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append({key.lower(): value for key, value in self.headers.items()})
            body = b"<!doctype html><title>platform probe</title>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header(
                "Accept-CH",
                "Sec-CH-UA-Platform, Sec-CH-UA-Platform-Version, Sec-CH-UA-Arch, Sec-CH-UA-Bitness",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        binary, _, capabilities = probe_capabilities(default_binary())
        assert PLATFORM_CAPABILITY in capabilities
        with sync_playwright() as driver:
            browser = driver.chromium.launch(
                executable_path=str(binary),
                headless=True,
                args=[f"--moltskin-platform={preset}"],
            )
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/")
                page.wait_for_timeout(500)
                values = page.evaluate(
                    """async () => ({
                      platform: navigator.platform,
                      ua: navigator.userAgent,
                      data: navigator.userAgentData ? {
                        platform: navigator.userAgentData.platform,
                        high: await navigator.userAgentData.getHighEntropyValues(
                          ['architecture', 'bitness', 'platformVersion']),
                      } : null,
                    })"""
                )
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join()

    assert values["platform"] == expected["navigator"]
    assert expected["ua"] in values["ua"]
    assert values["data"]["platform"] == expected["platform"]
    assert values["data"]["high"]["architecture"] == expected["architecture"]
    assert values["data"]["high"]["bitness"] == "64"
    assert values["data"]["high"]["platformVersion"] == expected["platform_version"]

    hinted = [
        request for request in requests
        if request.get("sec-ch-ua-platform-version")
    ]
    assert hinted, requests
    assert hinted[-1]["sec-ch-ua-platform"] == f'"{expected["platform"]}"'
    assert hinted[-1]["sec-ch-ua-arch"] == f'"{expected["architecture"]}"'
    assert hinted[-1]["sec-ch-ua-bitness"] == '"64"'
    assert hinted[-1]["sec-ch-ua-platform-version"] == f'"{expected["platform_version"]}"'
