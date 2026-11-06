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
  browser_version  TEXT,
  fingerprint      TEXT NOT NULL DEFAULT '{}',
  user_data_dir    TEXT NOT NULL,
  headless         INTEGER NOT NULL DEFAULT 0,
  status           TEXT NOT NULL DEFAULT 'draft',
  last_session_id  TEXT,
  last_opened_at   TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_profiles_status ON profiles(status);
CREATE INDEX IF NOT EXISTS idx_profiles_group  ON profiles(group_id);
CREATE INDEX IF NOT EXISTS idx_profiles_proxy  ON profiles(proxy_id);

CREATE TABLE IF NOT EXISTS sessions (
  id            TEXT PRIMARY KEY,
  profile_id    TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
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
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        for key, value in config.DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )


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
