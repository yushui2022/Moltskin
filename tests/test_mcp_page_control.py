"""Stealth CDP page-level control and MCP tool integration tests."""

from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import threading

from fastapi.testclient import TestClient
import pytest

from chromium.native import CDP_STEALTH_CAPABILITY, default_binary, probe_capabilities
from moltskin.services.core import cdp, mcp_server


def test_mcp_exposes_page_level_tools():
    tool_names = {tool.name for tool in mcp_server.mcp._tool_manager.list_tools()}
    expected = {
        "list_pages",
        "new_page",
        "close_page",
        "navigate_page",
        "snapshot_page",
        "click_page",
        "fill_page",
        "press_key",
        "evaluate_page",
        "screenshot_page",
    }
    assert expected <= tool_names


def test_page_endpoints_reject_stopped_or_camoufox_profiles(data_dir):
    from moltskin.services.core import main
    from moltskin.services.core.security import get_token

    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as client:
        fox = client.post(
            "/api/v1/profiles", json={"name": "Fox", "engine": "camoufox"}
        ).raise_for_status().json()["data"]
        res_fox = client.get(f"/api/v1/profiles/{fox['id']}/pages")
        assert res_fox.status_code == 422
        assert res_fox.json()["error"]["code"] == "ENGINE_UNSUPPORTED"

        chrome = client.post(
            "/api/v1/profiles", json={"name": "Chrome Stopped", "engine": "chromium"}
        ).raise_for_status().json()["data"]
        res_stopped = client.post(
            f"/api/v1/profiles/{chrome['id']}/pages/navigate",
            json={"url": "https://example.com"},
        )
        assert res_stopped.status_code == 409
        assert res_stopped.json()["error"]["code"] == "NOT_RUNNING"


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_native_build_reports_cdp_stealth_and_windows_debug_shaders_hlsl():
    from playwright.sync_api import sync_playwright

    binary, _, capabilities = probe_capabilities(default_binary())
    assert CDP_STEALTH_CAPABILITY in capabilities

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=str(binary),
            headless=True,
            args=["--moltskin-hide-webdriver", "--moltskin-webgl-profile=windows"],
        )
        try:
            page = browser.new_page()
            page.set_content("<html><body>shader test</body></html>")
            info = page.evaluate(
                """() => {
                  const canvas = document.createElement('canvas');
                  const gl = canvas.getContext('webgl2');
                  const ext = gl.getExtension('WEBGL_debug_shaders');
                  const vs = gl.createShader(gl.VERTEX_SHADER);
                  gl.shaderSource(vs, 'void main() { gl_Position = vec4(0.0); }');
                  gl.compileShader(vs);
                  return {
                    webdriver: navigator.webdriver,
                    hlsl: ext ? ext.getTranslatedShaderSource(vs) : '',
                  };
                }"""
            )
            assert info["webdriver"] is False
            assert "metal_stdlib" not in info["hlsl"]
            assert "cbuffer DriverConstants" in info["hlsl"]
            assert "SV_Position" in info["hlsl"]
        finally:
            browser.close()


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_stealth_page_control_defeats_cdp_traps_and_drives_trusted_ui(data_dir):
    from moltskin.services.core import main
    from moltskin.services.core.security import get_token

    probe_capabilities(default_binary())

    html = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Stealth Trap & Form</title></head>
<body>
  <h1>Stealth Verification</h1>
  <form id="loginForm" onsubmit="event.preventDefault(); window.__submitted = true;">
    <input id="userInput" name="username" placeholder="Enter handle" value="old_value">
    <input id="secretPwd" type="password" value="top_secret_123">
    <select id="roleSelect"><option value="viewer">Viewer</option><option value="admin">Admin</option></select>
    <button id="actionBtn" type="button">Verify Action</button>
  </form>
  <div id="resultBox">idle</div>
  <script>
    window.__cdpTrapsTriggered = [];
    window.__clickTrusted = null;

    document.getElementById('actionBtn').addEventListener('click', (ev) => {
      window.__clickTrusted = ev.isTrusted;
      document.getElementById('resultBox').textContent =
        'clicked:' + document.getElementById('userInput').value + ':' + document.getElementById('roleSelect').value;
    });

    window.runAntiBotProbes = async () => {
      let trapped = [];

      // 1. Error.stack getter trap
      const err = new Error('probe');
      Object.defineProperty(err, 'stack', {
        get() { trapped.push('error_stack'); return ''; }
      });
      console.debug(err);
      console.log(err);

      // 2. HTMLElement.id getter trap (Cloudflare / Brotector Runtime.enable probe)
      const div = document.createElement('div');
      Object.defineProperty(div, 'id', {
        get() { trapped.push('element_id'); return 'x'; }
      });
      console.debug(div);

      // 3. console.table getter trap
      const row = {};
      Object.defineProperty(row, 'probeCol', {
        get() { trapped.push('console_table'); return 1; }
      });
      console.table([row]);

      // 4. Proxy trap in console.dir
      const p = new Proxy({}, {
        get(t, k) {
          if (k !== Symbol.toPrimitive && k !== 'toString' && k !== 'valueOf' && k !== Symbol.toStringTag) {
            trapped.push('proxy_' + String(k));
          }
          return undefined;
        },
        ownKeys() { trapped.push('proxy_ownKeys'); return []; }
      });
      console.dir(p);

      // 5. Worker navigator.webdriver check (W3C WebDriver exposes webdriver on Window Navigator only)
      const workerBlob = new Blob(
        ['postMessage({ truthy: Boolean(self.navigator.webdriver) });'],
        { type: 'application/javascript' }
      );
      const workerRes = await new Promise((resolve) => {
        const w = new Worker(URL.createObjectURL(workerBlob));
        w.onmessage = (e) => resolve(e.data);
      });

      return {
        webdriver: navigator.webdriver,
        workerWebdriver: workerRes.truthy,
        trapped,
      };
    };
  </script>
</body>
</html>
""".encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header(
                "Set-Cookie",
                "live_login=captured_on_stop_888; Path=/; Max-Age=86400; HttpOnly; SameSite=Lax",
            )
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *_args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    try:
        with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as client:
            profile = client.post(
                "/api/v1/profiles",
                json={
                    "name": "MCP Stealth Runner",
                    "engine": "chromium",
                    "headless": True,
                    "startup_page": "blank",
                },
            ).raise_for_status().json()["data"]
            pid = profile["id"]
            base = f"/api/v1/profiles/{pid}"

            client.post(f"{base}/launch").raise_for_status()
            try:
                # 1. Navigate
                nav = client.post(
                    f"{base}/pages/navigate",
                    json={"url": f"http://127.0.0.1:{port}/", "wait_until": "load"},
                ).raise_for_status().json()["data"]
                assert nav["title"] == "Stealth Trap & Form"
                assert nav["ready_state"] == "complete"

                # 2. List pages
                pages = client.get(f"{base}/pages").raise_for_status().json()["data"]["pages"]
                assert any(p["target_id"] == nav["target_id"] for p in pages)

                # 3. Snapshot (verifies isolated world DOM inspection + password redaction)
                snap = client.post(f"{base}/pages/snapshot").raise_for_status().json()["data"]
                assert "Stealth Verification" in snap["text"]
                selectors = {el["selector"]: el for el in snap["elements"]}
                assert "#userInput" in selectors
                assert "#secretPwd" in selectors
                assert selectors["#secretPwd"]["value"] == "***"

                # 4. Fill input & select + trusted Click
                client.post(
                    f"{base}/pages/fill",
                    json={"selector": "#userInput", "value": "moltskin_agent", "clear": True},
                ).raise_for_status()
                client.post(
                    f"{base}/pages/fill",
                    json={"selector": "#roleSelect", "value": "admin"},
                ).raise_for_status()
                client.post(
                    f"{base}/pages/click",
                    json={"selector": "#actionBtn"},
                ).raise_for_status()

                # 5. Evaluate anti-bot probes AND verify event.isTrusted === true!
                probe = client.post(
                    f"{base}/pages/evaluate",
                    json={
                        "expression": """(async () => ({
                          probes: await window.runAntiBotProbes(),
                          clickTrusted: window.__clickTrusted,
                          box: document.getElementById('resultBox').textContent
                        }))()""",
                        "await_promise": True,
                    },
                ).raise_for_status().json()["data"]["result"]

                assert probe["probes"] == {
                    "webdriver": False,
                    "workerWebdriver": False,
                    "trapped": [],
                }
                assert probe["clickTrusted"] is True
                assert probe["box"] == "clicked:moltskin_agent:admin"

                # 6. Screenshot returns valid PNG bytes
                shot = client.post(f"{base}/pages/screenshot").raise_for_status().json()["data"]
                raw_png = base64.b64decode(shot["data_base64"])
                assert raw_png.startswith(b"\x89PNG\r\n\x1a\n")

                # 7. Hard guard: Runtime.enable is strictly forbidden on CDPBrowserSession
                with cdp.CDPBrowserSession(profile["user_data_dir"]) as raw_session:
                    with pytest.raises(cdp.CDPError) as exc_info:
                        raw_session.call("Runtime.enable")
                    assert exc_info.value.code == "CDP_FORBIDDEN_METHOD"
            finally:
                # Stopping the profile snapshots live cookies via Storage.getCookies!
                client.post(f"{base}/stop").raise_for_status()

            # 8. Even AFTER browser stop, live Set-Cookie was snapshotted into portable store!
            saved_cookies = client.get(f"{base}/cookies").raise_for_status().json()["data"]["cookies"]
            assert any(
                c["name"] == "live_login" and c["value"] == "captured_on_stop_888"
                for c in saved_cookies
            )
    finally:
        srv.shutdown()
        srv.server_close()
