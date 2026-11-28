"""Chrome brand, media codec, WebGPU and WebGL2 profile consistency."""

import http.server
import os
import socketserver
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from chromium.native import BRAND_CAPABILITY, WEBGPU_CAPABILITY, WEBGL_INFO_CAPABILITY, default_binary, probe_capabilities
from moltskin.services.core import chromium


def test_launcher_adds_chrome_brand_for_chrome_uas(tmp_path, monkeypatch):
    capabilities = frozenset({BRAND_CAPABILITY})
    monkeypatch.setattr(chromium, "resolve_build",
                        lambda version: (tmp_path / "Chromium", "158.0.8127.88", capabilities))
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path, "standard", True,
        chromium_user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    )
    assert "--moltskin-brand=chrome" in args
    assert "--moltskin-brand-version=126.0.0.0" in args
    assert "--moltskin-brand-full-version=126.0.0.0" in args

    args, _ = chromium.prepare_launch(
        tmp_path, "standard", True,
        chromium_user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    )
    assert "--moltskin-brand=chrome" not in args


def test_launcher_brand_defaults_to_full_build(tmp_path, monkeypatch):
    capabilities = frozenset({BRAND_CAPABILITY})
    monkeypatch.setattr(chromium, "resolve_build",
                        lambda version: (tmp_path / "Chromium", "158.0.8127.88", capabilities))
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(tmp_path, "standard", True)
    assert "--moltskin-brand-version=158.0.0.0" in args
    assert "--moltskin-brand-full-version=158.0.8127.88" in args


class _Handler(http.server.BaseHTTPRequestHandler):
    headers = {}

    def do_GET(self):
        type(self).headers = {key.lower(): value for key, value in self.headers.items()}
        body = b"<html><body>probe</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


PROBE = """async () => {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 32;
  const gl = canvas.getContext('webgl2');
  const video = document.createElement('video');
  const result = {
    ua: navigator.userAgent,
    brands: navigator.userAgentData.brands,
    high: await navigator.userAgentData.getHighEntropyValues(['fullVersionList']),
    h264: video.canPlayType('video/mp4; codecs="avc1.42E01E"'),
    aac: video.canPlayType('audio/mp4; codecs="mp4a.40.2"'),
    mse: MediaSource.isTypeSupported('video/mp4; codecs="avc1.42E01E"'),
  };
  if (gl) {
    result.webgl2 = {
      samples: gl.getParameter(gl.MAX_SAMPLES),
      ubo: gl.getParameter(gl.MAX_UNIFORM_BUFFER_BINDINGS),
      point: Array.from(gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE)),
      highInt: (() => {
        const p = gl.getShaderPrecisionFormat(gl.VERTEX_SHADER, gl.HIGH_INT);
        return [p.rangeMin, p.rangeMax, p.precision];
      })(),
      lowFloat: (() => {
        const p = gl.getShaderPrecisionFormat(gl.FRAGMENT_SHADER, gl.LOW_FLOAT);
        return [p.rangeMin, p.rangeMax, p.precision];
      })(),
      vertexComponents: gl.getParameter(gl.MAX_VERTEX_UNIFORM_COMPONENTS),
      fragmentComponents: gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_COMPONENTS),
    };
  }
  if (navigator.gpu) {
    const adapter = await navigator.gpu.requestAdapter();
    if (adapter) {
      result.gpu = {
        vendor: adapter.info.vendor,
        architecture: adapter.info.architecture,
      };
    }
  }
  return result;
}"""


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs Chromium")
def test_native_chrome_brand_codecs_and_gpu_profile():
    binary, _, capabilities = probe_capabilities(default_binary())
    assert BRAND_CAPABILITY in capabilities and WEBGPU_CAPABILITY in capabilities
    server = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    args = [
        "--moltskin-brand=chrome",
        "--moltskin-brand-version=158.0.0.0",
        "--moltskin-brand-full-version=158.0.8127.88",
        "--moltskin-webgl-profile=windows",
        "--moltskin-webgpu-vendor=nvidia",
        "--moltskin-webgpu-architecture=ampere",
        "--moltskin-webgpu-device=NVIDIA GeForce RTX 3060",
    ]
    try:
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=str(binary), headless=True, args=args)
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/")
                result = page.evaluate(PROBE)
            finally:
                browser.close()
    finally:
        server.shutdown()

    assert "Chrome/158.0.0.0" in result["ua"] or "HeadlessChrome/158.0.0.0" in result["ua"]
    assert {"brand": "Google Chrome", "version": "158"} in result["brands"]
    assert {"brand": "Google Chrome", "version": "158.0.8127.88"} in result["high"]["fullVersionList"]
    headers = {key.lower(): value for key, value in _Handler.headers.items()}
    assert '"Google Chrome";v="158"' in headers["sec-ch-ua"]
    assert result["h264"] == "probably" and result["aac"] == "probably"
    assert result["mse"] is True
    assert result["webgl2"]["samples"] == 4
    assert result["webgl2"]["ubo"] == 24
    assert result["webgl2"]["point"] == [1.0, 1024.0]
    assert result["webgl2"]["highInt"] == [31, 30, 0]
    assert result["webgl2"]["lowFloat"] == [127, 127, 23]
    assert result["webgl2"]["vertexComponents"] == 16384
    assert result["webgl2"]["fragmentComponents"] == 4096
    if "gpu" in result:
        assert result["gpu"]["vendor"] == "nvidia"
        assert result["gpu"]["architecture"] == "ampere"
