"""Render known WebGL pixels and an offline Web Audio signal in native Chromium."""

import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from playwright.sync_api import sync_playwright

from chromium.launch_preview import permission_switches
from chromium.native import default_binary, probe_capabilities


PAGE = """<!doctype html><meta charset="utf-8"><canvas width="16" height="16"></canvas>
<script>
function drawTriangle() {
  const gl = document.querySelector('canvas').getContext('webgl', {antialias: false});
  if (!gl) throw new Error('Unable to create WebGL context');
  const program = gl.createProgram();
  function attach(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    gl.attachShader(program, shader);
  }
  attach(gl.VERTEX_SHADER, 'attribute vec2 p; void main(){gl_Position=vec4(p,0.0,1.0);}');
  attach(gl.FRAGMENT_SHADER, 'precision mediump float; void main(){gl_FragColor=vec4(0.0,1.0,0.0,1.0);}');
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
  gl.useProgram(program);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1]), gl.STATIC_DRAW);
  const position = gl.getAttribLocation(program, 'p');
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
  gl.clearColor(0, 0, 1, 1);
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  function pixel(x, y) {
    const rgba = new Uint8Array(4);
    gl.readPixels(x, y, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, rgba);
    return Array.from(rgba);
  }
  const result = {inside: pixel(2, 2), outside: pixel(13, 13)};
  if (gl.getError() !== gl.NO_ERROR) throw new Error('WebGL draw or read failed');
  return result;
}

async function renderAudio() {
  const ctx = new OfflineAudioContext(1, 256, 44100);
  const source = ctx.createBufferSource();
  source.buffer = ctx.createBuffer(1, 256, 44100);
  const samples = source.buffer.getChannelData(0);
  samples.fill(1, 0, 128);
  samples.fill(-1, 128);
  const gain = ctx.createGain();
  gain.gain.value = 0.25;
  source.connect(gain).connect(ctx.destination);
  source.start();
  const rendered = await ctx.startRendering();
  return Array.from(rendered.getChannelData(0));
}
</script>"""


@pytest.fixture
def local_page(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(PAGE, encoding="utf-8")
    with ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=site)) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/"
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium build")
@pytest.mark.parametrize("restricted", [False, True], ids=["standard", "restricted"])
def test_webgl_drawing_and_offline_audio(restricted, local_page, tmp_path):
    binary, _, capabilities = probe_capabilities(default_binary())
    policies = {
        "local_fonts": "restricted" if restricted else "standard",
        "media_capture": "blocked" if restricted else "standard",
        "geolocation": "blocked" if restricted else "standard",
    }
    with sync_playwright() as driver:
        with driver.chromium.launch_persistent_context(
            tmp_path / "browser", executable_path=str(binary), headless=True,
            args=permission_switches(policies, capabilities),
        ) as context:
            page = context.pages[0]
            page.goto(local_page)
            assert page.evaluate("drawTriangle()") == {"inside": [0, 255, 0, 255], "outside": [0, 0, 255, 255]}
            assert page.evaluate("renderAudio()") == [0.25] * 128 + [-0.25] * 128
