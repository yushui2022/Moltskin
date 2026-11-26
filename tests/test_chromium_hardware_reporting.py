"""Observe hardware privacy from normal sites, workers and opted-in HTTP requests."""

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


READ = "const read = () => [navigator.hardwareConcurrency, navigator.deviceMemory];"
MEASURE = """<!doctype html><body><script>
async function measure() {
  const frame = document.createElement('iframe');
  const frameUrl = new URL('/frame', 'http://localhost:' + location.port);
  const framed = new Promise(resolve => window.addEventListener('message', event => {
    if (event.origin === frameUrl.origin && event.source === frame.contentWindow) resolve(event.data);
  }));
  frame.src = frameUrl;
  document.body.appendChild(frame);
  const worker = new Worker('/dedicated.js');
  const dedicated = new Promise(resolve => worker.onmessage = event => resolve(event.data));
  const shared = new SharedWorker('/shared.js');
  const sharedValue = new Promise(resolve => shared.port.onmessage = event => resolve(event.data));
  await navigator.serviceWorker.register('/service.js');
  const registration = await navigator.serviceWorker.ready;
  const channel = new MessageChannel();
  const service = new Promise(resolve => channel.port1.onmessage = event => resolve(event.data));
  registration.active.postMessage(null, [channel.port2]);
  const response = await fetch('/headers');
  return {
    contexts: [read(), ...await Promise.all([framed, dedicated, sharedValue, service])],
    navigation: NAVIGATION_HEADERS, resource: await response.json(),
    first: JSON.parse(sessionStorage.getItem('first'))
  };
}
measure().catch(e => ({error: String(e)})).then(result =>
    fetch('/report', {method: 'POST', body: JSON.stringify(result)}));
</script>"""


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the patched Chromium")
def test_hardware_limit_across_contexts_headers_and_restarts(data_dir, monkeypatch):
    reports = Queue()

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            headers = json.dumps([self.headers.get(key) for key in ("Sec-CH-Device-Memory", "Device-Memory")])
            routes = {
                "/prime": ("text/html", "<script>sessionStorage.setItem('first', JSON.stringify("
                           + headers + ")); location.replace('/measure');</script>"),
                "/measure": ("text/html", MEASURE.replace("<script>", "<script>" + READ)
                            .replace("NAVIGATION_HEADERS", headers)),
                "/frame": ("text/html", "<script>" + READ
                           + "parent.postMessage(read(), 'http://127.0.0.1:' + location.port);</script>"),
                "/dedicated.js": ("text/javascript", READ + "postMessage(read());"),
                "/shared.js": ("text/javascript", READ + "onconnect = e => e.ports[0].postMessage(read());"),
                "/service.js": ("text/javascript", READ + "onmessage = e => e.ports[0].postMessage(read());"),
                "/headers": ("application/json", headers),
            }
            if self.path not in routes:
                self.send_error(404)
                return
            content_type, body = routes[self.path]
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            if self.path == "/prime":
                self.send_header("Accept-CH", "Sec-CH-Device-Memory, Device-Memory")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):
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
        monkeypatch.setattr(config, "local_dashboard_url", lambda: f"http://127.0.0.1:{server.server_port}/prime")
        api = stack.enter_context(TestClient(main.app, headers={"Authorization": "Bearer " + get_token()}))

        def create(name, **settings):
            response = api.post("/api/v1/profiles", json={"name": name, "engine": "chromium",
                                "headless": not bool(os.getenv("MOLTSKIN_TEST_X11_INPUT")),
                                "startup_page": "dashboard", **settings}).raise_for_status()
            return "/api/v1/profiles/" + response.json()["data"]["id"]

        @contextmanager
        def observe(profile, fresh=False):
            api.post(profile + "/launch").raise_for_status()
            try:
                report = reports.get(timeout=25)
                assert "error" not in report, report
                page, *other_contexts = report["contexts"]
                assert len(other_contexts) == 4 and all(values == page for values in other_contexts)
                assert all(isinstance(value, (int, float)) and value > 0 for value in page)
                assert list(map(float, report["navigation"])) == [page[1]] * 2
                assert list(map(float, report["resource"])) == [page[1]] * 2
                if fresh:
                    assert report["first"] == [None, None]
                yield page
            finally:
                api.post(profile + "/stop").raise_for_status()

        standard = create("Standard")
        limited = create("Limited", hardware_reporting="limited")
        with observe(standard, fresh=True) as native, observe(limited, fresh=True) as reduced:
            assert reduced == [min(native[0], 4), min(native[1], 4)]
        with observe(limited) as restarted:
            assert restarted == reduced
        api.patch(limited, json={"hardware_reporting": "standard"}).raise_for_status()
        with observe(limited) as restored:
            assert restored == native
