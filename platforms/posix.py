"""Shared POSIX process and singleton-lock operations."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
from pathlib import Path

from .base import PlatformAdapter


class PosixPlatformAdapter(PlatformAdapter):
    supports_chromium = True
    unsupported_chromium_message = ""

    def __init__(self, family: str) -> None:
        self.family = family

    def pid_alive(self, pid: int | None) -> bool:
        if not isinstance(pid, int) or not 0 < pid <= 2**31 - 1:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, TypeError):
            return False

    def process_command(self, pid: int) -> str | None:
        try:
            result = subprocess.run(
                ["ps", "-ww", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OSError(f"无法读取进程 {pid} 的命令行") from exc
        return result.stdout.rstrip("\n") if result.returncode == 0 else None

    def process_list(self) -> list[tuple[int, str]]:
        try:
            output = subprocess.check_output(
                ["ps", "-ww", "-axo", "pid=,command="],
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OSError("无法枚举本机进程") from exc
        rows: list[tuple[int, str]] = []
        for line in output.splitlines():
            try:
                pid, command = line.strip().split(None, 1)
                rows.append((int(pid), command))
            except (ValueError, IndexError):
                continue
        return rows

    def process_executable(self, pid: int) -> str | None:
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return Path(result.stdout.strip()).name or None

    def process_start_kwargs(self) -> dict:
        return {"start_new_session": True}

    def send_stop(self, pid: int, engine: str) -> None:
        try:
            os.kill(pid, signal.SIGINT if engine == "chromium" else signal.SIGTERM)
        except ProcessLookupError:
            pass

    def send_kill(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def launch_environment(self, timezone: str) -> dict[str, str] | None:
        if not timezone:
            return None
        return {**os.environ, "TZ": timezone}

    def disposable_keychain_args(self) -> list[str]:
        return []

    def owns_lock(self, profile_dir: Path, pid: int) -> bool:
        try:
            return os.readlink(profile_dir / "SingletonLock") == f"{socket.gethostname()}-{pid}"
        except (FileNotFoundError, NotADirectoryError):
            return False

    def matches_process(self, profile_dir: Path, pid: int) -> bool:
        command = self.process_command(pid)
        if command is None:
            return False
        # Our first switch identifies the directory. Later user-supplied text
        # may resemble switches.
        _, marker, options = command.partition(" --user-data-dir=")
        expected = f"{profile_dir.resolve()} --no-first-run --no-default-browser-check"
        return bool(marker) and options.startswith(expected)

    def chromium_owns_directory(self, user_data: Path) -> bool:
        # Chromium also creates a lock when launched directly, or if our launcher
        # exits first.
        try:
            owner = os.readlink(user_data / "SingletonLock")
        except FileNotFoundError:
            return False
        host, _, pid = owner.rpartition("-")
        if host == socket.gethostname() and pid.isascii() and pid.isdigit() and 0 < int(pid) <= 2**31 - 1:
            try:
                os.kill(int(pid), 0)
            except ProcessLookupError:
                return False  # Chromium cleans its own stale lock on startup.
            except PermissionError:
                pass
        return True
