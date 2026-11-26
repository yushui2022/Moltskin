"""macOS host adapter."""

from __future__ import annotations

import os
import platform as host_platform
from pathlib import Path

from .posix import PosixPlatformAdapter


class MacOSPlatform(PosixPlatformAdapter):
    family = "macos"

    def __init__(self) -> None:
        super().__init__("macos")

    def host_os_label(self) -> str:
        return ("macOS " + host_platform.mac_ver()[0]).strip()

    def host_arch(self) -> str:
        return host_platform.machine()

    def default_chromium_binary(self) -> Path:
        configured = os.environ.get("MOLTSKIN_CHROMIUM_BINARY")
        if configured:
            return Path(configured).expanduser()
        out = Path.home() / "chromium-baseline" / "src" / "out"
        executable = "Chromium.app"
        for directory in ("MoltskinHardwarePrivacy", "MoltskinGraphicsPrivacy", "MoltskinLocation", "MoltskinMedia",
                          "MoltskinToolbar", "MoltskinPanel"):
            binary = out / directory / executable
            if (binary / "Contents" / "MacOS" / "Chromium").is_file():
                return binary
        return out / "MoltskinFont" / executable

    def resolve_chromium_binary(self, path: Path) -> Path:
        if path.suffix == ".app":
            return path / "Contents" / "MacOS" / "Chromium"
        return path

    def chromium_catalog_glob(self) -> str:
        return "*/Chromium.app/Contents/MacOS/Chromium"

    def disposable_keychain_args(self) -> list[str]:
        return ["--use-mock-keychain"]
