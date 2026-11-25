"""Exercise Linux acceptance gating independently of an installed native browser."""

import json
from subprocess import CompletedProcess

import pytest

from chromium import check_linux


def test_acceptance_rejects_wrong_target_and_propagates_failed_checks(tmp_path, monkeypatch, capsys):
    binary = tmp_path / "chrome"
    pinned = json.loads((check_linux.ROOT / "chromium/upstream.json").read_text())["version"]
    monkeypatch.setattr(check_linux.sys, "argv", ["check_linux.py", str(binary)])
    monkeypatch.setattr(check_linux.sys, "platform", "linux")
    monkeypatch.setattr(check_linux.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(check_linux.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(check_linux.shutil, "which", lambda name: "/usr/bin/" + name)
    binary.write_bytes(b"not a Linux executable")
    with pytest.raises(SystemExit):
        check_linux.main()
    assert "ELF" in capsys.readouterr().err

    binary.write_bytes(b"\x7fELF\x02\x01" + bytes(12) + b"\x3e\x00")
    caps = check_linux.native.DESKTOP_CAPABILITIES
    monkeypatch.setattr(check_linux.native, "probe_capabilities", lambda path: (binary, "0.0.0.0", caps))
    with pytest.raises(SystemExit):
        check_linux.main()
    assert "expected pinned Chromium" in capsys.readouterr().err

    monkeypatch.setattr(check_linux.native, "probe_capabilities", lambda path: (binary, pinned, caps))
    def run(cmd, *, cwd, env):
        assert cmd[0] == "xvfb-run" and "-nolisten tcp" in cmd[3]
        assert "tests/test_chromium_graphics_privacy.py" in cmd and "tests/test_chromium_worker_canvas_policy.py" in cmd
        assert env["MOLTSKIN_TEST_X11_INPUT"] == "1" and env["MOLTSKIN_HEADLESS"] == "0"
        assert env["MOLTSKIN_CHROMIUM_BINARY"] == str(binary)
        assert cwd == check_linux.ROOT
        return CompletedProcess(cmd, 1)

    monkeypatch.setattr(check_linux.subprocess, "run", run)
    assert check_linux.main() == 1
