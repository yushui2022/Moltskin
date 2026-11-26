"""Locate the Moltskin Chromium build and inspect its native capabilities."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

CAPABILITY = "moltskin-disable-local-font-access"
MEDIA_CAPTURE_CAPABILITY = "moltskin-block-media-capture"
GEOLOCATION_CAPABILITY = "moltskin-block-geolocation"
WEBGL_DEBUG_INFO_CAPABILITY = "moltskin-block-webgl-debug-info"
CANVAS_READBACK_CAPABILITY = "moltskin-block-canvas-readback"
HARDWARE_REPORTING_CAPABILITY = "moltskin-limit-hardware-reporting"
WEBDRIVER_CAPABILITY = "moltskin-hide-webdriver"
SCREEN_METRICS_CAPABILITY = "moltskin-screen-metrics"
SCREEN_AVAIL_CAPABILITY = "moltskin-screen-avail"
PLATFORM_CAPABILITY = "moltskin-platform"
WEBGL_INFO_CAPABILITY = "moltskin-webgl-info"
CANVAS_NOISE_CAPABILITY = "moltskin-canvas-noise"
AUDIO_NOISE_CAPABILITY = "moltskin-audio-noise"
LOCALE_CAPABILITY = "moltskin-locale"
SPEECH_VOICES_CAPABILITY = "moltskin-speech-voices"
CANVAS_READBACK_SWITCH = CANVAS_READBACK_CAPABILITY
DESKTOP_CAPABILITIES = frozenset({CAPABILITY, MEDIA_CAPTURE_CAPABILITY, GEOLOCATION_CAPABILITY,
                                  WEBGL_DEBUG_INFO_CAPABILITY, CANVAS_READBACK_CAPABILITY, HARDWARE_REPORTING_CAPABILITY,
                                  WEBDRIVER_CAPABILITY, SCREEN_METRICS_CAPABILITY, SCREEN_AVAIL_CAPABILITY, PLATFORM_CAPABILITY,
                                  WEBGL_INFO_CAPABILITY, CANVAS_NOISE_CAPABILITY, AUDIO_NOISE_CAPABILITY, LOCALE_CAPABILITY,
                                  SPEECH_VOICES_CAPABILITY,
                                  "moltskin-panel", "moltskin-toolbar"})


def default_binary() -> Path:
    configured = os.environ.get("MOLTSKIN_CHROMIUM_BINARY")
    if configured:
        return Path(configured).expanduser()
    out = Path.home() / "chromium-baseline" / "src" / "out"
    executable = "Chromium.app" if sys.platform == "darwin" else "chrome"
    for directory in ("MoltskinHardwarePrivacy", "MoltskinGraphicsPrivacy", "MoltskinLocation", "MoltskinMedia",
                      "MoltskinToolbar", "MoltskinPanel"):
        binary = out / directory / executable
        program = binary / "Contents/MacOS/Chromium" if sys.platform == "darwin" else binary
        if program.is_file():
            return binary
    return out / "MoltskinFont" / executable


def probe_capabilities(path: Path) -> tuple[Path, str, frozenset[str]]:
    path = path.expanduser().resolve()
    if path.suffix == ".app":
        path = path / "Contents" / "MacOS" / "Chromium"
    result = subprocess.run(
        [path, "--version", "--moltskin-capabilities"], capture_output=True,
        timeout=15, check=True, text=True, errors="replace",
    )
    lines = result.stdout.splitlines()
    capabilities = frozenset(lines[1:])
    if CAPABILITY not in capabilities:
        raise ValueError(f"this binary does not report the Moltskin Local Font Access capability: {path}")
    version = re.search(r"\b\d+(?:\.\d+){3}\b", lines[0])
    return path, version.group() if version else lines[0].strip(), capabilities


VERSION_PATTERN = r"[0-9]{1,9}(?:\.[0-9]{1,9}){3}"


class InstalledBuild(NamedTuple):
    binary: Path
    version: str
    capabilities: frozenset[str]


def _read_build(path: Path) -> InstalledBuild:
    build = InstalledBuild(*probe_capabilities(path))
    if not re.fullmatch(VERSION_PATTERN, build.version):
        raise ValueError("开发包没有报告有效的 Chromium 版本号")
    return build


def build_catalog() -> tuple[dict[str, InstalledBuild], str | None, str | None]:
    """Discover the default and explicitly registered, host-managed packages only."""
    builds: dict[str, InstalledBuild] = {}
    default = None
    problem = None
    if sys.platform not in {"darwin", "linux"}:
        return builds, default, "Chromium 预览版目前支持 macOS 和 Linux。"
    try:
        build = _read_build(default_binary())
        default = build.version
        builds[default] = build
    except FileNotFoundError:
        problem = "未找到默认 Moltskin Chromium，可通过 MOLTSKIN_CHROMIUM_BINARY 指定开发包。"
    except ValueError:
        problem = "默认 Chromium 未报告有效的 Moltskin 补丁或版本号。"
    except (OSError, subprocess.SubprocessError):
        problem = "无法执行默认 Moltskin Chromium，请检查开发包路径和执行权限。"

    directory = os.environ.get("MOLTSKIN_CHROMIUM_BUILDS_DIR")
    if directory:
        root = Path(directory).expanduser().resolve()
        package = "Chromium.app/Contents/MacOS/Chromium" if sys.platform == "darwin" else "chrome"
        for candidate in sorted(root.glob("*/" + package)):
            try:
                binary = candidate.resolve(strict=True)
                if not binary.is_relative_to(root) or not binary.is_file():
                    continue
                build = _read_build(binary)
            except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
                continue
            builds.setdefault(build.version, build)
    return builds, default, problem


def resolve_build(version: str) -> InstalledBuild:
    if not version:
        return _read_build(default_binary())
    builds, _, _ = build_catalog()
    if version not in builds:
        raise ValueError(f"本机未安装所选 Chromium {version}，请安装该版本或重新选择内核。")
    return builds[version]


def check_access_policies(capabilities: frozenset[str], media_capture: str, geolocation: str,
                          webgl_debug_info: str = "standard", canvas_readback: str = "standard",
                          hardware_reporting: str = "standard") -> None:
    if media_capture == "blocked" and MEDIA_CAPTURE_CAPABILITY not in capabilities:
        raise ValueError(f"当前内核缺少 {MEDIA_CAPTURE_CAPABILITY} 补丁，请更新内核以阻止摄像头与麦克风访问")
    if geolocation == "blocked" and GEOLOCATION_CAPABILITY not in capabilities:
        raise ValueError(f"当前内核缺少 {GEOLOCATION_CAPABILITY} 补丁，请更新内核以阻止地理位置访问")
    if webgl_debug_info == "blocked" and WEBGL_DEBUG_INFO_CAPABILITY not in capabilities:
        raise ValueError(f"当前内核缺少 {WEBGL_DEBUG_INFO_CAPABILITY} 补丁，请更新内核以限制 WebGL 显卡信息")
    if canvas_readback == "blocked" and CANVAS_READBACK_CAPABILITY not in capabilities:
        raise ValueError(f"当前内核缺少 {CANVAS_READBACK_CAPABILITY} 补丁，请更新内核以限制 Canvas 2D 读取")
    if hardware_reporting == "limited" and HARDWARE_REPORTING_CAPABILITY not in capabilities:
        raise ValueError(f"当前内核缺少 {HARDWARE_REPORTING_CAPABILITY} 补丁，请更新内核以限制硬件信息")


def probe(path: Path, *, capability: str = CAPABILITY) -> tuple[Path, str]:
    path, version, capabilities = probe_capabilities(path)
    if capability not in capabilities:
        raise ValueError(f"this binary does not report the {capability} capability: {path}")
    return path, version


def chromium_owns_directory(user_data: Path) -> bool:
    # Chromium also creates a lock when launched directly, or if our launcher exits first.
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
