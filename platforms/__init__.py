"""Host platform adapters for Moltskin."""

from __future__ import annotations

import sys

from .base import PlatformAdapter
from .linux import LinuxPlatform
from .macos import MacOSPlatform
from .windows import WindowsPlatform


def detect_platform() -> PlatformAdapter:
    if sys.platform == "darwin":
        return MacOSPlatform()
    if sys.platform.startswith("linux"):
        return LinuxPlatform()
    if sys.platform == "win32":
        return WindowsPlatform()
    return WindowsPlatform()


current = detect_platform()

__all__ = ["PlatformAdapter", "current", "detect_platform"]
