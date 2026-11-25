"""SQLite 数据层（M0 使用标准库 sqlite3，够用且零依赖）。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS groups (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  parent_id   TEXT REFERENCES groups(id) ON DELETE SET NULL,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proxies (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  protocol        TEXT NOT NULL CHECK (protocol IN ('http','https','socks5')),
  host            TEXT NOT NULL,
  port            INTEGER NOT NULL,
  username        TEXT,
  password        TEXT,
  exit_ip         TEXT,
  country         TEXT,
  timezone        TEXT,
  latency_ms      INTEGER,
  status          TEXT NOT NULL DEFAULT 'unchecked',
  last_checked_at TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
  id               TEXT PRIMARY KEY,
  name             TEXT NOT NULL UNIQUE,
  group_id         TEXT REFERENCES groups(id) ON DELETE SET NULL,
  proxy_id         TEXT REFERENCES proxies(id) ON DELETE SET NULL,
  tags             TEXT NOT NULL DEFAULT '[]',
  remark           TEXT NOT NULL DEFAULT '',
  os               TEXT NOT NULL DEFAULT 'windows',
  engine           TEXT NOT NULL DEFAULT 'camoufox' CHECK (engine IN ('camoufox','chromium')),
  chromium_version TEXT NOT NULL DEFAULT '',
  local_fonts      TEXT NOT NULL DEFAULT 'standard' CHECK (local_fonts IN ('standard','restricted')),
  media_capture    TEXT NOT NULL DEFAULT 'standard' CHECK (media_capture IN ('standard','blocked')),
  geolocation      TEXT NOT NULL DEFAULT 'standard' CHECK (geolocation IN ('standard','blocked')),
  chromium_js_heap_mb INTEGER NOT NULL DEFAULT 0,
  chromium_mute_audio INTEGER NOT NULL DEFAULT 0,
  chromium_renderer_limit INTEGER NOT NULL DEFAULT 0,
  browser_language TEXT NOT NULL DEFAULT '',
  browser_timezone TEXT NOT NULL DEFAULT '',
  browser_window_size TEXT NOT NULL DEFAULT '',
  startup_page     TEXT NOT NULL DEFAULT 'default' CHECK (startup_page IN ('default','dashboard','blank')),
  block_images     INTEGER NOT NULL DEFAULT 0 CHECK (block_images IN (0,1)),
  browser_version  TEXT,
  fingerprint      TEXT NOT NULL DEFAULT '{}',
  user_data_dir    TEXT NOT NULL,
  headless         INTEGER NOT NULL DEFAULT 0,
  status           TEXT NOT NULL DEFAULT 'draft',
  last_session_id  TEXT,
  last_opened_at   TEXT,
  open_count       INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_profiles_status ON profiles(status);
CREATE INDEX IF NOT EXISTS idx_profiles_group  ON profiles(group_id);
CREATE INDEX IF NOT EXISTS idx_profiles_proxy  ON profiles(proxy_id);

CREATE TABLE IF NOT EXISTS trashed_profiles (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  engine          TEXT NOT NULL,
  trashed_at      TEXT NOT NULL,
  purge_started   INTEGER NOT NULL DEFAULT 0 CHECK (purge_started IN (0,1)),
  profile_json    TEXT NOT NULL,
  sessions_json   TEXT NOT NULL,
  extensions_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_preset_drafts (
  id          TEXT NOT NULL,
  os          TEXT NOT NULL,
  preset      TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (id, os)
);

CREATE TABLE IF NOT EXISTS sessions (
  id            TEXT PRIMARY KEY,
  profile_id    TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  browser_version TEXT,
  pid           INTEGER,
  state         TEXT NOT NULL,
  error_code    TEXT,
  error_message TEXT,
  started_at    TEXT NOT NULL,
  ended_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_profile ON sessions(profile_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_state   ON sessions(state);

CREATE TABLE IF NOT EXISTS logs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  actor      TEXT,
  action     TEXT NOT NULL,
  target_id  TEXT,
  detail     TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extensions (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  version     TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  filename    TEXT NOT NULL DEFAULT '',
  size        INTEGER NOT NULL DEFAULT 0,
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_extensions (
  profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  ext_id     TEXT NOT NULL REFERENCES extensions(id) ON DELETE CASCADE,
  PRIMARY KEY (profile_id, ext_id)
);

CREATE TABLE IF NOT EXISTS schedules (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  action      TEXT NOT NULL CHECK (action IN ('launch','stop')),
  mode        TEXT NOT NULL DEFAULT 'daily',
  interval_minutes INTEGER NOT NULL DEFAULT 60,
  time_of_day TEXT NOT NULL,
  weekdays    TEXT NOT NULL DEFAULT '[1,2,3,4,5,6,7]',
  target_type TEXT NOT NULL DEFAULT 'all',
  target_ids  TEXT NOT NULL DEFAULT '[]',
  enabled     INTEGER NOT NULL DEFAULT 1,
  last_run_at TEXT,
  last_result TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_start_utc() -> str:
    """本地“今天 0 点”对应的 UTC 时间。

    数据库里存的是 UTC ISO 字符串，直接用本地 0 点去比会错开时区，
    导致“今日打开”少算或调度任务重复触发。
    """
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc).isoformat()


def count_launches_since(conn: sqlite3.Connection, since: str) -> int:
    """Trashing an environment must not reset today's launch usage."""
    return conn.execute(
        "SELECT (SELECT COUNT(*) FROM sessions WHERE started_at >= ?) + "
        "(SELECT COUNT(*) FROM trashed_profiles t, json_each(t.sessions_json) h "
        "WHERE t.trashed_at >= ? AND json_extract(h.value, '$.started_at') >= ?)",
        (since, since, since),
    ).fetchone()[0]


def valid_archived_sessions(sessions: Any, profile_id: str) -> bool:
    return isinstance(sessions, list) and all(
        isinstance(session, dict) and session.get("profile_id") == profile_id
        and session.get("state") in ("stopped", "crashed", "failed", "killed")
        for session in sessions
    )


def local_date(iso: str | None):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone().date()
    except ValueError:
        return None


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def session():
    """自动提交/回滚并关闭连接。"""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    config.ensure_dirs()
    with session() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        for key, value in config.DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )


def _migrate(conn: sqlite3.Connection) -> None:
    """给旧库补齐后加的列，保持升级不丢数据。"""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(profiles)")}
    additions = {
        "browser_version": "TEXT",
        "open_count": "INTEGER NOT NULL DEFAULT 0",
        "engine": "TEXT NOT NULL DEFAULT 'camoufox' CHECK (engine IN ('camoufox','chromium'))",
        "chromium_version": "TEXT NOT NULL DEFAULT ''",
        "local_fonts": "TEXT NOT NULL DEFAULT 'standard' CHECK (local_fonts IN ('standard','restricted'))",
        "media_capture": "TEXT NOT NULL DEFAULT 'standard' CHECK (media_capture IN ('standard','blocked'))",
        "geolocation": "TEXT NOT NULL DEFAULT 'standard' CHECK (geolocation IN ('standard','blocked'))",
        "chromium_js_heap_mb": "INTEGER NOT NULL DEFAULT 0",
        "chromium_mute_audio": "INTEGER NOT NULL DEFAULT 0",
        "chromium_renderer_limit": "INTEGER NOT NULL DEFAULT 0",
        "browser_language": "TEXT NOT NULL DEFAULT ''",
        "browser_timezone": "TEXT NOT NULL DEFAULT ''",
        "browser_window_size": "TEXT NOT NULL DEFAULT ''",
        "startup_page": "TEXT NOT NULL DEFAULT 'default' CHECK (startup_page IN ('default','dashboard','blank'))",
        "block_images": "INTEGER NOT NULL DEFAULT 0 CHECK (block_images IN (0,1))",
    }
    for name, ddl in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE profiles ADD COLUMN {name} {ddl}")

    session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "browser_version" not in session_columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN browser_version TEXT")

    schedule_columns = {row["name"] for row in conn.execute("PRAGMA table_info(schedules)")}
    schedule_additions = {
        "mode": "TEXT NOT NULL DEFAULT 'daily'",
        "interval_minutes": "INTEGER NOT NULL DEFAULT 60",
    }
    for name, ddl in schedule_additions.items():
        if schedule_columns and name not in schedule_columns:
            conn.execute(f"ALTER TABLE schedules ADD COLUMN {name} {ddl}")


def get_setting(key: str, default: Any = None) -> Any:
    with session() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return config.DEFAULTS.get(key, default)
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return default


def set_setting(key: str, value: Any) -> None:
    with session() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def log(action: str, target_id: str | None = None, detail: Any = None, actor: str = "local") -> None:
    with session() as conn:
        conn.execute(
            "INSERT INTO logs(actor, action, target_id, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (actor, action, target_id, json.dumps(detail or {}, ensure_ascii=False), now()),
        )


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def available_profile_name(conn: sqlite3.Connection, base: str, suffix: str = "") -> str:
    """Allocate a display name while the caller holds the database write lock."""
    index = 1
    while True:
        ending = suffix if index == 1 else f"{suffix} ({index})"
        name = base[:120 - len(ending)] + ending
        if not conn.execute("SELECT 1 FROM profiles WHERE name=?", (name,)).fetchone():
            return name
        index += 1
