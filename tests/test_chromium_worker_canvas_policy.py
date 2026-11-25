"""Verify worker canvas policies and cross-origin image restrictions on local origins."""

import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from playwright.sync_api import sync_playwright

from chromium import native


WORKER_PROBE = """
function readPixel() {
  const ctx = new OffscreenCanvas(1, 1).getContext('2d');
  ctx.fillStyle = '#0f0';
  ctx.fillRect(0, 0, 1, 1);
  try { return Array.from(ctx.getImageData(0, 0, 1, 1).data); }
  catch (e) { return e.name; }
}
"""


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the patched Chromium")
@pytest.mark.parametrize("blocked", [False, True], ids=["standard", "restricted"])
def test_worker_readback_and_cross_origin_image_rules(tmp_path, blocked):
    binary, _, caps = native.probe_capabilities(native.default_binary())
    assert native.CANVAS_READBACK_CAPABILITY in caps
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<!doctype html><title>Canvas privacy</title>")
    (site / "image.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2">'
                                    '<rect width="2" height="2" fill="red"/></svg>')
    (site / "shared.js").write_text(WORKER_PROBE + "onconnect = e => e.ports[0].postMessage(readPixel());")
    (site / "service.js").write_text(WORKER_PROBE + "onmessage = e => e.ports[0].postMessage(readPixel());")
    with ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=site)) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            with sync_playwright() as driver:
                args = ["--enable-unsafe-swiftshader"]
                if blocked:
                    args.append("--" + native.CANVAS_READBACK_SWITCH)
                browser = driver.chromium.launch(executable_path=str(binary), chromium_sandbox=True, args=args)
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/")
                expected = "SecurityError" if blocked else [0, 255, 0, 255]
                assert page.evaluate("""async () => {
                  const worker = new SharedWorker('/shared.js');
                  return await new Promise(resolve => worker.port.onmessage = e => resolve(e.data));
                }""") == expected
                assert page.evaluate("""async () => {
                  await navigator.serviceWorker.register('/service.js');
                  const registration = await navigator.serviceWorker.ready;
                  const channel = new MessageChannel();
                  const result = new Promise(resolve => channel.port1.onmessage = e => resolve(e.data));
                  registration.active.postMessage(null, [channel.port2]);
                  return await result;
                }""") == expected
                # Same server, distinct hostname and no CORS grant for the image.
                cross_origin = f"http://localhost:{server.server_port}/image.svg"
                assert page.evaluate("""async url => {
                  const img = new Image();
                  img.src = url;
                  await img.decode();
                  function checked(fn) {
                    try { fn(); return 'allowed'; } catch (e) { return e.name; }
                  }
                  const ctx = document.createElement('canvas').getContext('2d');
                  ctx.drawImage(img, 0, 0);
                  const gl = document.createElement('canvas').getContext('webgl');
                  gl.bindTexture(gl.TEXTURE_2D, gl.createTexture());
                  return [checked(() => ctx.getImageData(0, 0, 1, 1)),
                    checked(() => gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img))];
                }""", cross_origin) == ["SecurityError", "SecurityError"]
                browser.close()
        finally:
            server.shutdown()
            thread.join()
