"""Native Chromium launch settings for persistent dashboard environments."""

from __future__ import annotations

import hashlib
import os
import re
import socket
import stat
import subprocess
import sys
from pathlib import Path

from chromium.native import (
    CANVAS_READBACK_SWITCH, CAPABILITY, GEOLOCATION_CAPABILITY, MEDIA_CAPTURE_CAPABILITY, VERSION_PATTERN,
    AUDIO_NOISE_CAPABILITY, CANVAS_NOISE_CAPABILITY, HARDWARE_REPORTING_CAPABILITY, PLATFORM_CAPABILITY,
    SCREEN_METRICS_CAPABILITY,
    WEBDRIVER_CAPABILITY, WEBGL_DEBUG_INFO_CAPABILITY, WEBGL_INFO_CAPABILITY,
    check_access_policies, chromium_owns_directory, resolve_build,
)

from . import config
from .security import workbench_signature


def prepare_launch(profile_dir: Path, local_fonts: str, headless: bool, *,
                   chromium_version: str = "", chromium_user_agent: str = "",
                   proxy_server: str | None = None,
                   block_images: bool = False, open_dashboard: bool = False, workbench_session: str | None = None,
                   chromium_js_heap_mb: int = 0, chromium_mute_audio: bool = False, chromium_renderer_limit: int = 0,
                   media_capture: str = "standard", geolocation: str = "standard",
                   hardware_reporting: str = "standard",
                   canvas_readback: str = "standard", webgl_debug_info: str = "standard",
                   browser_language: str = "", browser_window_size: str = "",
                   chromium_screen: str = "", chromium_platform: str = "",
                   chromium_webgl: str = "", canvas_noise_seed: int = 0,
                   audio_noise_seed: int = 0) -> tuple[list[str], str]:
    if sys.platform not in ("darwin", "linux"):
        raise ValueError("Chromium 预览版仅支持 macOS 和 Linux")
    if local_fonts not in ("standard", "restricted"):
        raise ValueError("未知的字体访问设置")
    if media_capture not in ("standard", "blocked"):
        raise ValueError("未知的摄像头与麦克风访问设置")
    if geolocation not in ("standard", "blocked"):
        raise ValueError("未知的地理位置访问设置")
    if hardware_reporting not in ("standard", "limited"):
        raise ValueError("未知的硬件信息设置")
    if canvas_readback not in ("standard", "blocked") or webgl_debug_info not in ("standard", "blocked"):
        raise ValueError("未知的图形隐私设置")
    if chromium_js_heap_mb and chromium_js_heap_mb not in config.CHROMIUM_JS_HEAP_LIMITS:
        raise ValueError("不支持的 JavaScript 堆内存设置")
    if chromium_renderer_limit and chromium_renderer_limit not in config.CHROMIUM_RENDERER_LIMITS:
        raise ValueError("不支持的渲染进程数量")
    if browser_language and browser_language not in config.CHROMIUM_BROWSER_LANGUAGES:
        raise ValueError("未知的网站首选语言")
    if browser_window_size and browser_window_size not in config.CHROMIUM_BROWSER_WINDOW_SIZES:
        raise ValueError("未知的启动窗口大小")
    if chromium_screen and chromium_screen not in config.CHROMIUM_SCREEN_PRESETS:
        raise ValueError("未知的屏幕预设")
    if chromium_platform and chromium_platform not in config.CHROMIUM_PLATFORM_PRESETS:
        raise ValueError("未知的平台预设")
    if chromium_webgl and chromium_webgl not in config.CHROMIUM_WEBGL_PRESETS:
        raise ValueError("未知的显卡预设")
    if canvas_noise_seed and not 0 < canvas_noise_seed <= 0xFFFFFFFF:
        raise ValueError("Canvas 噪声种子无效")
    if audio_noise_seed and not 0 < audio_noise_seed <= 0xFFFFFFFF:
        raise ValueError("音频噪声种子无效")
    config.validate_chromium_user_agent(chromium_user_agent)
    binary, version, capabilities = resolve_build(chromium_version)
    check_access_policies(capabilities, media_capture, geolocation, webgl_debug_info, canvas_readback, hardware_reporting)
    if chromium_screen and SCREEN_METRICS_CAPABILITY not in capabilities:
        raise ValueError("当前内核缺少 moltskin-screen-metrics 补丁，请更新内核以使用屏幕预设")
    if chromium_platform and PLATFORM_CAPABILITY not in capabilities:
        raise ValueError("当前内核缺少 moltskin-platform 补丁，请更新内核以使用平台预设")
    if chromium_webgl and WEBGL_INFO_CAPABILITY not in capabilities:
        raise ValueError("当前内核缺少 moltskin-webgl-info 补丁，请更新内核以使用显卡预设")
    if canvas_noise_seed and CANVAS_NOISE_CAPABILITY not in capabilities:
        raise ValueError("当前内核缺少 moltskin-canvas-noise 补丁，请更新内核以使用像素噪声")
    if audio_noise_seed and AUDIO_NOISE_CAPABILITY not in capabilities:
        raise ValueError("当前内核缺少 moltskin-audio-noise 补丁，请更新内核以使用音频噪声")
    if chromium_owns_directory(profile_dir):
        raise BlockingIOError("此 Chromium 环境已被其他进程打开")
    args = [str(binary), f"--user-data-dir={profile_dir.absolute()}", "--no-first-run", "--no-default-browser-check"]
    if proxy_server:
        args.append(f"--proxy-server={proxy_server}")
        # Do not let WebRTC bypass the profile proxy with direct UDP/STUN.
        args.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
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
    if hardware_reporting == "limited":
        args.append("--" + HARDWARE_REPORTING_CAPABILITY)
    if canvas_readback == "blocked":
        args.append("--" + CANVAS_READBACK_SWITCH)
    if webgl_debug_info == "blocked":
        args.append("--" + WEBGL_DEBUG_INFO_CAPABILITY)
    # Keep navigator.webdriver false even if an automation client enables CDP.
    args.append("--" + WEBDRIVER_CAPABILITY)
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
    if chromium_user_agent:
        args.append(f"--user-agent={chromium_user_agent}")
    if browser_window_size:
        args.append("--window-size=" + browser_window_size.replace("x", ","))
    if chromium_screen:
        args.append("--" + SCREEN_METRICS_CAPABILITY + "=" + chromium_screen)
    if chromium_platform:
        args.append("--" + PLATFORM_CAPABILITY + "=" + chromium_platform)
    if chromium_webgl:
        _, vendor, renderer = chromium_webgl.split("|", 2)
        args.append("--moltskin-webgl-vendor=" + vendor)
        args.append("--moltskin-webgl-renderer=" + renderer)
    if canvas_noise_seed:
        args.append(f"--{CANVAS_NOISE_CAPABILITY}={canvas_noise_seed}")
    if audio_noise_seed:
        args.append(f"--{AUDIO_NOISE_CAPABILITY}={audio_noise_seed}")
    if headless:
        args.append("--headless")
    if workbench_session:
        signature = workbench_signature(workbench_session)
        args.append(f"http://127.0.0.1:{config.PORT}/workbench/{workbench_session}/{signature}")
    else:
        args.append(config.local_dashboard_url() if open_dashboard else "about:blank")
    return args, version


def _noise_seed(scope: str, profile_id: str) -> int:
    digest = hashlib.sha256((f"moltskin-{scope}-noise:" + profile_id).encode()).digest()
    return int.from_bytes(digest[:4], "big") or 1


def canvas_noise_seed(profile_id: str) -> int:
    """Stable per-profile seed; a new profile id produces a new noise pattern."""
    return _noise_seed("canvas", profile_id)


def audio_noise_seed(profile_id: str) -> int:
    return _noise_seed("audio", profile_id)


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
    # Our first switch identifies the directory. Later user-supplied text may resemble switches.
    _, marker, options = result.stdout.partition(" --user-data-dir=")
    expected = f"{profile_dir.resolve()} --no-first-run --no-default-browser-check"
    return result.returncode == 0 and bool(marker) and options.startswith(expected)
