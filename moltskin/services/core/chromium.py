"""Native Chromium launch settings for persistent dashboard environments."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

from chromium.native import (
    CAPABILITY, GEOLOCATION_CAPABILITY, MEDIA_CAPTURE_CAPABILITY,
    chromium_owns_directory, default_binary, probe_capabilities,
)

from . import config


def prepare_launch(profile_dir: Path, local_fonts: str, headless: bool, *,
                   block_images: bool = False, open_dashboard: bool = False,
                   media_capture: str = "standard", geolocation: str = "standard",
                   browser_language: str = "", browser_window_size: str = "") -> tuple[list[str], str]:
    if sys.platform not in ("darwin", "linux"):
        raise ValueError("Chromium 预览版仅支持 macOS 和 Linux")
    if local_fonts not in ("standard", "restricted"):
        raise ValueError("未知的字体访问设置")
    if media_capture not in ("standard", "blocked"):
        raise ValueError("未知的摄像头与麦克风访问设置")
    if geolocation not in ("standard", "blocked"):
        raise ValueError("未知的地理位置访问设置")
    if browser_language and browser_language not in config.CHROMIUM_BROWSER_LANGUAGES:
        raise ValueError("未知的网站首选语言")
    if browser_window_size and browser_window_size not in config.CHROMIUM_BROWSER_WINDOW_SIZES:
        raise ValueError("未知的启动窗口大小")
    binary, version, capabilities = probe_capabilities(default_binary())
    if media_capture == "blocked" and MEDIA_CAPTURE_CAPABILITY not in capabilities:
        raise ValueError(f"当前内核缺少 {MEDIA_CAPTURE_CAPABILITY} 补丁，请更新内核以阻止摄像头与麦克风访问")
    if geolocation == "blocked" and GEOLOCATION_CAPABILITY not in capabilities:
        raise ValueError(f"当前内核缺少 {GEOLOCATION_CAPABILITY} 补丁，请更新内核以阻止地理位置访问")
    if chromium_owns_directory(profile_dir):
        raise BlockingIOError("此 Chromium 环境已被其他进程打开")
    args = [str(binary), f"--user-data-dir={profile_dir.absolute()}", "--no-first-run", "--no-default-browser-check"]
    if not 1 <= config.PORT <= 65535:
        raise ValueError("控制台端口须在 1 到 65535 之间")
    if config.PORT != 8765:
        args.append(f"--moltskin-panel-port={config.PORT}")
    if local_fonts == "restricted":
        args.append("--" + CAPABILITY)
    if media_capture == "blocked":
        args.append("--" + MEDIA_CAPTURE_CAPABILITY)
    if geolocation == "blocked":
        args.append("--" + GEOLOCATION_CAPABILITY)
    if block_images:
        args.append("--blink-settings=imagesEnabled=false")
    if browser_language:
        args.append(f"--accept-lang={browser_language}")
    if browser_window_size:
        args.append("--window-size=" + browser_window_size.replace("x", ","))
    if headless:
        args.append("--headless")
    args.append(config.local_dashboard_url() if open_dashboard else "about:blank")
    return args, version


def launch_environment(browser_timezone: str) -> dict[str, str] | None:
    if not browser_timezone:
        return None
    if browser_timezone not in config.CHROMIUM_BROWSER_TIMEZONES:
        raise ValueError("未知的浏览器时区")
    # Each window owns a Chromium process tree. The manager and other windows retain their TZ.
    return {**os.environ, "TZ": browser_timezone}


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
