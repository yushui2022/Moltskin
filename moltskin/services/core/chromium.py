"""Native Chromium launch settings for persistent dashboard environments."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

from chromium.native import CAPABILITY, chromium_owns_directory, default_binary, probe

from . import config


def chrome_binary() -> Path:
    if sys.platform not in ("darwin", "linux"):
        raise ValueError("Chromium 预览版仅支持 macOS 和 Linux")
    binary, _ = probe(default_binary())
    return binary


def launch_command(profile_dir: Path, local_fonts: str, headless: bool) -> list[str]:
    if local_fonts not in ("standard", "restricted"):
        raise ValueError("未知的字体访问设置")
    binary = chrome_binary()
    profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if chromium_owns_directory(profile_dir):
        raise BlockingIOError("此 Chromium 环境已被其他进程打开")
    args = [str(binary), f"--user-data-dir={profile_dir.resolve()}", "--no-first-run", "--no-default-browser-check"]
    if not 1 <= config.PORT <= 65535:
        raise ValueError("控制台端口须在 1 到 65535 之间")
    if config.PORT != 8765:
        args.append(f"--moltskin-panel-port={config.PORT}")
    if local_fonts == "restricted":
        args.append("--" + CAPABILITY)
    if headless:
        args.append("--headless")
    # The management window owns the dashboard. Managed profile windows open on a blank tab.
    args.append("about:blank")
    return args


def owns_lock(profile_dir: Path, pid: int) -> bool:
    try:
        return os.readlink(profile_dir / "SingletonLock") == f"{socket.gethostname()}-{pid}"
    except (FileNotFoundError, NotADirectoryError):
        return False


def matches_process(profile_dir: Path, pid: int) -> bool:
    """A persisted PID or a leftover SingletonLock alone cannot identify a browser."""
    result = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "command="], capture_output=True,
                            text=True, errors="replace", timeout=3, check=False)
    # ps flattens argv, so include the next fixed option to delimit paths containing spaces.
    expected = f" --user-data-dir={profile_dir.resolve()} --no-first-run"
    return result.returncode == 0 and expected in result.stdout
