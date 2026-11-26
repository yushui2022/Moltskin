"""Remote sessions must authenticate the framebuffer and keep all listeners local."""
import json
import os
import threading

import pytest

from chromium.remote import session


def test_vnc_configuration_disables_both_ipv6_listeners(tmp_path):
    args = session.vnc_command(":81", "xauthority", tmp_path / "flag", tmp_path / "auth")
    assert args[args.index("-listen") + 1] == "127.0.0.1"
    assert "-no6" in args                 # x11vnc's own IPv6 transport
    assert args[args.index("-rfbportv6") + 1] == "0"  # independent LibVNCServer listener
    assert args[args.index("-rfbauth") + 1] == str(tmp_path / "auth")


@pytest.mark.parametrize("offered", [b"\x02", b"\x01", b"\x01\x02"])
def test_ready_requires_vnc_password_auth(tmp_path, offered):
    flag = tmp_path / "port"
    with session.loopback_listener() as server:
        host, port = server.getsockname()
        assert host == "127.0.0.1"
        flag.write_text(f"PORT={port}\n")

        def advertise():
            conn, _ = server.accept()
            with conn, conn.makefile("rb") as reader:
                conn.sendall(b"RFB 003.008\n")
                if reader.read(12) == b"RFB 003.008\n":
                    conn.sendall(bytes([len(offered)]) + offered)

        thread = threading.Thread(target=advertise)
        thread.start()
        try:
            if offered == b"\x02":
                assert session.rfb_port(flag) == port
            else:
                with pytest.raises(RuntimeError, match="password-authenticated"):
                    session.rfb_port(flag)
        finally:
            thread.join(timeout=3)
            assert not thread.is_alive()


@pytest.mark.skipif(os.name != "posix", reason="Linux user session control uses flock")
def test_reconnect_reuses_unit_and_stale_state_cannot_authorize(tmp_path, monkeypatch, capsys):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(session, "state", lambda: "active")
    folder = tmp_path / session.NAME
    folder.mkdir(mode=0o700)
    connection = {"port": 38100, "password": "Test1234"}
    (folder / "connection.json").write_text(json.dumps(connection))

    def unexpected_restart(*args, **kwargs):
        pytest.fail("An active viewer should keep its window and credential")

    monkeypatch.setattr(session.subprocess, "run", unexpected_restart)
    session.control("start")
    assert capsys.readouterr().out == ""
    session.control("connection")
    assert json.loads(capsys.readouterr().out) == connection

    monkeypatch.setattr(session, "state", lambda: "inactive")
    session.control("stop")
    with pytest.raises(RuntimeError, match="not running"):
        session.control("connection")
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("port,password", [(True, "Test1234"), (65536, "Test1234"), (12345, "not-8")])
def test_connection_rejects_invalid_saved_values(tmp_path, port, password):
    path = tmp_path / "connection.json"
    path.write_text(json.dumps({"port": port, "password": password}))
    with pytest.raises(RuntimeError, match="Invalid viewer"):
        session.read_connection(path)
