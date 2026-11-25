"""Native capture policy with pre-authorized loopback media and synthetic devices."""

import json
import os
import time
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

import pytest
from fastapi.testclient import TestClient


PAGE = b"""<!doctype html><title>Local media capture test</title><script>
(async () => {
  const result = { grants: {}, capture: {} };
  try {
    result.marker = localStorage.getItem('marker') || crypto.randomUUID();
    localStorage.setItem('marker', result.marker);
    for (const name of ['microphone', 'camera']) {
      result.grants[name] = (await navigator.permissions.query({ name })).state;
    }
    for (const [name, constraints] of Object.entries({
      microphone: { audio: true }, camera: { video: true }, both: { audio: true, video: true }
    })) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        const tracks = stream.getTracks();
        result.capture[name] = tracks.map(t => t.kind + ':' + t.readyState).sort();
        tracks.forEach(t => t.stop());
      } catch (error) {
        result.capture[name] = error.name;
      }
    }
  } catch (error) {
    result.pageError = String(error);
  }
  await fetch('/reading', { method: 'POST', body: JSON.stringify(result) });
})();
</script>"""


@pytest.fixture
def capture_site():
    readings = Queue()

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(PAGE)

        def do_POST(self):
            if self.path != "/reading":
                self.send_error(404)
                return
            readings.put(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Site) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server.server_port, readings
        finally:
            server.shutdown()
            thread.join()


def allow_site_in_new_profile(directory, origin):
    # Seed Chrome's per-origin user exceptions before first launch. The two
    # permissions persist across restarts even when the profile policy changes.
    default = directory / "Default"
    default.mkdir()
    grant = {f"{origin},*": {"setting": 1}}
    exceptions = {"media_stream_mic": grant, "media_stream_camera": grant}
    (default / "Preferences").write_text(json.dumps({
        "profile": {"content_settings": {"exceptions": exceptions}}
    }), encoding="utf-8")


def stop_browser(client, url):
    client.post(url + "/stop").raise_for_status()
    deadline = time.monotonic() + 15
    while client.get(url + "/status").raise_for_status().json()["data"]["session_id"] is not None:
        assert time.monotonic() < deadline, "browser did not stop"
        time.sleep(0.1)


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium build")
def test_granted_media_capture_tracks_each_profile_after_restart(data_dir, monkeypatch, capture_site):
    from moltskin.services.core import chromium, config, main
    from moltskin.services.core.security import get_token

    port, readings = capture_site
    origin = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(config, "PORT", port)
    monkeypatch.setattr(config, "OPEN_MANAGER", True)
    prepare_launch = chromium.prepare_launch

    def launch_with_test_devices(*args, **kwargs):
        command, version = prepare_launch(*args, **kwargs)
        command.insert(-1, "--use-fake-device-for-media-stream")
        return command, version

    monkeypatch.setattr(chromium, "prepare_launch", launch_with_test_devices)
    allowed = {"microphone": ["audio:live"], "camera": ["video:live"], "both": ["audio:live", "video:live"]}
    denied = dict.fromkeys(allowed, "NotAllowedError")
    markers = {}
    urls = []
    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as client:
        for name in ("A", "B"):
            response = client.post("/api/v1/profiles", json={
                "name": name, "engine": "chromium", "startup_page": "dashboard", "headless": True,
            })
            profile = response.raise_for_status().json()["data"]
            allow_site_in_new_profile(Path(profile["user_data_dir"]), origin)
            urls.append("/api/v1/profiles/" + profile["id"])

        # Establish that both profiles have functioning devices and site grants.
        # Keep both browser processes alive together in each round.
        for modes in (("standard", "standard"), ("blocked", "standard"), ("standard", "blocked")):
            with ExitStack() as browsers:
                for url, mode in zip(urls, modes):
                    client.patch(url, json={"media_capture": mode}).raise_for_status()
                    launched = client.post(url + "/launch")
                    assert launched.is_success, f"{url} ({mode}): {launched.text}"
                    browsers.callback(stop_browser, client, url)
                    try:
                        result = readings.get(timeout=30)
                    except Empty:
                        pytest.fail(f"{url} ({mode}): the loopback page did not report its capture results")
                    assert "pageError" not in result, result
                    permission = "granted" if mode == "standard" else "denied"
                    assert result["grants"] == {"microphone": permission, "camera": permission}, result
                    assert result["capture"] == (allowed if mode == "standard" else denied), result
                    assert result["marker"] == markers.setdefault(url, result["marker"])
                assert len(set(markers.values())) == 2
