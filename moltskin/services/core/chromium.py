"""Native Chromium launch settings for persistent dashboard environments."""

from __future__ import annotations

import os
import re
import socket
import stat
import subprocess
import sys
from pathlib import Path

from chromium.native import (
    CAPABILITY, GEOLOCATION_CAPABILITY, MEDIA_CAPTURE_CAPABILITY, VERSION_PATTERN,
    check_access_policies, chromium_owns_directory, resolve_build,
)

from . import config


def prepare_launch(profile_dir: Path, local_fonts: str, headless: bool, *,
                   chromium_version: str = "", block_images: bool = False, open_dashboard: bool = False,
                   chromium_js_heap_mb: int = 0, chromium_mute_audio: bool = False, chromium_renderer_limit: int = 0,
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
    if chromium_js_heap_mb and chromium_js_heap_mb not in config.CHROMIUM_JS_HEAP_LIMITS:
        raise ValueError("不支持的 JavaScript 堆内存设置")
    if chromium_renderer_limit and chromium_renderer_limit not in config.CHROMIUM_RENDERER_LIMITS:
        raise ValueError("不支持的渲染进程数量")
    if browser_language and browser_language not in config.CHROMIUM_BROWSER_LANGUAGES:
        raise ValueError("未知的网站首选语言")
    if browser_window_size and browser_window_size not in config.CHROMIUM_BROWSER_WINDOW_SIZES:
        raise ValueError("未知的启动窗口大小")
    binary, version, capabilities = resolve_build(chromium_version)
    check_access_policies(capabilities, media_capture, geolocation)
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
    if chromium_js_heap_mb:
        args.append(f"--js-flags=--max-old-space-size={chromium_js_heap_mb}")
    if chromium_mute_audio:
        args.append("--mute-audio")
    if chromium_renderer_limit:
        args.append(f"--renderer-process-limit={chromium_renderer_limit}")
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


def check_profile_version(profile_dir: Path, version: str, last_launched: str | None) -> None:
    """A newer Chromium major may already have upgraded files in this data directory."""
    versions = [last_launched] if last_launched else []
    try:
        fd = os.open(profile_dir / "Last Version", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        pass
    else:
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("无法读取此环境的 Chromium 数据版本。")
            marker = handle.read(65)
            if len(marker) > 64:
                raise ValueError("此环境的 Chromium 数据版本格式异常。")
            versions.append(marker.decode("ascii").strip())
    for previous in versions:
        if not re.fullmatch(VERSION_PATTERN, previous):
            raise ValueError("此环境的 Chromium 数据版本格式异常。")
        if int(previous.partition(".")[0]) > int(version.partition(".")[0]):
            raise ValueError(f"此环境已使用 Chromium {previous}，请选择同一主版本或更高版本。"
                             "测试旧内核时请新建环境。")


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
