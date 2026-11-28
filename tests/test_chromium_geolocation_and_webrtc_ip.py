"""Tests for 0031 Geolocation proxy auto-spoofing and WebRTC public IP replacement."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import subprocess
import tempfile
from threading import Event, Thread
import time

import pytest
from playwright.sync_api import sync_playwright

from chromium import native as chromium_native
from moltskin.services.core import cdp, chromium, geoip


def test_geoip_jitter_is_deterministic_and_distinct_per_profile():
    lat1, lon1, acc1 = geoip.jitter_coordinates(34.0522, -118.2437, "pf_alpha")
    lat1_again, lon1_again, acc1_again = geoip.jitter_coordinates(34.0522, -118.2437, "pf_alpha")
    lat2, lon2, acc2 = geoip.jitter_coordinates(34.0522, -118.2437, "pf_beta")

    assert (lat1, lon1, acc1) == (lat1_again, lon1_again, acc1_again)
    assert (lat1, lon1) != (lat2, lon2)
    assert abs(lat1 - 34.0522) < 0.01
    assert abs(lon1 - (-118.2437)) < 0.01
    assert 18.0 <= acc1 <= 42.0
    assert 18.0 <= acc2 <= 42.0


def test_prepare_launch_geolocation_spoof_and_webrtc_ip_switches(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda _ver: chromium_native.InstalledBuild(
            Path("/bin/echo"), "158.0.8127.88", chromium_native.DESKTOP_CAPABILITIES
        ),
    )
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda _dir: False)

    args, _ = chromium.prepare_launch(
        tmp_path / "p_geo_webrtc",
        "standard",
        True,
        proxy_server="http://127.0.0.1:8080",
        geolocation="auto",
        geolocation_coords=(35.6762, 139.6503, 21.5),
        webrtc_policy="proxy",
        webrtc_public_ip="198.51.100.77",
    )
    assert "--moltskin-geolocation=35.676200,139.650300,21.5" in args
    assert "--moltskin-webrtc-public-ip=198.51.100.77" in args
    assert args.count("--webrtc-ip-handling-policy=disable_non_proxied_udp") == 1


class _GeoWebrtcHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<!doctype html><html><head><meta charset='utf-8'></head><body>ok</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@pytest.mark.skipif(
    not chromium_native.default_binary().exists(),
    reason="Moltskin Chromium binary not installed",
)
def test_native_chromium_0031_geolocation_spoof_and_webrtc_public_ip():
    binary, _ver, caps = chromium_native.probe_capabilities(chromium_native.default_binary())
    assert chromium_native.GEOLOCATION_SPOOF_CAPABILITY in caps
    assert chromium_native.WEBRTC_IP_CAPABILITY in caps

    http_server = ThreadingHTTPServer(("127.0.0.1", 0), _GeoWebrtcHandler)
    http_port = http_server.server_address[1]
    Thread(target=http_server.serve_forever, daemon=True).start()

    # Bind a real local UDP socket to prove zero STUN packets ever escape when disable_non_proxied_udp is active.
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.bind(("127.0.0.1", 0))
    udp_sock.settimeout(0.2)
    stun_port = udp_sock.getsockname()[1]
    udp_packets: list[bytes] = []
    stop_udp = Event()

    def _watch_udp() -> None:
        while not stop_udp.is_set():
            try:
                pkt, _ = udp_sock.recvfrom(2048)
                udp_packets.append(pkt)
            except TimeoutError:
                continue
            except OSError:
                break

    Thread(target=_watch_udp, daemon=True).start()

    try:
        with tempfile.TemporaryDirectory() as user_data:
            origin = f"http://127.0.0.1:{http_port}"
            proc = subprocess.Popen(
                [
                    str(binary),
                    f"--user-data-dir={user_data}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--headless",
                    "--remote-debugging-port=0",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--moltskin-hardware-seed=987654321",
                    "--moltskin-geolocation=35.6762,139.6503,21.5",
                    "--moltskin-webrtc-public-ip=198.51.100.77",
                    f"{origin}/test",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 10.0
                ws_url = ""
                while time.monotonic() < deadline:
                    try:
                        dev_port, ws_path = cdp.read_devtools_endpoint(Path(user_data))
                        ws_url = f"ws://127.0.0.1:{dev_port}{ws_path}"
                        break
                    except Exception:  # noqa: BLE001
                        time.sleep(0.1)
                assert ws_url

                with sync_playwright() as pw:
                    browser = pw.chromium.connect_over_cdp(ws_url)
                    context = browser.contexts[0]
                    page = context.pages[0]
                    page.goto(f"{origin}/test", wait_until="domcontentloaded")

                    # 1) Unvisited origin must still report 'prompt' (no global permission anomaly).
                    initial_perm = page.evaluate(
                        "() => navigator.permissions.query({name: 'geolocation'}).then(r => r.state)"
                    )
                    assert initial_perm == "prompt"

                    # 2) Grant geolocation for this origin only; getCurrentPosition & watchPosition
                    # must return our spoofed Tokyo coordinates without macOS CoreLocation TCC!
                    context.grant_permissions(["geolocation"], origin=origin)
                    geo_reading = page.evaluate(
                        """() => new Promise((resolve, reject) => {
                            navigator.geolocation.getCurrentPosition(
                                pos => {
                                    const wid = navigator.geolocation.watchPosition(wpos => {
                                        navigator.geolocation.clearWatch(wid);
                                        resolve({
                                            lat: pos.coords.latitude,
                                            lon: pos.coords.longitude,
                                            acc: pos.coords.accuracy,
                                            ageMs: Math.abs(Date.now() - pos.timestamp),
                                            watchLat: wpos.coords.latitude,
                                            watchLon: wpos.coords.longitude,
                                        });
                                    }, err => reject(err.message));
                                },
                                err => reject(err.message),
                                { enableHighAccuracy: true, timeout: 5000 }
                            );
                        })"""
                    )
                    assert geo_reading["lat"] == pytest.approx(35.6762, abs=1e-4)
                    assert geo_reading["lon"] == pytest.approx(139.6503, abs=1e-4)
                    assert geo_reading["acc"] == pytest.approx(21.5, abs=0.2)
                    assert geo_reading["watchLat"] == pytest.approx(35.6762, abs=1e-4)
                    assert geo_reading["watchLon"] == pytest.approx(139.6503, abs=1e-4)
                    assert geo_reading["ageMs"] < 5000

                    # 3) WebRTC with STUN server must emit mDNS host + srflx with 198.51.100.77,
                    # inject into localDescription.sdp, and send ZERO real UDP packets!
                    webrtc_with_stun = page.evaluate(
                        f"""async () => {{
                            const pc = new RTCPeerConnection({{
                                iceServers: [{{ urls: "stun:127.0.0.1:{stun_port}" }}]
                            }});
                            const events = [];
                            const states = [];
                            pc.addEventListener("icegatheringstatechange", () => states.push(pc.iceGatheringState));
                            const done = new Promise(resolve => {{
                                pc.onicecandidate = e => {{
                                    if (e.candidate) {{
                                        events.push({{
                                            candidate: e.candidate.candidate,
                                            address: e.candidate.address,
                                            port: e.candidate.port,
                                            type: e.candidate.type,
                                            protocol: e.candidate.protocol,
                                            url: e.candidate.url,
                                        }});
                                    }} else {{
                                        resolve();
                                    }}
                                }};
                            }});
                            pc.createDataChannel("probe");
                            const offer = await pc.createOffer();
                            await pc.setLocalDescription(offer);
                            await done;
                            const sdp = pc.localDescription.sdp;
                            pc.close();
                            return {{ events, states, sdp }};
                        }}"""
                    )
                    types = [c["type"] for c in webrtc_with_stun["events"]]
                    assert types == ["host", "srflx"], webrtc_with_stun
                    host_cand, srflx_cand = webrtc_with_stun["events"]
                    assert host_cand["address"].endswith(".local")
                    assert srflx_cand["address"] == "198.51.100.77"
                    assert srflx_cand["protocol"] == "udp"
                    assert srflx_cand["url"] == f"stun:127.0.0.1:{stun_port}"
                    assert "198.51.100.77" in webrtc_with_stun["sdp"]
                    assert "typ srflx" in webrtc_with_stun["sdp"]
                    assert "c=IN IP4 198.51.100.77" in webrtc_with_stun["sdp"]
                    assert webrtc_with_stun["states"] == ["gathering", "complete"]
                    assert udp_packets == []

                    # 4) Anti-spoof trap check: without iceServers, NO srflx candidate should appear!
                    webrtc_no_stun = page.evaluate(
                        """async () => {
                            const pc = new RTCPeerConnection();
                            const types = [];
                            const done = new Promise(resolve => {
                                pc.onicecandidate = e => {
                                    if (e.candidate) types.push(e.candidate.type);
                                    else resolve();
                                };
                            });
                            pc.createDataChannel("local");
                            await pc.setLocalDescription(await pc.createOffer());
                            await done;
                            pc.close();
                            return types;
                        }"""
                    )
                    assert webrtc_no_stun == ["host"]
            finally:
                proc.terminate()
                proc.wait(timeout=5)
    finally:
        stop_udp.set()
        udp_sock.close()
        http_server.shutdown()
        http_server.server_close()
