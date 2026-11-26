"""Per-profile unmasked WebGL vendor and renderer overrides."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from chromium.native import PLATFORM_CAPABILITY, WEBGL_INFO_CAPABILITY, WEBGL_PARAMS_CAPABILITY, default_binary, probe_capabilities
from moltskin.services.core import chromium

PRESET = (
    "Apple M1|Google Inc. (Apple)|"
    "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"
)


def test_launcher_passes_webgl_preset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (tmp_path / "Chromium", "158.0.0.0", frozenset({WEBGL_INFO_CAPABILITY, WEBGL_PARAMS_CAPABILITY, PLATFORM_CAPABILITY})),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        chromium_webgl=PRESET,
        chromium_platform="windows:10.0.0:x86_64",
    )
    assert "--moltskin-webgl-vendor=Google Inc. (Apple)" in args
    assert "--moltskin-webgl-renderer=ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)" in args
    assert "--moltskin-webgl-profile=windows" in args


def test_editor_exposes_webgl_field():
    html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="fChromiumWebgl"' in html
    assert 'id="bChromiumWebgl"' in html
    assert 'key: "chromium_webgl"' in html


def test_profile_webgl_persists_and_exports(data_dir):
    from fastapi.testclient import TestClient

    from moltskin.services.core import main
    from moltskin.services.core.security import get_token

    headers = {"Authorization": f"Bearer {get_token()}"}
    with TestClient(main.app) as client:
        created = client.post(
            "/api/v1/profiles",
            headers=headers,
            json={
                "name": "webgl-profile",
                "engine": "chromium",
                "headless": True,
                "chromium_webgl": PRESET,
            },
        )
        assert created.status_code == 201, created.text
        profile = created.json()["data"]
        assert profile["chromium_webgl"] == PRESET

        exported = client.get("/api/v1/export/profiles", headers=headers)
        assert exported.status_code == 200
        assert exported.json()["version"] == 13
        assert exported.json()["profiles"][0]["chromium_webgl"] == PRESET

        engine = client.get("/api/v1/engine", headers=headers)
        assert engine.status_code == 200
        assert PRESET in engine.json()["data"]["chromium"]["chromium_webgl_presets"]

        rejected = client.post(
            "/api/v1/profiles",
            headers=headers,
            json={"name": "bad-webgl", "engine": "chromium", "chromium_webgl": "not-a-preset"},
        )
        assert rejected.status_code == 422


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_native_webgl_info_is_consistent():
    binary, _, capabilities = probe_capabilities(default_binary())
    assert WEBGL_INFO_CAPABILITY in capabilities
    assert WEBGL_PARAMS_CAPABILITY in capabilities
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=str(binary),
            headless=True,
            args=[
                "--moltskin-webgl-vendor=Google Inc. (Apple)",
                "--moltskin-webgl-renderer=ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)",
                "--moltskin-webgl-profile=windows",
            ],
        )
        try:
            page = browser.new_page()
            page.set_content("<html><body>webgl probe</body></html>")
            values = page.evaluate(
                """() => {
                  const gl = document.createElement('canvas').getContext('webgl');
                  const ext = gl.getExtension('WEBGL_debug_renderer_info');
                  return {
                    vendor: gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
                    renderer: gl.getParameter(ext.UNMASKED_RENDERER_WEBGL),
                    maskedVendor: gl.getParameter(gl.VENDOR),
                    maskedRenderer: gl.getParameter(gl.RENDERER),
                    maxTexture: gl.getParameter(gl.MAX_TEXTURE_SIZE),
                    viewportDims: Array.from(gl.getParameter(gl.MAX_VIEWPORT_DIMS)),
                    astc: Boolean(gl.getExtension('WEBGL_compressed_texture_astc')),
                    astcSupported: gl.getSupportedExtensions().includes('WEBGL_compressed_texture_astc'),
                  };
                }"""
            )
        finally:
            browser.close()
    assert values["vendor"] == "Google Inc. (Apple)"
    assert values["renderer"] == "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"
    assert values["maskedVendor"] == "WebKit"
    assert values["maskedRenderer"] == "WebKit WebGL"
    assert values["maxTexture"] == 16384
    assert values["viewportDims"] == [32767, 32767]
    assert values["astc"] is False
    assert values["astcSupported"] is False
