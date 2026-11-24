"""Verify that the one-click manager only reuses its own local service."""

import json
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

pytest.importorskip("fcntl")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chromium import open_panel


def test_start_and_reuse_service(tmp_path, monkeypatch):
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    children = []
    popen = subprocess.Popen

    def capture(*args, **kwargs):
        proc = popen(*args, **kwargs)
        children.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", capture)
    try:
        with (tmp_path / "service.log").open("w") as log:
            open_panel.ensure_service(port, tmp_path, log)
            open_panel.ensure_service(port, tmp_path, log)
        assert len(children) == 1
        assert open_panel.service_matches(port, tmp_path)
        wrong_data = tmp_path / "another-installation"
        wrong_data.mkdir()
        (wrong_data / "api.token").write_text("unrelated-token")
        assert not open_panel.service_matches(port, wrong_data)
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=10)


def test_unrelated_port_never_receives_api_token(tmp_path):
    token = "test-token-only-known-to-the-launcher"
    (tmp_path / "api.token").write_text(token)
    requests = []

    class OtherApp(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, str(self.headers)))
            body = json.dumps({"status": "ok", "panel_proof": "0" * 64}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    with HTTPServer(("127.0.0.1", 0), OtherApp) as other:
        serving = Thread(target=other.serve_forever, daemon=True)
        serving.start()
        try:
            with (tmp_path / "service.log").open("w") as log, pytest.raises(RuntimeError, match="occupied"):
                open_panel.ensure_service(other.server_port, tmp_path, log)
        finally:
            other.shutdown()
            serving.join()
    assert requests
    for target, headers in requests:
        assert target.startswith("/api/v1/health?panel_challenge=")
        assert "Authorization" not in headers
        assert token not in headers and token not in target
