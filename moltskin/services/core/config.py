"""Moltskin 配置与路径。"""

from __future__ import annotations

import os
from pathlib import Path

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


def ensure_dirs() -> None:
    for path in (DATA_DIR, PROFILES_DIR, RUN_DIR, LOG_DIR, SELFTEST_DIR):
        path.mkdir(parents=True, exist_ok=True)
