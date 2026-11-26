"""Native screen geometry and DPR presets."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from fastapi.testclient import TestClient

from chromium.native import PLATFORM_CAPABILITY, SCREEN_AVAIL_CAPABILITY, SCREEN_METRICS_CAPABILITY, default_binary, probe_capabilities
from moltskin.services.core import chromium, main
from moltskin.services.core.security import get_token


def test_launcher_passes_screen_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (tmp_path / "Chromium", "158.0.0.0", frozenset({SCREEN_METRICS_CAPABILITY, SCREEN_AVAIL_CAPABILITY, PLATFORM_CAPABILITY})),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        chromium_screen="1920x1080x24@1.25",
        chromium_platform="windows:10.0.0:x86_64",
    )
    assert "--moltskin-screen-metrics=1920x1080x24@1.25|1920x1040" in args


def test_launcher_uses_macos_work_area(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (tmp_path / "Chromium", "158.0.0.0", frozenset({SCREEN_METRICS_CAPABILITY, SCREEN_AVAIL_CAPABILITY, PLATFORM_CAPABILITY})),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        chromium_screen="1920x1080x24@1.25",
        chromium_platform="macos:14.6.0:arm64",
    )
    assert "--moltskin-screen-metrics=1920x1080x24@1.25|1920x1000" in args


def test_editor_exposes_screen_field():
    html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="fChromiumScreen"' in html
    assert 'id="bChromiumScreen"' in html
    assert 'key: "chromium_screen"' in html


def test_profile_screen_persists_and_exports(data_dir):
    headers = {"Authorization": f"Bearer {get_token()}"}
    with TestClient(main.app) as client:
        created = client.post(
            "/api/v1/profiles",
            headers=headers,
            json={
                "name": "screen-profile",
                "engine": "chromium",
                "headless": True,
                "chromium_screen": "1920x1080x24@1.25",
            },
        )
        assert created.status_code == 201, created.text
        profile = created.json()["data"]
        assert profile["chromium_screen"] == "1920x1080x24@1.25"

        updated = client.patch(
            f"/api/v1/profiles/{profile['id']}",
            headers=headers,
            json={"chromium_screen": "1440x900x24@1"},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["chromium_screen"] == "1440x900x24@1"

        exported = client.get("/api/v1/export/profiles", headers=headers)
        assert exported.status_code == 200
        assert exported.json()["version"] == 11
        assert exported.json()["profiles"][0]["chromium_screen"] == "1440x900x24@1"


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_native_screen_avail_override():
    binary, _, capabilities = probe_capabilities(default_binary())
    assert SCREEN_METRICS_CAPABILITY in capabilities
    assert SCREEN_AVAIL_CAPABILITY in capabilities
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=str(binary),
            headless=True,
            args=["--moltskin-screen-metrics=1920x1080x24@1.25|1920x1040"],
        )
        try:
            page = browser.new_page()
            page.set_content("<html><body>screen probe</body></html>")
            values = page.evaluate(
                "() => [screen.width, screen.height, screen.availWidth, screen.availHeight]"
            )
        finally:
            browser.close()
    assert values == [1920, 1080, 1920, 1040]


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_native_screen_metrics_are_consistent():
    binary, _, capabilities = probe_capabilities(default_binary())
    assert SCREEN_METRICS_CAPABILITY in capabilities
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=str(binary),
            headless=True,
            args=["--moltskin-screen-metrics=1920x1080x24@1.25"],
        )
        try:
            page = browser.new_page()
            page.set_content("<html><body>screen probe</body></html>")
            values = page.evaluate("""() => ({
              screen: [screen.width, screen.height, screen.availWidth, screen.availHeight, screen.colorDepth],
              dpr: devicePixelRatio,
              width: matchMedia('(device-width: 1920px)').matches,
              height: matchMedia('(device-height: 1080px)').matches,
              resolution: matchMedia('(resolution: 1.25dppx)').matches,
            })""")
        finally:
            browser.close()
    assert values["screen"] == [1920, 1080, 1920, 1080, 24]
    assert values["dpr"] == 1.25
    assert values["width"] is True
    assert values["height"] is True
    assert values["resolution"] is True
