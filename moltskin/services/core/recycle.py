"""Archive and restore stopped profiles without losing their storage or session history."""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Lock

from fastapi import HTTPException

from chromium.native import chromium_owns_directory
from . import config, db


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


# The local self-test opens a browser outside the launcher's session table.
_busy_profiles: set[str] = set()
_busy_lock = Lock()


@contextmanager
def reserve_profile(profile_id: str):
    with _busy_lock:
        if profile_id in _busy_profiles:
            raise _error(409, "PROFILE_BUSY", "环境正在操作中，请稍后重试。")
        _busy_profiles.add(profile_id)
    try:
        yield
    finally:
        with _busy_lock:
            _busy_profiles.remove(profile_id)


def _profile_dir(profile_id: str, saved: str | None) -> Path:
    try:
        return config.stored_profile_directory(profile_id, saved)
    except (ValueError, OSError, RuntimeError) as exc:
        raise _error(409, "INVALID_PROFILE_DIRECTORY", "环境目录异常，请检查数据目录。") from exc


def remove_profile_record(conn: sqlite3.Connection, profile_id: str, delete_data: bool) -> str | None:
    """Remove a stopped profile and return the validated data directory when requested."""
    row = conn.execute("SELECT engine, status, user_data_dir FROM profiles WHERE id=?", (profile_id,)).fetchone()
    if row is None:
        raise _error(404, "PROFILE_NOT_FOUND", "环境不存在")
    if row["status"] == "running":
        raise _error(409, "PROFILE_RUNNING", "请先关闭环境")
    directory = row["user_data_dir"] if delete_data else None
    if delete_data and row["engine"] == "chromium":
        path = _profile_dir(profile_id, directory)
        if chromium_owns_directory(path):
            raise _error(409, "PROFILE_RUNNING", "请先关闭环境")
        directory = str(path)
    conn.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
    return directory


def _log(conn: sqlite3.Connection, action: str, profile_id: str, detail: dict | None = None):
    conn.execute("INSERT INTO logs(actor, action, target_id, detail, created_at) VALUES ('local', ?, ?, ?, ?)",
                 (action, profile_id, json.dumps(detail or {}, ensure_ascii=False), db.now()))


def _record(conn: sqlite3.Connection, profile_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM trashed_profiles WHERE id=?", (profile_id,)).fetchone()
    if row is None:
        raise _error(404, "TRASH_NOT_FOUND", "回收站中没有此环境")
    if conn.execute("SELECT 1 FROM profiles WHERE id=?", (profile_id,)).fetchone():
        raise _error(409, "PROFILE_ID_CONFLICT", "已有相同 ID 的活动环境")
    return row


def _profile_data(row: sqlite3.Row) -> dict:
    try:
        profile = json.loads(row["profile_json"])
    except (TypeError, ValueError) as exc:
        raise _error(409, "INVALID_TRASH_RECORD", "回收站环境记录异常") from exc
    if (not isinstance(profile, dict) or any(profile.get(k) != row[k] for k in ("id", "name", "engine"))
            or profile.get("status") not in ("draft", "ready", "crashed", "stopped")):
        raise _error(409, "INVALID_TRASH_RECORD", "回收站环境记录异常")
    return profile


def list_profiles() -> list[dict]:
    with db.session() as conn:
        rows = conn.execute(
            "SELECT id, name, engine, trashed_at, purge_started FROM trashed_profiles ORDER BY trashed_at DESC, id"
        ).fetchall()
    return [{**row, "purge_started": bool(row["purge_started"])} for row in rows]


def archive(profile_id: str) -> dict:
    with reserve_profile(profile_id), db.session() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            raise _error(404, "PROFILE_NOT_FOUND", "环境不存在")
        path = _profile_dir(profile_id, row["user_data_dir"])
        sessions = db.rows_to_dicts(conn.execute("SELECT * FROM sessions WHERE profile_id=?", (profile_id,)))
        if (row["status"] == "running" or any(s["state"] in ("starting", "running", "stopping") for s in sessions)
                or row["engine"] == "chromium" and chromium_owns_directory(path)):
            raise _error(409, "PROFILE_RUNNING", "请先关闭环境")
        extensions = [r[0] for r in conn.execute("SELECT ext_id FROM profile_extensions WHERE profile_id=?", (profile_id,))]
        ts = db.now()
        conn.execute(
            "INSERT INTO trashed_profiles(id,name,engine,trashed_at,profile_json,sessions_json,extensions_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (profile_id, row["name"], row["engine"], ts, json.dumps(dict(row), ensure_ascii=False),
             json.dumps(sessions, ensure_ascii=False), json.dumps(extensions)),
        )
        conn.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
        _log(conn, "profile.trash", profile_id)
    return {"id": profile_id, "name": row["name"], "engine": row["engine"], "trashed_at": ts, "purge_started": False}


def _insert_saved(conn: sqlite3.Connection, table: str, saved: dict, columns: list[str]):
    # Only local schema column names reach SQL. Newly added columns keep their defaults.
    names = [name for name in columns if name in saved]
    identifiers = ",".join('"' + name.replace('"', '""') + '"' for name in names)
    placeholders = ",".join("?" for _ in names)
    conn.execute(f"INSERT INTO {table} ({identifiers}) VALUES ({placeholders})", [saved[name] for name in names])


def restore(profile_id: str) -> None:
    limit = int(db.get_setting("profile_limit", 200) or 0)
    with ExitStack() as cleanup:
        with db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = _record(conn, profile_id)
            if row["purge_started"]:
                raise _error(409, "PURGE_STARTED", "永久删除已开始，请重试永久删除。")
            if limit > 0 and conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] >= limit:
                raise _error(409, "PROFILE_LIMIT", f"环境数量已达上限（{limit}），可在设置中调整")
            profile = _profile_data(row)
            path = _profile_dir(profile_id, profile.get("user_data_dir"))
            profile["name"] = db.available_profile_name(conn, row["name"])
            if profile.get("group_id") and not conn.execute("SELECT 1 FROM groups WHERE id=?", (profile["group_id"],)).fetchone():
                profile["group_id"] = None
            if profile.get("proxy_id") and not conn.execute("SELECT 1 FROM proxies WHERE id=?", (profile["proxy_id"],)).fetchone():
                profile["proxy_id"] = None
            try:
                sessions = json.loads(row["sessions_json"])
            except (TypeError, ValueError) as exc:
                raise _error(409, "INVALID_TRASH_RECORD", "回收站中的会话记录异常") from exc
            if not db.valid_archived_sessions(sessions, profile_id):
                raise _error(409, "INVALID_TRASH_RECORD", "回收站中的会话记录异常")
            profile_columns = [c["name"] for c in conn.execute("PRAGMA table_info(profiles)")]
            session_columns = [c["name"] for c in conn.execute("PRAGMA table_info(sessions)")]
            _insert_saved(conn, "profiles", profile, profile_columns)
            for session in sessions:
                _insert_saved(conn, "sessions", session, session_columns)
            conn.execute(
                "INSERT INTO profile_extensions(profile_id, ext_id) "
                "SELECT ?, id FROM extensions WHERE id IN (SELECT value FROM json_each(?))",
                (profile_id, row["extensions_json"]),
            )
            if not path.exists():
                path.mkdir(mode=0o700)
                cleanup.callback(path.rmdir)
            conn.execute("DELETE FROM trashed_profiles WHERE id=?", (profile_id,))
            _log(conn, "profile.restore", profile_id, {"name": profile["name"]})
        cleanup.pop_all()


def purge(profile_id: str) -> None:
    # Commit intent first. Even after a crash or partial rmtree the remaining files
    # can only be purged, never mistaken for a completely restorable profile.
    with db.session() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _record(conn, profile_id)
        path = _profile_dir(profile_id, _profile_data(row).get("user_data_dir"))
        if row["engine"] == "chromium" and chromium_owns_directory(path):
            raise _error(409, "PROFILE_RUNNING", "请先关闭环境")
        conn.execute("UPDATE trashed_profiles SET purge_started=1 WHERE id=?", (profile_id,))

    with db.session() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _record(conn, profile_id)
        path = _profile_dir(profile_id, _profile_data(row).get("user_data_dir"))
        if not row["purge_started"]:
            raise _error(409, "PROFILE_CHANGED", "回收站环境已更新，请刷新后重试。")
        if path.exists():
            try:
                shutil.rmtree(path)
            except OSError as exc:
                raise _error(500, "PURGE_FAILED", "环境文件未完全删除，请检查目录权限后重试。") from exc
        conn.execute("DELETE FROM trashed_profiles WHERE id=?", (profile_id,))
        _log(conn, "profile.purge", profile_id)
