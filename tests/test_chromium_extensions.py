"""Chromium CRX2/CRX3/MV3 extension installation, assignment, and storage persistence."""

from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import threading
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import pytest

from chromium.native import default_binary, probe_capabilities
from moltskin.services.core import chromium, extensions


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _pb_bytes(field_num: int, payload: bytes) -> bytes:
    tag = (field_num << 3) | 2
    return _encode_varint(tag) + _encode_varint(len(payload)) + payload


def make_zip(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content.encode("utf-8") if isinstance(content, str) else content)
    return buf.getvalue()


def make_crx3(files: dict[str, bytes | str]) -> tuple[bytes, bytes]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    spki_der = priv.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    zip_payload = make_zip(files)
    proof = _pb_bytes(1, spki_der) + _pb_bytes(2, b"fake-signature")
    crx_header = _pb_bytes(2, proof) + _pb_bytes(10000, _pb_bytes(1, b"\x00" * 16))
    archive = b"Cr24" + struct.pack("<II", 3, len(crx_header)) + crx_header + zip_payload
    return archive, spki_der


def make_crx2(files: dict[str, bytes | str]) -> tuple[bytes, bytes]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    spki_der = priv.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    sig = b"crx2-sig"
    zip_payload = make_zip(files)
    archive = (
        b"Cr24"
        + struct.pack("<III", 2, len(spki_der), len(sig))
        + spki_der
        + sig
        + zip_payload
    )
    return archive, spki_der


def test_crx3_and_crx2_unpacking_preserves_official_chrome_id_and_i18n(data_dir):
    crx3_bytes, spki3 = make_crx3(
        {
            "manifest.json": json.dumps(
                {
                    "manifest_version": 3,
                    "name": "__MSG_extName__",
                    "description": "__MSG_extDesc__",
                    "default_locale": "zh_CN",
                    "version": "2.4.1",
                    "background": {"service_worker": "sw.js"},
                }
            ),
            "_locales/zh_CN/messages.json": json.dumps(
                {
                    "extName": {"message": "沉浸式翻译测试"},
                    "extDesc": {"message": "双语对照网页翻译扩展"},
                },
                ensure_ascii=False,
            ),
            "sw.js": "self.addEventListener('install', () => {});",
        }
    )
    info3 = extensions.install("ext_crx3", crx3_bytes, "immersive.crx")
    expected_id3 = extensions.chrome_id_from_pubkey_bytes(spki3)
    assert info3["name"] == "沉浸式翻译测试"
    assert info3["description"] == "双语对照网页翻译扩展"
    assert info3["manifest_version"] == 3
    assert info3["chrome_id"] == expected_id3
    assert info3["engines"] == ["chromium"]
    saved_manifest = json.loads((extensions.addon_dir("ext_crx3") / "manifest.json").read_text("utf-8"))
    assert "browser_specific_settings" not in saved_manifest
    assert saved_manifest["key"] == base64.b64encode(spki3).decode("ascii")

    crx2_bytes, spki2 = make_crx2(
        {
            "manifest.json": json.dumps(
                {"manifest_version": 2, "name": "Authenticator CRX2", "version": "1.0.0"}
            )
        }
    )
    info2 = extensions.install("ext_crx2", crx2_bytes, "auth.crx")
    assert info2["chrome_id"] == extensions.chrome_id_from_pubkey_bytes(spki2)


def test_chromium_profile_extension_api_and_launch_args(data_dir, monkeypatch):
    from moltskin.services.core import main
    from moltskin.services.core.security import get_token

    fake_bin = data_dir / "fake-chromium"
    fake_bin.write_text(
        """#!/usr/bin/env python3
import json, os, signal, socket, sys
from pathlib import Path
if sys.argv[1:] == ["--version", "--moltskin-capabilities"]:
    print("Chromium 158.0.0.1")
    print("moltskin-disable-local-font-access")
    sys.exit(0)
directory = Path(next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--user-data-dir=")))
lock = directory / "SingletonLock"
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
(directory / "test-args.json").write_text(json.dumps(sys.argv[1:]))
lock.symlink_to(f"{socket.gethostname()}-{os.getpid()}")
try:
    signal.alarm(30)
    signal.pause()
finally:
    lock.unlink(missing_ok=True)
""",
        encoding="utf-8",
    )
    fake_bin.chmod(0o700)
    monkeypatch.setenv("MOLTSKIN_CHROMIUM_BINARY", str(fake_bin))

    crx_bytes, _ = make_crx3(
        {
            "manifest.json": json.dumps(
                {
                    "manifest_version": 3,
                    "name": "Wallet MV3",
                    "version": "3.1.0",
                    "background": {"service_worker": "bg.js"},
                }
            ),
            "bg.js": "console.log('ready');",
        }
    )

    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as client:
        uploaded = client.post(
            "/api/v1/extensions",
            json={"filename": "wallet.crx", "content": base64.b64encode(crx_bytes).decode("ascii")},
        ).raise_for_status().json()["data"]
        assert uploaded["manifest_version"] == 3
        assert uploaded["engines"] == ["chromium"]
        assert len(uploaded["chrome_id"]) == 32

        profile = client.post(
            "/api/v1/profiles",
            json={"name": "Web3 Profile", "engine": "chromium", "startup_page": "blank"},
        ).raise_for_status().json()["data"]
        pid = profile["id"]

        put_res = client.put(
            f"/api/v1/profiles/{pid}/extensions",
            json={"ids": [uploaded["id"]]},
        ).raise_for_status().json()["data"]
        assert put_res["ids"] == [uploaded["id"]]
        assert client.get(f"/api/v1/profiles/{pid}/extensions").raise_for_status().json()["data"]["ids"] == [uploaded["id"]]

        client.post(f"/api/v1/profiles/{pid}/launch").raise_for_status()
        try:
            # Cannot mutate extensions while profile is running.
            assert client.put(f"/api/v1/profiles/{pid}/extensions", json={"ids": []}).status_code == 409
            args = json.loads((Path(profile["user_data_dir"]) / "test-args.json").read_text("utf-8"))
            load_flags = [a for a in args if a.startswith("--load-extension=")]
            assert len(load_flags) == 1
            assert str(extensions.addon_dir(uploaded["id"]).resolve()) in load_flags[0]
        finally:
            client.post(f"/api/v1/profiles/{pid}/stop").raise_for_status()

        # Disabling extension globally omits it from launch.
        client.patch(f"/api/v1/extensions/{uploaded['id']}", json={"enabled": False}).raise_for_status()
        client.post(f"/api/v1/profiles/{pid}/launch").raise_for_status()
        try:
            args2 = json.loads((Path(profile["user_data_dir"]) / "test-args.json").read_text("utf-8"))
            assert not any(a.startswith("--load-extension=") for a in args2)
        finally:
            client.post(f"/api/v1/profiles/{pid}/stop").raise_for_status()


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_native_chromium_loads_mv3_crx_and_persists_storage_across_restarts_and_relocated_path(
    data_dir, tmp_path
):
    probe_capabilities(default_binary())
    crx_bytes, spki = make_crx3(
        {
            "manifest.json": json.dumps(
                {
                    "manifest_version": 3,
                    "name": "Persistent MV3 Probe",
                    "version": "1.0.0",
                    "permissions": ["storage"],
                    "content_scripts": [
                        {
                            "matches": ["http://127.0.0.1/*"],
                            "js": ["content.js"],
                            "run_at": "document_end",
                        }
                    ],
                }
            ),
            "content.js": """
document.documentElement.dataset.injected = "1";
chrome.storage.local.get(["visits"], (res) => {
  const next = (res.visits || 0) + 1;
  chrome.storage.local.set({ visits: next }, () => {
    fetch("/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: chrome.runtime.id, visits: next }),
    });
  });
});
""",
        }
    )
    info = extensions.install("ext_mv3_live", crx_bytes, "probe.crx")
    expected_chrome_id = extensions.chrome_id_from_pubkey_bytes(spki)
    assert info["chrome_id"] == expected_chrome_id

    reports: list[dict] = []
    report_event = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/plain":
                body = b"<html><body><script>fetch('/report',{method:'POST',body:JSON.stringify({plain:true})});</script></body></html>"
            else:
                body = (
                    b"<html><body>mv3 extension probe"
                    b"<script>setTimeout(() => { if (!document.documentElement.dataset.injected) location.reload(); }, 250);</script>"
                    b"</body></html>"
                )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            reports.append(payload)
            self.send_response(204)
            self.end_headers()
            report_event.set()

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    profile_dir = tmp_path / "chromium-ext-profile"
    profile_dir.mkdir(mode=0o700)
    relocated_ext = tmp_path / "relocated-extension-dir"
    shutil.copytree(info["path"], relocated_ext)

    def run_once(ext_dirs: list[str], url_path: str = "/") -> dict:
        report_event.clear()
        cmd, _ = chromium.prepare_launch(
            profile_dir,
            "standard",
            True,
            extension_dirs=ext_dirs,
        )
        cmd[-1] = f"http://127.0.0.1:{port}{url_path}"
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            assert report_event.wait(timeout=20), "Timed out waiting for browser report"
            return reports[-1]
        finally:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    try:
        first = run_once([info["path"]])
        assert first == {"id": expected_chrome_id, "visits": 1}

        # Relaunch pointing to a relocated extension directory: manifest.key keeps
        # chrome.runtime.id identical, so chrome.storage.local increments 1 -> 2!
        second = run_once([str(relocated_ext)])
        assert second == {"id": expected_chrome_id, "visits": 2}
    finally:
        server.shutdown()
        server.server_close()
