"""Exercise first-party site storage through the real Chromium launch and stop APIs."""

import json
import os
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from moltskin.services.core import config, main
from moltskin.services.core.security import get_token


PAGE = b"""<!doctype html><meta charset=utf-8><script>
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
    return [localStorage.getItem('marker'), row ?? null, response ? await response.text() : null];
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
const step = location.hash.slice(1);
check(...step.split('/'))
  .then(samples => ({step, samples}), error => ({step, error: String(error)}))
  .then(report => fetch('/result', {method: 'POST', body: JSON.stringify(report)}));
</script>"""


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium build")
def test_same_origin_storage_isolated_across_native_profile_restarts(data_dir, monkeypatch):
    reports = Queue()

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
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
                "name": name, "engine": "chromium", "headless": True, "startup_page": "dashboard",
            }).raise_for_status()
            paths[name] = f"{profiles}/{response.json()['data']['id']}"

        def stop(path):
            response = api.post(path + "/stop").raise_for_status()
            assert response.json()["data"]["state"] == "stopped"

        def launch(running, name, step, expected):
            # Only the fragment changes. Origin, page, database, cache and keys are identical.
            url = f"http://127.0.0.1:{site.server_port}/#{step}"
            monkeypatch.setattr(config, "local_dashboard_url", lambda: url)
            path = paths[name]
            api.post(path + "/launch").raise_for_status()
            running.callback(stop, path)
            assert reports.get(timeout=25) == {"step": step, "samples": [[value] * 3 for value in expected]}

        with ExitStack() as running:
            launch(running, "A", "write/A", [None, "A"])
            launch(running, "B", "write/B", [None, "B"])
        with ExitStack() as running:
            launch(running, "B", "read/B", ["B"])
            launch(running, "A", "edit-clear/edited", ["A", "edited", None])
        with ExitStack() as running:
            launch(running, "B", "read/B", ["B"])
            launch(running, "A", "read/A", [None])
