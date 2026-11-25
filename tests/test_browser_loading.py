"""Opt-in startup and image-loading checks using the installed browser engines."""

import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue
from threading import Thread

import pytest
from fastapi.testclient import TestClient


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs installed browser engines")
@pytest.mark.parametrize("engine", ["chromium", "camoufox"])
def test_image_loading_after_profile_restart(data_dir, monkeypatch, engine):
    from moltskin.services.core import config, main
    from moltskin.services.core.security import get_token

    # A local page at the dashboard address reports whether the image decoded.
    # Count HTTP requests as well, so hiding an already downloaded image fails.
    widths = Queue()
    image_requests = []

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                mime, body = "text/html", b"""<!doctype html><img id="sample" src="/sample.svg">
                <script>window.onload = () => fetch('/width/' + document.getElementById('sample').naturalWidth)</script>"""
            elif self.path == "/sample.svg":
                image_requests.append(self.path)
                mime, body = "image/svg+xml", b'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12"/>'
            elif self.path.startswith("/width/"):
                widths.put(int(self.path.rsplit("/", 1)[-1]))
                mime, body = "text/plain", b"ok"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Page) as server:
        monkeypatch.setattr(config, "PORT", server.server_port)
        monkeypatch.setattr(config, "OPEN_MANAGER", True)
        monkeypatch.setattr(config, "MANAGER_URL", f"http://127.0.0.1:{server.server_port}")
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as client:
                client.put("/api/v1/settings", json={"manager_button": False}).raise_for_status()
                profiles = "/api/v1/profiles"
                profile = client.post(profiles, json={"name": "Loading test", "engine": engine, "headless": True,
                                                      "startup_page": "dashboard", "block_images": True})
                profile.raise_for_status()
                url = f"{profiles}/{profile.json()['data']['id']}"
                for blocked in (True, False):
                    client.patch(url, json={"block_images": blocked}).raise_for_status()
                    image_requests.clear()
                    try:
                        client.post(url + "/launch").raise_for_status()
                        assert widths.get(timeout=25) == (0 if blocked else 12)
                        assert bool(image_requests) == (not blocked)
                    finally:
                        client.post(url + "/stop")
                        deadline = time.monotonic() + 12
                        while client.get(url + "/status").json()["data"]["session_id"] is not None:
                            assert time.monotonic() < deadline, "browser did not stop"
                            time.sleep(0.1)
        finally:
            server.shutdown()
            thread.join()
