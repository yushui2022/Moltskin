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
  const read = canvas => Array.from(
    canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data);
  const load = src => new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
  const decode = async src => {
    const img = await load(src);
    const canvas = document.createElement('canvas');
    canvas.width = img.width;
    canvas.height = img.height;
    const context = canvas.getContext('2d');
    context.drawImage(img, 0, 0);
    return Array.from(context.getImageData(0, 0, canvas.width, canvas.height).data);
  };

  // Known-pixel probes must stay byte-exact.
  const pure = document.createElement('canvas');
  pure.width = pure.height = 16;
  const pureContext = pure.getContext('2d');
  pureContext.fillStyle = '#808080';
  pureContext.fillRect(0, 0, 16, 16);

  const grid = document.createElement('canvas');
  grid.width = grid.height = 8;
  const gridContext = grid.getContext('2d');
  for (let y = 0; y < 8; ++y) {
    for (let x = 0; x < 8; ++x) {
      gridContext.fillStyle = `rgb(${x * 31}, ${y * 31}, ${(x + y) * 15})`;
      gridContext.fillRect(x, y, 1, 1);
    }
  }

  const single = document.createElement('canvas');
  single.width = single.height = 1;
  const singleContext = single.getContext('2d');
  singleContext.fillStyle = '#808080';
  singleContext.fillRect(0, 0, 1, 1);

  // Organic 2D drawing: text, a stroked arc and a gradient.
  const organic = document.createElement('canvas');
  organic.width = 96;
  organic.height = 64;
  const context = organic.getContext('2d');
  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, 96, 64);
  context.fillStyle = '#123456';
  context.font = '20px Arial';
  context.fillText('Moltskin', 2, 30);
  context.strokeStyle = '#ff0000';
  context.lineWidth = 3;
  context.beginPath();
  context.arc(60, 32, 20, 0, Math.PI * 2);
  context.stroke();
  const gradient = context.createLinearGradient(0, 40, 96, 64);
  gradient.addColorStop(0, '#00ff00');
  gradient.addColorStop(1, '#0000ff');
  context.fillStyle = gradient;
  context.fillRect(0, 40, 96, 24);

  const full = read(organic);
  const crop = Array.from(context.getImageData(5, 5, 20, 20).data);
  const dataUrl = organic.toDataURL('image/png');
  const blob = await new Promise(resolve => organic.toBlob(resolve, 'image/png'));
  const fromBlob = await decode(URL.createObjectURL(blob));
  const fromUrl = await decode(dataUrl);

  // putImageData is a protected region even when text was drawn first.
  const put = document.createElement('canvas');
  put.width = put.height = 64;
  const putContext = put.getContext('2d');
  putContext.fillStyle = '#ffffff';
  putContext.fillRect(0, 0, 64, 64);
  putContext.fillStyle = '#000000';
  putContext.font = '18px Arial';
  putContext.fillText('TEXT', 2, 20);
  const imageData = putContext.createImageData(16, 16);
  for (let i = 0; i < imageData.data.length; i += 4) {
    imageData.data[i] = 255;
    imageData.data[i + 1] = 0;
    imageData.data[i + 2] = 0;
    imageData.data[i + 3] = 255;
  }
  putContext.putImageData(imageData, 40, 40);
  const putPixels = read(put);

  // OffscreenCanvas source must carry noise eligibility through drawImage.
  const source = document.createElement('canvas');
  source.width = 48;
  source.height = 32;
  const sourceContext = source.getContext('2d');
  sourceContext.fillStyle = '#ffffff';
  sourceContext.fillRect(0, 0, 48, 32);
  sourceContext.fillStyle = '#204060';
  sourceContext.font = '16px Arial';
  sourceContext.fillText('copy', 2, 20);
  const target = document.createElement('canvas');
  target.width = 48;
  target.height = 32;
  const targetContext = target.getContext('2d');
  targetContext.drawImage(source, 0, 0);
  const targetPixels = read(target);

  // WebGL: a flat clear is a probe, a shaded triangle is organic.
  const glCanvas = document.createElement('canvas');
  glCanvas.width = glCanvas.height = 32;
  const gl = glCanvas.getContext('webgl');
  gl.clearColor(0.5, 0.5, 0.5, 1);
  gl.clear(gl.COLOR_BUFFER_BIT);
  const glClear = new Uint8Array(32 * 32 * 4);
  gl.readPixels(0, 0, 32, 32, gl.RGBA, gl.UNSIGNED_BYTE, glClear);
  const glClearUrl = glCanvas.toDataURL('image/png');

  const vertexSource = `attribute vec2 p; varying vec2 v; void main() {
    v = p; gl_Position = vec4(p, 0.0, 1.0);
  }`;
  const fragmentSource = `precision mediump float; varying vec2 v; void main() {
    gl_FragColor = vec4(v * 0.5 + 0.5, 0.5, 1.0);
  }`;
  const shader = (type, source) => {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    return shader;
  };
  const program = gl.createProgram();
  gl.attachShader(program, shader(gl.VERTEX_SHADER, vertexSource));
  gl.attachShader(program, shader(gl.FRAGMENT_SHADER, fragmentSource));
  gl.linkProgram(program);
  gl.useProgram(program);
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, 0, 1]), gl.STATIC_DRAW);
  const position = gl.getAttribLocation(program, 'p');
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  const glPixels = new Uint8Array(32 * 32 * 4);
  gl.readPixels(0, 0, 32, 32, gl.RGBA, gl.UNSIGNED_BYTE, glPixels);
  const glFlipped = [];
  for (let y = 31; y >= 0; --y) {
    for (let x = 0; x < 32; ++x) {
      for (let channel = 0; channel < 4; ++channel) {
        glFlipped.push(glPixels[(y * 32 + x) * 4 + channel]);
      }
    }
  }
  const glUrl = glCanvas.toDataURL('image/png');
  const glFromUrl = await decode(glUrl);

  return {
    pure: read(pure), pureUrl: pure.toDataURL('image/png'),
    grid: read(grid), gridUrl: grid.toDataURL('image/png'),
    single: Array.from(singleContext.getImageData(0, 0, 1, 1).data),
    full, crop, dataUrl, fromUrl, fromBlob,
    put: putPixels,
    target: targetPixels,
    glClear: Array.from(glClear), glClearUrl,
    glFlipped, glUrl, glFromUrl,
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


def _diff_count(first, second):
    return sum(left != right for left, right in zip(first, second))


def _assert_one_channel_deltas(baseline, noisy):
    changed = 0
    for index in range(0, len(noisy), 4):
        if noisy[index:index + 4] == baseline[index:index + 4]:
            continue
        changed += 1
        assert noisy[index + 3] == baseline[index + 3]
        deltas = [noisy[index + channel] - baseline[index + channel] for channel in range(3)]
        assert sum(delta != 0 for delta in deltas) == 1
        assert abs(next(delta for delta in deltas if delta)) == 1
    assert changed > 0


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_native_canvas_and_webgl_noise_are_stable():
    baseline = _read_probe([])
    noisy = _read_probe(["--moltskin-canvas-noise=12345"])
    noisy_again = _read_probe(["--moltskin-canvas-noise=12345"])
    other = _read_probe(["--moltskin-canvas-noise=67890"])

    # Known-pixel probes are byte-exact.
    assert noisy["pure"] == baseline["pure"]
    assert noisy["pureUrl"] == baseline["pureUrl"]
    assert noisy["grid"] == baseline["grid"]
    assert noisy["gridUrl"] == baseline["gridUrl"]
    assert noisy["single"] == baseline["single"] == [128, 128, 128, 255]

    # Organic 2D rendering is stable, seed-specific and sparse.
    assert noisy["full"] == noisy_again["full"]
    assert noisy["full"] != baseline["full"]
    assert noisy["full"] != other["full"]
    assert noisy["dataUrl"] == noisy_again["dataUrl"]
    assert noisy["dataUrl"] != baseline["dataUrl"]
    _assert_one_channel_deltas(baseline["full"], noisy["full"])

    # getImageData crop, toDataURL and toBlob agree byte-for-byte.
    full = noisy["full"]
    for y in range(20):
        for x in range(20):
            crop_index = (y * 20 + x) * 4
            full_index = ((y + 5) * 96 + (x + 5)) * 4
            assert noisy["crop"][crop_index:crop_index + 4] == full[full_index:full_index + 4]
    assert noisy["fromUrl"] == full
    assert noisy["fromBlob"] == full

    # putImageData stays exact while text remains perturbed.
    for y in range(64):
        for x in range(64):
            for channel in range(4):
                index = (y * 64 + x) * 4 + channel
                if 40 <= x < 56 and 40 <= y < 56:
                    assert noisy["put"][index] == baseline["put"][index]
    assert _diff_count(baseline["put"], noisy["put"]) > 0

    # drawImage(canvas) carries eligibility to the destination.
    assert noisy["target"] != baseline["target"]
    _assert_one_channel_deltas(baseline["target"], noisy["target"])

    # Flat gl.clear() is untouched; a shaded triangle is perturbed consistently.
    assert noisy["glClear"] == baseline["glClear"]
    assert noisy["glClearUrl"] == baseline["glClearUrl"]
    assert noisy["glFlipped"] == noisy_again["glFlipped"]
    assert noisy["glFlipped"] != baseline["glFlipped"]
    assert noisy["glFlipped"] != other["glFlipped"]
    assert noisy["glFlipped"] == noisy["glFromUrl"]
    assert noisy["glUrl"] == noisy_again["glUrl"]
    _assert_one_channel_deltas(baseline["glFlipped"], noisy["glFlipped"])
