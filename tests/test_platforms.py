"""Host platform adapter contract."""

import os
import sys
from pathlib import Path

import pytest

from platforms import current, detect_platform
from platforms.linux import LinuxPlatform
from platforms.macos import MacOSPlatform
from platforms.windows import WindowsPlatform


def test_current_platform_family_matches_host():
    expected = "macos" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else "windows"
    assert current.family == expected


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS app bundle behavior")
def test_macos_resolves_app_bundle(tmp_path):
    program = tmp_path / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
    program.parent.mkdir(parents=True)
    program.touch()
    adapter = MacOSPlatform()
    assert adapter.resolve_chromium_binary(tmp_path / "Chromium.app") == program


def test_linux_resolves_plain_executable(tmp_path):
    binary = tmp_path / "chrome"
    binary.touch()
    assert LinuxPlatform().resolve_chromium_binary(binary) == binary


def test_windows_adapter_is_disabled_until_packaged():
    adapter = WindowsPlatform()
    assert adapter.family == "windows"
    assert adapter.supports_chromium is False
    assert "Windows" in adapter.unsupported_chromium_message


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process checks")
def test_posix_process_contract():
    assert current.pid_alive(os.getpid()) is True
    assert current.pid_alive(None) is False
    assert current.process_start_kwargs() == {"start_new_session": True}
    command = current.process_command(os.getpid())
    assert command is not None
    assert "pytest" in command or "python" in command


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX timezone environment")
def test_launch_environment_adds_timezone():
    environment = current.launch_environment("Asia/Tokyo")
    assert environment is not None
    assert environment["TZ"] == "Asia/Tokyo"
    assert current.launch_environment("") is None


def test_platform_resolution_with_configured_binary(monkeypatch, tmp_path):
    binary = tmp_path / "Chromium.app"
    monkeypatch.setenv("MOLTSKIN_CHROMIUM_BINARY", str(binary))
    assert current.default_chromium_binary() == binary


def test_detect_platform_returns_windows_on_win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert isinstance(detect_platform(), WindowsPlatform)
