"""Linux host adapter."""

from __future__ import annotations

import os
import platform as host_platform
from pathlib import Path

from .posix import PosixPlatformAdapter


class LinuxPlatform(PosixPlatformAdapter):
    family = "linux"

    def __init__(self) -> None:
        super().__init__("linux")

    def host_os_label(self) -> str:
        try:
            return host_platform.freedesktop_os_release()["PRETTY_NAME"]
        except (AttributeError, KeyError, OSError):
            return "Linux " + host_platform.release()

    def host_arch(self) -> str:
        return host_platform.machine()

    def default_chromium_binary(self) -> Path:
        configured = os.environ.get("MOLTSKIN_CHROMIUM_BINARY")
        if configured:
            return Path(configured).expanduser()
        out = Path.home() / "chromium-baseline" / "src" / "out"
        for directory in ("MoltskinHardwarePrivacy", "MoltskinGraphicsPrivacy", "MoltskinLocation", "MoltskinMedia",
                          "MoltskinToolbar", "MoltskinPanel"):
            binary = out / directory / "chrome"
            if binary.is_file():
                return binary
        return out / "MoltskinFont" / "chrome"

    def resolve_chromium_binary(self, path: Path) -> Path:
        return path

    def chromium_catalog_glob(self) -> str:
        return "*/chrome"
