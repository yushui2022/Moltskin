"""Moltskin 配置与路径。"""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

# moltskin/services/core/config.py -> 项目根目录
ROOT = Path(__file__).resolve().parents[3]

DATA_DIR = Path(os.environ.get("MOLTSKIN_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "moltskin.db"
PROFILES_DIR = DATA_DIR / "profiles"
RUN_DIR = DATA_DIR / "run"
LOG_DIR = DATA_DIR / "logs"
SELFTEST_DIR = DATA_DIR / "selftest"
TOKEN_PATH = DATA_DIR / "api.token"
WEB_DIR = ROOT / "web"

HOST = "127.0.0.1"
PORT = int(os.environ.get("MOLTSKIN_PORT", "8765"))
MANAGER_URL = os.environ.get("MOLTSKIN_MANAGER_URL", f"http://127.0.0.1:{PORT}")

CHROMIUM_JS_HEAP_LIMITS = (256, 512, 1024)
CHROMIUM_RENDERER_LIMITS = (2, 4, 8)
CHROMIUM_BROWSER_LANGUAGES = ("zh-CN", "en-US", "en-GB", "ja-JP", "de-DE", "fr-FR")
CHROMIUM_BROWSER_WINDOW_SIZES = ("1024x768", "1280x800", "1440x900", "1920x1080")
CHROMIUM_BROWSER_TIMEZONES = (
    "Asia/Shanghai", "Asia/Tokyo", "Europe/London", "Europe/Berlin", "Europe/Paris",
    "America/New_York", "America/Los_Angeles", "UTC",
)

# Docker / server 部署：强制无头、不自动打开管理页
FORCE_HEADLESS = os.environ.get("MOLTSKIN_HEADLESS", "").strip().lower() in ("1", "true", "yes", "on")
OPEN_MANAGER = os.environ.get("MOLTSKIN_OPEN_MANAGER", "1").strip().lower() not in ("0", "false", "no", "off")

# 运行时默认值，可被 settings 表覆盖
DEFAULTS = {
    "max_concurrent": 10,
    "batch_concurrency": 5,
    "launch_timeout_sec": 30,
    "stop_timeout_sec": 8,
    "proxy_check_url": "https://ipinfo.io/json",
    "require_proxy_ok": False,
    "headless": False,
    "open_manager_on_launch": True,
    "proxy_health_check_minutes": 30,
    "manager_button": True,
    "profile_limit": 200,
    "daily_open_limit": 20000,
}


def local_dashboard_url() -> str:
    return f"http://127.0.0.1:{PORT}/#/home"


def profile_directory(profile_id: str) -> Path:
    """Return a direct child of the data root that can safely hold a profile."""
    if (not isinstance(profile_id, str) or not profile_id or profile_id in (".", "..")
            or "/" in profile_id or "\\" in profile_id or "\x00" in profile_id
            or PureWindowsPath(profile_id).drive):
        raise ValueError("环境 ID 无效")
    path = PROFILES_DIR.resolve() / profile_id
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError("存储位置已有同名文件或环境目录链接")
    return path


def stored_profile_directory(profile_id: str, saved: str | None) -> Path:
    """Check a managed profile's recorded path against the directory assigned to its ID."""
    path = profile_directory(profile_id)
    if not isinstance(saved, str) or Path(saved).absolute() not in (path, (PROFILES_DIR / profile_id).absolute()):
        raise ValueError("环境记录的目录与环境 ID 不匹配")
    return path


def ensure_dirs() -> None:
    for path in (DATA_DIR, PROFILES_DIR, RUN_DIR, LOG_DIR, SELFTEST_DIR):
        path.mkdir(parents=True, exist_ok=True)
