"""Exercise first-party site storage through the real Chromium launch and stop APIs."""

import json
import os
import subprocess
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from moltskin.services.core import config, main
from moltskin.services.core.security import get_token


PAGE = b"""<!doctype html><meta charset=utf-8><script>
const step = location.hash.slice(1);
const interactive = location.search === '?interactive';
function report(data) {
  return fetch('/result', {method: 'POST', body: JSON.stringify({step, ...data})});
}
if (interactive) {
  document.title = 'Moltskin GUI test ' + step;
  let clicked = false;
  addEventListener('pointerdown', event => { clicked = event.isTrusted; });
  addEventListener('keydown', event => { if (clicked && event.isTrusted) report({key: event.key}); });
}
function result(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
async function check(action, marker) {
  const open = indexedDB.open('site', 1);
  open.onupgradeneeded = () => open.result.createObjectStore('values');
  const db = await result(open);
  async function snapshot() {
    const row = await result(db.transaction('values').objectStore('values').get('marker'));
    const response = await caches.match('/marker', {cacheName: 'site'});
    const values = [localStorage.getItem('marker'), row ?? null, response ? await response.text() : null];
    if (interactive) values.push(document.cookie.match(/(?:^|; )marker=([^;]*)/)?.[1] ?? null);
    return values;
  }
  async function change(value) {
    const tx = db.transaction('values', 'readwrite');
    const done = new Promise((resolve, reject) => {
      tx.oncomplete = resolve;
      tx.onabort = () => reject(tx.error);
    });
    const store = tx.objectStore('values');
    if (value === null) store.clear();
    else store.put(value, 'marker');
    await done;
    if (interactive) document.cookie = 'marker=' + (value ?? '') + '; Max-Age=' + (value === null ? 0 : 3600) + '; Path=/';
    if (value === null) {
      localStorage.removeItem('marker');
      await caches.delete('site');
    } else {
      localStorage.setItem('marker', value);
      const cache = await caches.open('site');
      await cache.put('/marker', new Response(value));
    }
  }
  try {
    const samples = [await snapshot()];
    if (action === 'write' || action === 'edit-clear') {
      await change(marker);
      samples.push(await snapshot());
    }
    if (action === 'edit-clear') {
      await change(null);
      samples.push(await snapshot());
    }
    return samples;
  } finally {
    db.close();
  }
}
check(...step.split('/'))
  .then(samples => report({samples}), error => report({error: String(error)}));
</script>"""


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium build")
def test_same_origin_storage_isolated_across_native_profile_restarts(data_dir, monkeypatch):
    reports = Queue()
    interactive = os.environ.get("MOLTSKIN_TEST_X11_INPUT") == "1"

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/?interactive"):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(PAGE)

        def do_POST(self):
            if self.path != "/result":
                self.send_error(404)
                return
            report = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(204)
            self.end_headers()
            reports.put(report)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Site) as site, ExitStack() as resources:
        thread = Thread(target=site.serve_forever)
        thread.start()
        resources.callback(thread.join)
        resources.callback(site.shutdown)
        monkeypatch.setattr(config, "OPEN_MANAGER", True)
        monkeypatch.setattr(config, "PORT", site.server_port)
        api = resources.enter_context(TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}))
        profiles = "/api/v1/profiles"
        paths = {}
        for name in ("A", "B"):
            response = api.post(profiles, json={
                "name": name, "engine": "chromium", "headless": not interactive, "startup_page": "dashboard",
            }).raise_for_status()
            paths[name] = f"{profiles}/{response.json()['data']['id']}"

        def stop(path):
            response = api.post(path + "/stop").raise_for_status()
            assert response.json()["data"]["state"] == "stopped"

        def launch(running, name, step, expected):
            # Only the fragment changes. Origin, page, database, cache and keys are identical.
            query = "?interactive" if interactive else ""
            url = f"http://127.0.0.1:{site.server_port}/{query}#{step}"
            monkeypatch.setattr(config, "local_dashboard_url", lambda: url)
            path = paths[name]
            response = api.post(path + "/launch")
            assert response.is_success, f"Chromium launch failed: {response.text}. Session logs: {config.RUN_DIR}"
            running.callback(stop, path)
            width = 4 if interactive else 3
            assert reports.get(timeout=25) == {"step": step, "samples": [[value] * width for value in expected]}

        def send_input(step, key):
            window, = subprocess.check_output([
                "xdotool", "search", "--sync", "--onlyvisible", "--name", "^Moltskin GUI test " + step,
            ], timeout=10, text=True).splitlines()
            subprocess.run([
                "xdotool", "windowraise", window, "windowfocus", "--sync", window,
                "mousemove", "--window", window, "165", "250", "click", "1", "key", key,
            ], check=True, timeout=10)
            assert reports.get(timeout=10) == {"step": step, "key": key}
            return window

        with ExitStack() as running:
            launch(running, "A", "write/A", [None, "A"])
            launch(running, "B", "write/B", [None, "B"])
            if interactive:
                assert send_input("write/A", "a") != send_input("write/B", "b")
        with ExitStack() as running:
            launch(running, "B", "read/B", ["B"])
            launch(running, "A", "edit-clear/edited", ["A", "edited", None])
        with ExitStack() as running:
            launch(running, "B", "read/B", ["B"])
            launch(running, "A", "read/A", [None])
