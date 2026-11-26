"""Deterministic Canvas 2D and WebGL pixel noise."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from chromium.native import CANVAS_NOISE_CAPABILITY, default_binary, probe_capabilities
from moltskin.services.core import chromium


def test_launcher_passes_canvas_noise_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (tmp_path / "Chromium", "158.0.0.0", frozenset({CANVAS_NOISE_CAPABILITY})),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        canvas_noise_seed=12345,
    )
    assert "--moltskin-canvas-noise=12345" in args


def test_canvas_noise_seed_is_stable_and_profile_specific():
    first = chromium.canvas_noise_seed("pf_alpha")
    assert first == chromium.canvas_noise_seed("pf_alpha")
    assert 0 < first <= 0xFFFFFFFF
    assert first != chromium.canvas_noise_seed("pf_beta")


def test_editor_exposes_canvas_noise_field():
    html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="fCanvasNoise"' in html
    assert 'id="bCanvasNoise"' in html
    assert 'values.chromium_canvas_noise' in html
    assert 'p.chromium_canvas_noise' in html


def test_profile_canvas_noise_persists_and_exports(data_dir):
    from fastapi.testclient import TestClient

    from moltskin.services.core import main
    from moltskin.services.core.security import get_token

    headers = {"Authorization": f"Bearer {get_token()}"}
    with TestClient(main.app) as client:
        created = client.post(
            "/api/v1/profiles",
            headers=headers,
            json={
                "name": "noise-profile",
                "engine": "chromium",
                "headless": True,
                "chromium_canvas_noise": True,
            },
        )
        assert created.status_code == 201, created.text
        profile = created.json()["data"]
        assert profile["chromium_canvas_noise"] is True

        exported = client.get("/api/v1/export/profiles", headers=headers)
        assert exported.status_code == 200
        assert exported.json()["version"] == 14
        assert exported.json()["profiles"][0]["chromium_canvas_noise"] is True

        engine = client.get("/api/v1/engine", headers=headers)
        assert engine.status_code == 200
        assert engine.json()["data"]["chromium"]["supports_canvas_noise_policy"] is True

        rejected = client.post(
            "/api/v1/profiles",
            headers=headers,
            json={"name": "camoufox-noise", "engine": "camoufox", "chromium_canvas_noise": True},
        )
        assert rejected.status_code == 422


NOISE_PROBE = """async () => {
  const canvas = document.createElement('canvas');
  canvas.width = 16;
  canvas.height = 16;
  const context = canvas.getContext('2d');
  context.fillStyle = '#808080';
  context.fillRect(0, 0, 16, 16);
  const read = () => Array.from(context.getImageData(0, 0, 16, 16).data);
  const first = read();
  const second = read();
  const crop = Array.from(context.getImageData(4, 4, 2, 2).data);
  const dataUrl = canvas.toDataURL('image/png');
  const dataUrlAgain = canvas.toDataURL('image/png');
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
  const blobAgain = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
  const blobBytes = new Uint8Array(await blob.arrayBuffer());
  const blobAgainBytes = new Uint8Array(await blobAgain.arrayBuffer());
  const hash = values => values.reduce((value, current) => ((value * 31 + current) >>> 0), 0);

  const glCanvas = document.createElement('canvas');
  glCanvas.width = 4;
  glCanvas.height = 4;
  const gl = glCanvas.getContext('webgl');
  gl.clearColor(0.5, 0.5, 0.5, 1);
  gl.clear(gl.COLOR_BUFFER_BIT);
  const pixels = new Uint8Array(4 * 4 * 4);
  gl.readPixels(0, 0, 4, 4, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
  const webglFirst = Array.from(pixels);
  gl.readPixels(0, 0, 4, 4, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
  const glDataUrl = glCanvas.toDataURL('image/png');
  const glDataUrlAgain = glCanvas.toDataURL('image/png');
  return {
    first, second, crop,
    dataUrl, dataUrlAgain,
    blobHash: hash(blobBytes), blobAgainHash: hash(blobAgainBytes),
    blobSize: blob.size,
    glDataUrl, glDataUrlAgain,
    webglFirst, webglSecond: Array.from(pixels),
  };
}"""


def _read_probe(args):
    binary, _, capabilities = probe_capabilities(default_binary())
    assert CANVAS_NOISE_CAPABILITY in capabilities
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=str(binary),
            headless=True,
            args=args,
        )
        try:
            page = browser.new_page()
            page.set_content("<html><body>canvas noise probe</body></html>")
            return page.evaluate(NOISE_PROBE)
        finally:
            browser.close()


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_native_canvas_and_webgl_noise_are_stable():
    baseline = _read_probe([])
    noisy = _read_probe(["--moltskin-canvas-noise=12345"])
    other = _read_probe(["--moltskin-canvas-noise=67890"])

    assert baseline["first"] == baseline["second"]
    assert noisy["first"] == noisy["second"]
    assert other["first"] == other["second"]
    assert noisy["first"] != baseline["first"]
    assert other["first"] != baseline["first"]
    assert noisy["first"] != other["first"]

    for index in range(0, len(noisy["first"]), 4):
        assert noisy["first"][index + 3] == 255
        for channel in range(3):
            assert abs(noisy["first"][index + channel] - 128) <= 1

    full = noisy["first"]
    for y in range(2):
        for x in range(2):
            crop_index = (y * 2 + x) * 4
            full_index = ((y + 4) * 16 + (x + 4)) * 4
            assert noisy["crop"][crop_index:crop_index + 4] == full[full_index:full_index + 4]

    assert noisy["dataUrl"] == noisy["dataUrlAgain"]
    assert noisy["dataUrl"] != baseline["dataUrl"]
    assert noisy["blobHash"] == noisy["blobAgainHash"]
    assert noisy["blobHash"] != baseline["blobHash"]

    assert noisy["glDataUrl"] == noisy["glDataUrlAgain"]
    assert noisy["glDataUrl"] != baseline["glDataUrl"]

    assert noisy["webglFirst"] == noisy["webglSecond"]
    assert noisy["webglFirst"] != baseline["webglFirst"]
    for index in range(0, len(noisy["webglFirst"]), 4):
        assert noisy["webglFirst"][index + 3] == 255
        for channel in range(3):
            assert abs(noisy["webglFirst"][index + channel] - 128) <= 1
