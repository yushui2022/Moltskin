"""Local site readings from independently configured Chromium environments."""

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


READINGS = b"""
function readings() {
  return {language: navigator.language, languages: navigator.languages,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          offset: new Date('2025-07-01T12:00:00Z').getTimezoneOffset()};
}
"""
PAGE = b"<!doctype html><script>" + READINGS + b"""
const worker = new Worker('/worker.js');
worker.onmessage = event => fetch('/report', {method: 'POST', body: JSON.stringify({
  page: readings(), worker: event.data, size: [outerWidth, outerHeight],
  heapLimit: performance.memory.jsHeapSizeLimit
})});
worker.postMessage(null);
</script>"""
WORKER = READINGS + b"onmessage = () => postMessage(readings());"


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium")
def test_per_window_region_survives_restart_and_resets(data_dir, monkeypatch):
    reports = Queue()

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            content_type, body = {
                "/": ("text/html", PAGE), "/worker.js": ("text/javascript", WORKER),
            }.get(self.path, ("text/plain", b""))
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            report = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            report["http_language"] = self.headers["Accept-Language"].split(",")[0]
            reports.put(report)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Site) as site, ExitStack() as resources:
        thread = Thread(target=site.serve_forever)
        thread.start()
        resources.callback(thread.join)
        resources.callback(site.shutdown)
        monkeypatch.setattr(config, "OPEN_MANAGER", True)
        monkeypatch.setattr(config, "local_dashboard_url", lambda: f"http://127.0.0.1:{site.server_port}/")
        api = resources.enter_context(TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}))

        def create(name, **settings):
            response = api.post("/api/v1/profiles", json={
                "name": name, "engine": "chromium", "headless": True, "startup_page": "dashboard", **settings,
            }).raise_for_status()
            return "/api/v1/profiles/" + response.json()["data"]["id"]

        @contextmanager
        def run(path):
            api.post(path + "/launch").raise_for_status()
            try:
                report = reports.get(timeout=20)
                assert report["worker"] == report["page"]
                assert report["http_language"] == report["page"]["language"]
                yield report
            finally:
                api.post(path + "/stop").raise_for_status()

        a = create("A")
        with run(a) as default:
            pass
        api.patch(a, json={"browser_language": "fr-FR", "browser_timezone": "Europe/Paris",
                           "browser_window_size": "1024x768", "chromium_js_heap_mb": 256}).raise_for_status()
        b = create("B", browser_language="ja-JP", browser_timezone="Asia/Tokyo", browser_window_size="1280x800",
                   chromium_js_heap_mb=512)
        with run(a) as france, run(b) as japan:
            assert france["page"] == {"language": "fr-FR", "languages": ["fr-FR"],
                                      "timezone": "Europe/Paris", "offset": -120}
            assert japan["page"] == {"language": "ja-JP", "languages": ["ja-JP"],
                                     "timezone": "Asia/Tokyo", "offset": -540}
            assert france["size"] == [1024, 768]
            assert japan["size"] == [1280, 800]
            assert 0 < france["heapLimit"] < japan["heapLimit"]
        api.patch(a, json={"browser_language": "", "browser_timezone": "", "browser_window_size": "",
                           "chromium_js_heap_mb": 0}).raise_for_status()
        with run(b) as restarted, run(a) as reset:
            assert restarted == japan
            assert reset["page"] == default["page"]
            assert reset["heapLimit"] == default["heapLimit"]
