"""Client-side SSH routing and credential handling for the Linux preview."""
import argparse
from contextlib import contextmanager
import html
import os
from pathlib import Path
import re
import shlex
import socket
import stat
import subprocess
import sys
from unittest.mock import Mock
from urllib.parse import unquote, urlparse

import pytest

from chromium.remote import view


@pytest.mark.parametrize("value", ["a@my-node", "node-2.example", "127.0.0.1", "user@[::1]"])
def test_ssh_host(value):
    assert view.ssh_host(value) == value


@pytest.mark.parametrize("value", ["", "-oProxyCommand=id", "a\nb", "user@foo@bar", "-user@host",
                                   "host `id`", "[::1", "::1]", "fe80::1%$(id)"])
def test_ssh_host_rejects_option_and_shell_syntax(value):
    with pytest.raises(argparse.ArgumentTypeError):
        view.ssh_host(value)


@pytest.mark.parametrize("path, expected", [("~/repo name", "/home/test/repo name"), ("~", "/home/test/"),
                                              ("a'b;$(exit 17)", "/home/test/a'b;$(exit 17)"),
                                              ("/build/bin", "/build/bin")])
def test_remote_path_forms_one_shell_word_without_evaluation(path, expected):
    output = subprocess.check_output(["sh", "-c", 'printf "%s" ' + view.remote_path(path)],
                                     env={**os.environ, "HOME": "/home/test"}, text=True)
    assert output == expected


@pytest.mark.parametrize("data", [b'{"port":0,"password":"abcdefgh"}', b'{"port":1234,"password":"abcdefgh&other"}',
                                  b'{"port":true,"password":"Ab12Cd34"}', b"Ab12Cd34", b"[]", b"{}"])
def test_malformed_connection_does_not_reveal_password(data):
    with pytest.raises(ValueError) as err:
        view.connection(data)
    assert "Ab12Cd34" not in str(err.value)
    assert "abcdefgh" not in str(err.value)


def test_local_port_falls_back_when_preferred_port_is_in_use(monkeypatch):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        port = taken.getsockname()[1]
        monkeypatch.setattr(view, "PREFERRED_PORT", port)
        assert view.available_port() != port


def test_viewer_uses_private_launch_file(monkeypatch, tmp_path):
    launch = Mock(return_value=True)
    monkeypatch.setattr(view.webbrowser, "open", launch)
    view.open_viewer(19000, "Ab12Cd34", tmp_path)
    url = launch.call_args.args[0]
    assert "Ab12Cd34" not in url and urlparse(url).scheme == "file"
    page = Path(unquote(urlparse(url).path))
    assert stat.S_IMODE(page.stat().st_mode) == 0o600
    redirect = re.search(r'content="0;url=([^"]+)"', page.read_text())
    target = urlparse(html.unescape(redirect[1]))
    assert (target.hostname, target.port, target.path) == ("127.0.0.1", 19000, "/vnc.html")
    assert not target.query and "password=Ab12Cd34" in target.fragment


def test_stop_only_invokes_remote_lifecycle(monkeypatch):
    run = Mock()
    monkeypatch.setattr(view.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["view.py", "server", "--repo", "/data/my project", "--stop"])
    assert view.main() == 0
    invocation = run.call_args.args[0]
    assert invocation[-3:-1] == ["--", "server"]
    assert shlex.split(invocation[-1]) == ["exec", "/data/my project/.venv/bin/python",
                                           "/data/my project/chromium/remote/session.py", "stop"]


def test_connect_separates_remote_and_local_ports_and_keeps_credentials_private(monkeypatch, capsys):
    start = Mock()
    info = Mock(return_value=b'{"port":37817,"password":"Ab12Cd34"}')
    tunnel = Mock()
    tunnel.wait.return_value = 0
    @contextmanager
    def forward(host, port):
        assert (host, port) == ("node", 37817)
        yield 12345, tunnel

    launch = Mock()
    monkeypatch.setattr(view.subprocess, "run", start)
    monkeypatch.setattr(view.subprocess, "check_output", info)
    monkeypatch.setattr(view, "forward", forward)
    monkeypatch.setattr(view, "open_viewer", launch)
    monkeypatch.setattr(sys, "argv", ["view.py", "node", "--python", "venv/bin/python"])
    assert view.main() == 0
    assert start.call_args.args[0][-1].endswith(" start")
    assert info.call_args.args[0][-1].endswith(" connection")
    assert launch.call_args.args[:2] == (12345, "Ab12Cd34")
    assert not launch.call_args.args[2].exists()
    stdout, stderr = capsys.readouterr()
    assert "12345" in stdout
    assert "Ab12Cd34" not in stdout + stderr
