"""Per-profile hardware overrides and anti-correlation surfaces."""

import http.server
import os
import socketserver
import threading

import pytest
from playwright.sync_api import sync_playwright

from chromium.native import HARDWARE_PROFILE_CAPABILITY, default_binary, probe_capabilities
from moltskin.services.core import chromium


def test_launcher_passes_hardware_concurrency_memory_and_seed(tmp_path, monkeypatch):
    capabilities = frozenset({HARDWARE_PROFILE_CAPABILITY})
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (tmp_path / "Chromium", "158.0.8127.88", capabilities),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        hardware_concurrency=12,
        device_memory=8,
        hardware_seed=12345,
    )
    assert "--moltskin-hardware-concurrency=12" in args
    assert "--moltskin-device-memory=8" in args
    assert "--moltskin-hardware-seed=12345" in args


def test_hardware_seed_is_stable_and_distinct():
    first = chromium.hardware_seed("pf_alpha")
    assert first == chromium.hardware_seed("pf_alpha")
    assert 0 < first <= 0xFFFFFFFF
    assert first != chromium.hardware_seed("pf_beta")
    assert first != chromium.canvas_noise_seed("pf_alpha")
    assert first != chromium.audio_noise_seed("pf_alpha")


class _Site(http.server.BaseHTTPRequestHandler):
    headers_seen = []

    def do_GET(self):
        if self.path == "/worker.js":
            body = b"postMessage([navigator.hardwareConcurrency, navigator.deviceMemory]);"
            content_type = "text/javascript"
        else:
            type(self).headers_seen.append({k.lower(): v for k, v in self.headers.items()})
            body = b"<!doctype html><title>hardware probe</title>"
            content_type = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-CH", "Sec-CH-Device-Memory, Device-Memory")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


PROBE = """async () => {
  const worker = new Worker('/worker.js');
  const workerValues = await new Promise(resolve => {
    worker.onmessage = event => resolve(event.data);
  });
  const estimate = await navigator.storage.estimate();
  const battery = await navigator.getBattery();
  const context = new AudioContext();
  const audio = {
    sampleRate: context.sampleRate,
    baseLatency: context.baseLatency,
    outputLatency: context.outputLatency,
    maxChannelCount: context.destination.maxChannelCount,
  };
  await context.close();
  const devices = (await navigator.mediaDevices.enumerateDevices()).map(device => ({
    kind: device.kind,
    deviceId: device.deviceId,
    groupId: device.groupId,
    label: device.label,
  }));
  return {
    windowValues: [navigator.hardwareConcurrency, navigator.deviceMemory],
    workerValues,
    quota: estimate.quota,
    battery: {
      charging: battery.charging,
      chargingTime: battery.chargingTime,
      dischargingTimeInfinite: battery.dischargingTime === Infinity,
      level: battery.level,
    },
    audio,
    devices,
  };
}"""


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_native_hardware_overrides_and_anticorrelation_surfaces():
    binary, _, capabilities = probe_capabilities(default_binary())
    assert HARDWARE_PROFILE_CAPABILITY in capabilities

    server = socketserver.TCPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def run(seed: int, concurrency: int, memory: int, *, granted: bool = False):
        _Site.headers_seen.clear()
        args = [
            f"--moltskin-hardware-seed={seed}",
            f"--moltskin-hardware-concurrency={concurrency}",
            f"--moltskin-device-memory={memory}",
            "--moltskin-platform=windows:10.0.0:x86_64",
        ]
        if granted:
            args.extend([
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ])
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=str(binary), headless=True, args=args)
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/")
                page.goto(f"http://127.0.0.1:{port}/?second=1")
                if granted:
                    page.evaluate(
                        """async () => {
                          const stream = await navigator.mediaDevices.getUserMedia({audio: true, video: true});
                          stream.getTracks().forEach(track => track.stop());
                        }"""
                    )
                return page.evaluate(PROBE), list(_Site.headers_seen)
            finally:
                browser.close()

    try:
        first, first_headers = run(12345, 12, 8, granted=True)
        first_again, _ = run(12345, 12, 8, granted=True)
        second, second_headers = run(67890, 6, 4, granted=True)
        unprivileged, _ = run(12345, 8, 8, granted=False)
    finally:
        server.shutdown()

    assert first["windowValues"] == [12, 8]
    assert first["workerValues"] == [12, 8]
    assert second["windowValues"] == [6, 4]
    assert second["workerValues"] == [6, 4]
    assert first_headers[-1].get("sec-ch-device-memory") == "8"
    assert first_headers[-1].get("device-memory") == "8"
    assert second_headers[-1].get("sec-ch-device-memory") == "4"
    assert second_headers[-1].get("device-memory") == "4"

    # Storage quota is realistic, deterministic per profile and distinct across profiles.
    assert first["quota"] == first_again["quota"]
    assert first["quota"] != second["quota"]
    assert first["quota"] > 100 * (1024 ** 3)
    assert second["quota"] > 100 * (1024 ** 3)

    # Battery is always stabilized to desktop AC power.
    assert first["battery"] == {
        "charging": True,
        "chargingTime": 0,
        "dischargingTimeInfinite": True,
        "level": 1,
    }

    # AudioContext metadata matches a standard stereo Windows desktop.
    assert first["audio"]["maxChannelCount"] == 2
    assert first["audio"]["outputLatency"] == pytest.approx(0.016)
    assert first["audio"]["baseLatency"] == pytest.approx(480.0 / first["audio"]["sampleRate"])

    # Unprivileged pages see all three standard device kinds with blank identifiers.
    assert [d["kind"] for d in unprivileged["devices"]] == [
        "audioinput",
        "videoinput",
        "audiooutput",
    ]
    assert all(
        d["deviceId"] == "" and d["groupId"] == "" and d["label"] == ""
        for d in unprivileged["devices"]
    )

    # Privileged pages see clean per-profile virtual devices and labels.
    assert first["devices"] == first_again["devices"]
    assert [d["kind"] for d in first["devices"]] == [
        "audioinput",
        "videoinput",
        "audiooutput",
    ]
    assert [d["label"] for d in first["devices"]] == [
        "Default - Microphone (Realtek(R) Audio)",
        "HD WebCam",
        "Default - Speakers (Realtek(R) Audio)",
    ]
    assert all(len(d["deviceId"]) == 64 and len(d["groupId"]) == 64 for d in first["devices"])
    assert first["devices"][0]["groupId"] == first["devices"][2]["groupId"]
    assert [d["deviceId"] for d in first["devices"]] != [d["deviceId"] for d in second["devices"]]
    assert [d["groupId"] for d in first["devices"]] != [d["groupId"] for d in second["devices"]]
