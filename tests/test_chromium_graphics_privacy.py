"""Exercise the two independent graphics policies in real, persistent dashboard profiles."""

import json
import os
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from moltskin.services.core import config, main
from moltskin.services.core.security import get_token


PROBE = """
async function checkGraphicsAPIs(makeCanvas) {
  async function read(fn) {
    try { return await fn(); } catch (e) { return e.name; }
  }
  const canvas = makeCanvas();
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#00ff00';
  ctx.fillRect(0, 0, 1, 1);
  const pixel = await read(() => [...ctx.getImageData(0, 0, 1, 1).data]);
  const png = await read(async () => {
    let blob;
    if (canvas.convertToBlob) blob = await canvas.convertToBlob();
    else blob = await new Promise(resolve => canvas.toBlob(resolve));
    return blob?.type === 'image/png' && blob.size > 0;
  });
  const result = {pixel, png};
  if (canvas.toDataURL) result.url = await read(() => canvas.toDataURL().startsWith('data:image/png;base64,'));
  for (const kind of ['webgl', 'webgl2']) {
    const gl = makeCanvas().getContext(kind);
    if (!gl) throw Error(kind + ' context unavailable');
    const name = 'WEBGL_debug_renderer_info';
    const listed = gl.getSupportedExtensions().includes(name);
    const enabled = !!gl.getExtension(name);
    const queries = [0x9245, 0x9246].map(key => {
      const value = gl.getParameter(key);
      const error = gl.getError();
      return typeof value === 'string' && value.length > 0 && error === gl.NO_ERROR ? 'available' :
        value === null && error === gl.INVALID_ENUM ? 'restricted' : 'unexpected';
    });
    gl.clearColor(1, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    const color = new Uint8Array(4);
    gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, color);
    result[kind] = {listed, enabled, queries, rendered: [...color], ok: gl.getError() === gl.NO_ERROR};
    if (kind === 'webgl2') {
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, gl.createBuffer());
      gl.bufferData(gl.PIXEL_PACK_BUFFER, 4, gl.STREAM_READ);
      gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, 0);
      const packed = new Uint8Array(4);
      gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, packed);
      result[kind].packed = [...packed];
      result[kind].ok &&= gl.getError() === gl.NO_ERROR;
    }
  }
  return result;
}
"""
WORKER = PROBE + """
checkGraphicsAPIs(() => new OffscreenCanvas(2, 2)).then(postMessage.bind(self), e => postMessage({error: String(e)}));
"""
REPORT = PROBE + """
(async () => {
  const worker = new Worker('/worker.js');
  const background = new Promise((resolve, reject) => {
    worker.onmessage = e => resolve(e.data);
    worker.onerror = e => reject(Error(e.message));
  });
  try {
    const page = await checkGraphicsAPIs(() => document.createElement('canvas'));
    return {page, worker: await background,
      canvasLabel: document.querySelector('#canvasReadback').textContent,
      glLabel: document.querySelector('#webglDebugInfo').textContent};
  } finally { worker.terminate(); }
})().catch(e => ({error: String(e)})).then(body => fetch('/report', {method:'POST', body:JSON.stringify(body)}));
"""


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the patched Chromium build")
def test_graphics_privacy_per_window_and_after_restart(data_dir, monkeypatch):
    reports = Queue()
    site_page = (config.WEB_DIR / "runtime.html").read_bytes() + b"<script>" + REPORT.encode() + b"</script>"

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            routes = {
                "/": ("text/html; charset=utf-8", site_page), "/worker.js": ("text/javascript", WORKER.encode()),
            }
            if self.path not in routes:
                self.send_error(404)
                return
            content_type, body = routes[self.path]
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            assert self.path == "/report"
            reports.put(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Site) as server, ExitStack() as stack:
        thread = Thread(target=server.serve_forever)
        thread.start()
        stack.callback(thread.join)
        stack.callback(server.shutdown)
        monkeypatch.setattr(config, "OPEN_MANAGER", True)
        monkeypatch.setattr(config, "local_dashboard_url", lambda: f"http://127.0.0.1:{server.server_port}/")
        api = stack.enter_context(TestClient(main.app, headers={"Authorization": "Bearer " + get_token()}))

        def create(name, **settings):
            row = api.post("/api/v1/profiles", json={"engine": "chromium", "headless": True, "name": name,
                                                      "startup_page": "dashboard", **settings}).raise_for_status()
            return "/api/v1/profiles/" + row.json()["data"]["id"]

        @contextmanager
        def verify(profile, canvas_blocked, gl_blocked):
            api.post(profile + "/launch").raise_for_status()
            try:
                report = reports.get(timeout=30)
                assert "error" not in report, report
                assert report["canvasLabel"] == ("已阻止读取" if canvas_blocked else "可读取测试像素")
                if gl_blocked:
                    assert report["glLabel"] == "调试扩展不可用"
                else:
                    assert report["glLabel"] not in ("", "—", "WebGL 不可用", "调试扩展不可用", "扩展未提供型号")
                for context in (report["page"], report["worker"]):
                    assert context["pixel"] == ("SecurityError" if canvas_blocked else [0, 255, 0, 255])
                    assert context["png"] == ("SecurityError" if canvas_blocked else True)
                    for kind in ("webgl", "webgl2"):
                        expected = {
                            "listed": not gl_blocked, "enabled": not gl_blocked,
                            "queries": ["restricted" if gl_blocked else "available"] * 2,
                            "rendered": [255, 0, 0, 255], "ok": True,
                        }
                        if kind == "webgl2":
                            expected["packed"] = [255, 0, 0, 255]
                        assert context[kind] == expected
                assert report["page"]["url"] == ("SecurityError" if canvas_blocked else True)
                yield
            finally:
                api.post(profile + "/stop").raise_for_status()

        standard = create("standard")
        restricted = create("restricted", canvas_readback="blocked", webgl_debug_info="blocked")
        with verify(standard, False, False), verify(restricted, True, True):
            pass
        with verify(restricted, True, True):
            pass
        api.patch(standard, json={"canvas_readback": "blocked"}).raise_for_status()
        api.patch(restricted, json={"canvas_readback": "standard"}).raise_for_status()
        with verify(standard, True, False), verify(restricted, False, True):
            pass
