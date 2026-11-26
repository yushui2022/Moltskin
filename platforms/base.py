"""Platform adapter contract for host-specific browser operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class PlatformAdapter(ABC):
    """Small host boundary used by the product and Chromium build helpers.

    Platform-specific code belongs here instead of being spread across the
    service. The Windows implementation can stay disabled until its native
    Chromium package is available.
    """

    family: str
    supports_chromium: bool = False
    unsupported_chromium_message: str = "Chromium 预览版目前支持 macOS 和 Linux。"

    @abstractmethod
    def host_os_label(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def host_arch(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def default_chromium_binary(self) -> Path:
        raise NotImplementedError

    @abstractmethod
    def resolve_chromium_binary(self, path: Path) -> Path:
        raise NotImplementedError

    @abstractmethod
    def chromium_catalog_glob(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def pid_alive(self, pid: int | None) -> bool:
        raise NotImplementedError

    @abstractmethod
    def process_command(self, pid: int) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def process_list(self) -> list[tuple[int, str]]:
        raise NotImplementedError

    @abstractmethod
    def process_executable(self, pid: int) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def process_start_kwargs(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def send_stop(self, pid: int, engine: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def send_kill(self, pid: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def launch_environment(self, timezone: str) -> dict[str, str] | None:
        raise NotImplementedError

    @abstractmethod
    def disposable_keychain_args(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def owns_lock(self, profile_dir: Path, pid: int) -> bool:
        raise NotImplementedError

    @abstractmethod
    def matches_process(self, profile_dir: Path, pid: int) -> bool:
        raise NotImplementedError

    @abstractmethod
    def chromium_owns_directory(self, user_data: Path) -> bool:
        raise NotImplementedError
