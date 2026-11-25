"""Verify that the one-click manager only reuses its own local service."""

import json
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

pytest.importorskip("fcntl")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chromium import open_panel


@pytest.mark.parametrize(("legacy", "expected"), [
    ("absent", "manager-desktop"),
    ("closed", "manager-browser"),
    ("tabbed", "manager-desktop"),
    ("app", "manager-browser"),
])
def test_desktop_window_launch_and_reuse(tmp_path, monkeypatch, legacy, expected):
    port = 18765
    app_switch = f"--app=http://127.0.0.1:{port}/#/windows"
    previous = tmp_path / "manager-browser"
    (tmp_path / "manager-port").write_text(str(port))
    if legacy != "absent":
        previous.mkdir()
    owners = {}

    def mark_open(folder, pid):
        (folder / "SingletonLock").symlink_to(f"{socket.gethostname()}-{pid}")
        owners[folder] = pid

    if legacy in {"tabbed", "app"}:
        mark_open(previous, 1001)
    launches, activations = [], []

    def launch(args, **kwargs):
        launches.append(args)
        directory = Path(next(arg.removeprefix("--user-data-dir=") for arg in args if arg.startswith("--user-data-dir=")))
        mark_open(directory, 1002)
        return SimpleNamespace(poll=lambda: None)

    def run(args, **kwargs):
        if args[0] == "ps":
            desktop = legacy == "app" or owners.get(previous) == 1002
            return SimpleNamespace(returncode=0, stdout="Chromium " + (app_switch if desktop else "chrome://moltskin/"))
        assert args[0] == "/usr/bin/osascript"
        activations.append(int(args[-2]))
        return SimpleNamespace(returncode=0, stdout="yes")

    monkeypatch.setattr(open_panel.sys, "platform", "darwin")
    monkeypatch.setattr(open_panel, "ensure_service", lambda *args: None)
    monkeypatch.setattr(open_panel.native, "probe", lambda *args, **kwargs: ("/test/Chromium", "1"))
    monkeypatch.setattr(open_panel.native, "chromium_owns_directory", lambda directory: directory in owners)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", launch)
    open_panel.open_manager(port, tmp_path)
    open_panel.open_manager(port, tmp_path)

    if legacy == "app":
        assert not launches and activations == [1001, 1001]
    else:
        assert len(launches) == 1 and activations == [1002]
        assert launches[0][-1] == app_switch
        assert f"--user-data-dir={tmp_path / expected}" in launches[0]
    if legacy == "tabbed":
        assert owners[previous] == 1001
        # On the next cold start, keep the desktop's own local UI preferences.
        for folder in list(owners):
            (folder / "SingletonLock").unlink()
        owners.clear()
        open_panel.open_manager(port, tmp_path)
        assert f"--user-data-dir={tmp_path / 'manager-desktop'}" in launches[-1]


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
