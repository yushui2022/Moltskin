"""备份与恢复。

备份包是普通 zip：SQLite 快照 + 扩展目录 + （可选）每个环境的 Cookie 与
localStorage。恢复会替换数据库；为了安全，任何时候只要还有环境在运行就拒绝。
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone

from moltskin import __version__
from pathlib import Path
from typing import Any

from . import config, db
from .cookies import SESSION_FILENAME

MANIFEST = "manifest.json"
DB_NAME = "moltskin.db"


def _copy_database(source: Path, destination: Path) -> None:
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


def create(include_profiles: bool = False) -> Path:
    """生成备份文件，返回临时路径（同目录会在响应结束后删除）。"""
    workdir = Path(tempfile.mkdtemp(prefix="moltskin-backup-"))
    try:
        return _write_archive(workdir, include_profiles)
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def _write_archive(workdir: Path, include_profiles: bool) -> Path:
    tmp = workdir / "moltskin-backup.zip"
    db_copy = tmp.parent / DB_NAME
    _copy_database(config.DB_PATH.resolve(), db_copy)

    with closing(sqlite3.connect(db_copy)) as conn, conn:
        conn.execute("DROP TABLE IF EXISTS profile_preset_drafts")
        conn.row_factory = sqlite3.Row
        profiles = [
            dict(row)
            for row in conn.execute("SELECT id, name, engine FROM profiles ORDER BY created_at")
        ]
        trashed = [
            dict(row) for row in conn.execute(
                "SELECT id, name, engine, purge_started FROM trashed_profiles ORDER BY trashed_at"
            )
        ]
    all_profiles = profiles + trashed

    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_copy, DB_NAME)
        ext_dir = config.DATA_DIR / "extensions"
        if ext_dir.exists():
            for path in ext_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, str(Path("extensions") / path.relative_to(ext_dir)))
        if include_profiles:
            for profile in all_profiles:
                if profile["engine"] != "camoufox" or profile.get("purge_started"):
                    continue
                base = config.profile_directory(profile["id"])
                for name in ("cookies.sqlite", SESSION_FILENAME):
                    cookies = base / name
                    if cookies.is_file():
                        zf.write(cookies, f"profiles/{profile['id']}/{name}")
                storage = base / "storage"
                if storage.exists():
                    for path in storage.rglob("*"):
                        if path.is_file():
                            zf.write(path, f"profiles/{profile['id']}/storage/{path.relative_to(storage)}")

        zf.writestr(
            MANIFEST,
            json.dumps(
                {
                    "app": "moltskin",
                    "version": __version__,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "include_profiles": include_profiles,
                    "profiles": profiles,
                    "trashed_profiles": trashed,
                    "profile_data_excluded": [
                        p["id"] for p in all_profiles
                        if include_profiles and (p["engine"] != "camoufox" or p.get("purge_started"))
                    ],
                    "extensions": sum(1 for _ in (config.DATA_DIR / "extensions").rglob("*"))
                    if (config.DATA_DIR / "extensions").exists()
                    else 0,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    db_copy.unlink(missing_ok=True)
    return tmp


def _prepare_incoming(incoming: Path) -> set[str]:
    """Rebase a private backup copy before changing the live database."""
    camoufox_ids = set()
    with closing(sqlite3.connect(incoming)) as conn, conn:
        conn.execute("PRAGMA trusted_schema=OFF")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(profiles)")}
        engine = "engine" if "engine" in columns else "'camoufox'"
        profiles = conn.execute(f"SELECT id, {engine} FROM profiles").fetchall()
        for profile_id, engine_name in profiles:
            dest = config.profile_directory(profile_id)
            conn.execute("UPDATE profiles SET user_data_dir=? WHERE id=?", (str(dest), profile_id))
            if engine_name == "camoufox":
                camoufox_ids.add(profile_id)

        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trashed_profiles'").fetchone():
            active_ids = {profile_id for profile_id, _ in profiles}
            for ident, name, engine_name, payload, history, extensions, purge_started in conn.execute(
                "SELECT id, name, engine, profile_json, sessions_json, extensions_json, purge_started FROM trashed_profiles"
            ):
                dest = config.profile_directory(ident)
                if not all(isinstance(value, str) for value in (payload, history, extensions)):
                    raise ValueError("备份中回收站环境的配置无效")
                profile = json.loads(payload)
                sessions = json.loads(history)
                ext_ids = json.loads(extensions)
                if (ident in active_ids or not isinstance(profile, dict) or profile.get("id") != ident
                        or profile.get("name") != name or profile.get("engine") != engine_name
                        or engine_name not in ("chromium", "camoufox")):
                    raise ValueError("备份中回收站环境的信息不一致")
                if (not db.valid_archived_sessions(sessions, ident)
                        or not isinstance(ext_ids, list) or any(not isinstance(e, str) for e in ext_ids)):
                    raise ValueError("备份中的回收站会话或扩展记录无效")
                profile["user_data_dir"] = str(dest)
                conn.execute("UPDATE trashed_profiles SET profile_json=? WHERE id=?",
                             (json.dumps(profile, ensure_ascii=False), ident))
                if engine_name == "camoufox" and not purge_started:
                    camoufox_ids.add(ident)

        conn.execute("UPDATE profiles SET status='ready' WHERE status='running'")
        conn.execute("UPDATE profiles SET last_session_id=NULL")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone():
            conn.execute(
                "UPDATE sessions SET state='stopped', pid=NULL, ended_at=COALESCE(ended_at, ?) "
                "WHERE state IN ('starting', 'running', 'stopping')",
                (datetime.now(timezone.utc).isoformat(),),
            )
    return camoufox_ids


def restore(archive: bytes) -> dict[str, Any]:
    """从备份包恢复。会先校验、再把当前数据库备份成 .bak。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile:
        raise ValueError("不是有效的 zip 备份文件")
    names = set(zf.namelist())
    if DB_NAME not in names:
        raise ValueError("备份包缺少 moltskin.db")
    manifest = {}
    if MANIFEST in names:
        try:
            manifest = json.loads(zf.read(MANIFEST).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            manifest = {}

    workdir = Path(tempfile.mkdtemp(prefix="moltskin-restore-"))
    try:
        zf.extractall(workdir)
        incoming = workdir / DB_NAME

        try:
            camoufox_ids = _prepare_incoming(incoming)
        except sqlite3.Error as exc:
            raise ValueError(f"备份中的数据库不可用：{exc}") from exc

        config.ensure_dirs()
        backup_path = config.DB_PATH.with_suffix(".db.bak")
        if config.DB_PATH.exists():
            _copy_database(config.DB_PATH.resolve(), backup_path)
        _copy_database(incoming.resolve(), config.DB_PATH)

        ext_in = workdir / "extensions"
        if ext_in.exists():
            target = config.DATA_DIR / "extensions"
            target.mkdir(parents=True, exist_ok=True)
            for path in ext_in.rglob("*"):
                if path.is_file():
                    dest = target / path.relative_to(ext_in)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, dest)

        profiles_in = workdir / "profiles"
        restored_profiles = 0
        if profiles_in.exists():
            for profile_dir in profiles_in.iterdir():
                if not profile_dir.is_dir() or profile_dir.name not in camoufox_ids:
                    continue
                dest = config.PROFILES_DIR / profile_dir.name
                dest.mkdir(parents=True, exist_ok=True)
                if (profile_dir / "cookies.sqlite").is_file() and not (profile_dir / SESSION_FILENAME).exists():
                    # A backup made before session imports has no pending cookies.
                    (dest / SESSION_FILENAME).unlink(missing_ok=True)
                for item in profile_dir.rglob("*"):
                    if item.is_file():
                        out = dest / item.relative_to(profile_dir)
                        out.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, out)
                restored_profiles += 1

        return {
            "profiles": restored_profiles,
            "extensions": sum(1 for p in (config.DATA_DIR / "extensions").rglob("*") if p.is_file())
            if (config.DATA_DIR / "extensions").exists()
            else 0,
            "manifest": manifest,
            "backup_path": str(backup_path) if backup_path.exists() else None,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
