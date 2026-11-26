"""Windows host adapter placeholder.

The native Chromium package is not built yet. Keep this implementation free of
POSIX assumptions so it can be completed without changing the service layer.
"""

from __future__ import annotations

import os
import platform as host_platform
from pathlib import Path

from .base import PlatformAdapter


class WindowsPlatform(PlatformAdapter):
    family = "windows"
    supports_chromium = False
    unsupported_chromium_message = "Chromium 预览版目前支持 macOS 和 Linux。Windows 版本尚未构建。"

    def host_os_label(self) -> str:
        return host_platform.system()

    def host_arch(self) -> str:
        return host_platform.machine()

    def default_chromium_binary(self) -> Path:
        configured = os.environ.get("MOLTSKIN_CHROMIUM_BINARY")
        if configured:
            return Path(configured).expanduser()
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Moltskin" / "Chromium" / "chrome.exe"

    def resolve_chromium_binary(self, path: Path) -> Path:
        return path

    def chromium_catalog_glob(self) -> str:
        return "*/chrome.exe"

    def pid_alive(self, pid: int | None) -> bool:
        return False

    def process_command(self, pid: int) -> str | None:
        return None

    def process_list(self) -> list[tuple[int, str]]:
        return []

    def process_executable(self, pid: int) -> str | None:
        return None

    def process_start_kwargs(self) -> dict:
        return {"creationflags": 0x00000200}  # CREATE_NEW_PROCESS_GROUP

    def send_stop(self, pid: int, engine: str) -> None:
        raise NotImplementedError("Windows process shutdown is not implemented yet")

    def send_kill(self, pid: int) -> None:
        raise NotImplementedError("Windows process shutdown is not implemented yet")

    def launch_environment(self, timezone: str) -> dict[str, str] | None:
        return None

    def disposable_keychain_args(self) -> list[str]:
        return []

    def owns_lock(self, profile_dir: Path, pid: int) -> bool:
        return False

    def matches_process(self, profile_dir: Path, pid: int) -> bool:
        return False

    def chromium_owns_directory(self, user_data: Path) -> bool:
        return False
