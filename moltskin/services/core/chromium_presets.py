"""Self-consistent real-world Chromium fingerprint bundle generator."""

from __future__ import annotations

import os
import random
from typing import Any

from platforms import current as host_platform
from chromium import native as chromium_native

from . import config

# Curated real-world hardware templates where OS, CPU architecture, GPU ANGLE
# backend, CPU core count, Web-capped deviceMemory, screen resolution, and
# outer window size are 100% mutually consistent.
_MACOS_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "label": "MacBook Air (Apple M1 · 8核 / 8GB)",
        "chromium_platform": "macos:14.6.0:arm64",
        "chromium_webgl": "Apple M1|Google Inc. (Apple)|ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)",
        "concurrency_choices": (8,),
        "memory_choices": (8,),
        "screen_window_pairs": (
            ("1440x900x24@1", "1280x800"),
            ("1920x1080x24@1", "1440x900"),
            ("2560x1440x24@1", "1440x900"),
        ),
    },
    {
        "label": "MacBook Air (Apple M2 · 8核 / 8GB)",
        "chromium_platform": "macos:14.6.0:arm64",
        "chromium_webgl": "Apple M2|Google Inc. (Apple)|ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)",
        "concurrency_choices": (8,),
        "memory_choices": (8,),
        "screen_window_pairs": (
            ("1440x900x24@1", "1280x800"),
            ("1920x1080x24@1", "1440x900"),
            ("2560x1440x24@1", "1440x900"),
        ),
    },
    {
        "label": "MacBook Pro (Apple M1 Pro · 8-12核 / 16GB+)",
        "chromium_platform": "macos:14.6.0:arm64",
        "chromium_webgl": "Apple M1 Pro|Google Inc. (Apple)|ANGLE (Apple, ANGLE Metal Renderer: Apple M1 Pro, Unspecified Version)",
        "concurrency_choices": (8, 12),
        "memory_choices": (8,),
        "screen_window_pairs": (
            ("1920x1080x24@1", "1440x900"),
            ("2560x1440x24@1", "1440x900"),
            ("2560x1440x24@1.25", "1920x1080"),
        ),
    },
    {
        "label": "MacBook Pro (Apple M3 · 8-12核 / 16GB+)",
        "chromium_platform": "macos:14.6.0:arm64",
        "chromium_webgl": "Apple M3|Google Inc. (Apple)|ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)",
        "concurrency_choices": (8, 12),
        "memory_choices": (8,),
        "screen_window_pairs": (
            ("1440x900x24@1", "1280x800"),
            ("1920x1080x24@1", "1440x900"),
            ("2560x1440x24@1", "1920x1080"),
        ),
    },
)

_WINDOWS_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "label": "Windows PC (Intel UHD 630 · 6-8核 / 8GB)",
        "chromium_platform": "windows:10.0.0:x86_64",
        "chromium_webgl": "Intel UHD 630|Google Inc. (Intel)|ANGLE (Intel, Intel(R) UHD Graphics 630 (0x00003E9B) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "concurrency_choices": (6, 8),
        "memory_choices": (8,),
        "screen_window_pairs": (
            ("1920x1080x24@1", "1440x900"),
            ("1920x1080x24@1.25", "1280x800"),
            ("1536x864x24@1.25", "1280x800"),
        ),
    },
    {
        "label": "Windows Laptop (Intel Iris Xe · 8-12核 / 16GB)",
        "chromium_platform": "windows:10.0.0:x86_64",
        "chromium_webgl": "Intel Iris Xe|Google Inc. (Intel)|ANGLE (Intel, Intel(R) Iris(R) Xe Graphics (0x00009A49) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "concurrency_choices": (8, 12),
        "memory_choices": (8,),
        "screen_window_pairs": (
            ("1536x864x24@1.25", "1280x800"),
            ("1920x1080x24@1.25", "1440x900"),
            ("2560x1440x24@1.25", "1440x900"),
        ),
    },
    {
        "label": "Windows Desktop (NVIDIA RTX 3060 · 12-16核 / 16GB+)",
        "chromium_platform": "windows:10.0.0:x86_64",
        "chromium_webgl": "NVIDIA GeForce RTX 3060|Google Inc. (NVIDIA)|ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "concurrency_choices": (12, 16),
        "memory_choices": (8,),
        "screen_window_pairs": (
            ("1920x1080x24@1", "1440x900"),
            ("2560x1440x24@1", "1920x1080"),
            ("2560x1440x24@1.25", "1920x1080"),
        ),
    },
    {
        "label": "Windows Desktop (AMD Radeon RX 6600 · 12-16核 / 16GB+)",
        "chromium_platform": "windows:10.0.0:x86_64",
        "chromium_webgl": "AMD Radeon RX 6600|Google Inc. (AMD)|ANGLE (AMD, AMD Radeon RX 6600 (0x00000073) Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "concurrency_choices": (12, 16),
        "memory_choices": (8,),
        "screen_window_pairs": (
            ("1920x1080x24@1", "1440x900"),
            ("2560x1440x24@1", "1920x1080"),
        ),
    },
)

_LINUX_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "label": "Linux Workstation (8-12核 / 8GB)",
        "chromium_platform": "linux:6.8.0:x86_64",
        "chromium_webgl": "",
        "concurrency_choices": (8, 12),
        "memory_choices": (8,),
        "screen_window_pairs": (
            ("1920x1080x24@1", "1440x900"),
            ("2560x1440x24@1", "1920x1080"),
        ),
    },
)


def _installed_major_version(chromium_version: str = "") -> str:
    try:
        if chromium_version:
            return chromium_version.split(".", 1)[0]
        _, default_ver, _ = chromium_native.build_catalog()
        if default_ver:
            return default_ver.split(".", 1)[0]
    except Exception:  # noqa: BLE001
        pass
    return "158"


def _reduced_user_agent(os_family: str, major: str) -> str:
    if os_family == "macos":
        os_token = "Macintosh; Intel Mac OS X 10_15_7"
    elif os_family == "linux":
        os_token = "X11; Linux x86_64"
    else:
        os_token = "Windows NT 10.0; Win64; x64"
    return (
        f"Mozilla/5.0 ({os_token}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )


def resolve_os_family(os_family: str = "auto") -> str:
    normalized = (os_family or "auto").strip().lower()
    if normalized == "auto":
        family = host_platform.family
        return family if family in ("macos", "windows", "linux") else "windows"
    if normalized not in ("macos", "windows", "linux"):
        raise ValueError("不支持的画像系统类型，可选 auto / macos / windows / linux")
    return normalized


def generate_chromium_preset(
    os_family: str = "auto",
    *,
    chromium_version: str = "",
    seed: int | None = None,
) -> dict[str, Any]:
    """Generate a complete, internally consistent Chromium fingerprint bundle."""
    target_os = resolve_os_family(os_family)
    rng = random.Random(seed if seed is not None else int.from_bytes(os.urandom(8), "big"))
    templates = (
        _MACOS_TEMPLATES
        if target_os == "macos"
        else _LINUX_TEMPLATES
        if target_os == "linux"
        else _WINDOWS_TEMPLATES
    )
    chosen = rng.choice(templates)
    screen, window_size = rng.choice(chosen["screen_window_pairs"])
    concurrency = rng.choice(chosen["concurrency_choices"])
    memory = rng.choice(chosen["memory_choices"])
    major = _installed_major_version(chromium_version)
    ua = config.validate_chromium_user_agent(_reduced_user_agent(target_os, major))

    return {
        "os_family": target_os,
        "template_label": chosen["label"],
        "chromium_platform": chosen["chromium_platform"],
        "chromium_user_agent": ua,
        "chromium_webgl": chosen["chromium_webgl"],
        "chromium_screen": screen,
        "browser_window_size": window_size,
        "chromium_hardware_concurrency": concurrency,
        "chromium_device_memory": memory,
        "chromium_canvas_noise": True,
        "chromium_audio_noise": True,
        "geolocation": "auto",
        "webrtc_policy": "proxy",
    }
