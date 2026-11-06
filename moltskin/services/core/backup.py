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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

MANIFEST = "manifest.json"
DB_NAME = "moltskin.db"


def _snapshot_db(dest: Path) -> None:
    source = sqlite3.connect(config.DB_PATH)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def create(include_profiles: bool = False) -> Path:
    """生成备份文件，返回临时路径。"""
    tmp = Path(tempfile.mkdtemp(prefix="moltskin-backup-")) / "moltskin-backup.zip"
    db_copy = tmp.parent / DB_NAME
    _snapshot_db(db_copy)

    with sqlite3.connect(db_copy) as conn:
        conn.row_factory = sqlite3.Row
        profiles = [
            {"id": row["id"], "name": row["name"]}
            for row in conn.execute("SELECT id, name FROM profiles ORDER BY created_at")
        ]

    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_copy, DB_NAME)
        ext_dir = config.DATA_DIR / "extensions"
        if ext_dir.exists():
            for path in ext_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, str(Path("extensions") / path.relative_to(ext_dir)))
        if include_profiles:
            for profile in profiles:
                base = config.PROFILES_DIR / profile["id"]
                cookies = base / "cookies.sqlite"
                if cookies.exists():
                    zf.write(cookies, f"profiles/{profile['id']}/cookies.sqlite")
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
                    "version": "0.4.0",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "include_profiles": include_profiles,
                    "profiles": profiles,
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

        # 校验即将替换的数据库能打开且有 profiles 表
        try:
            conn = sqlite3.connect(f"file:{incoming}?mode=ro", uri=True)
            conn.execute("SELECT COUNT(*) FROM profiles").fetchone()
            conn.close()
        except sqlite3.Error as exc:
            raise ValueError(f"备份中的数据库不可用：{exc}")

        config.ensure_dirs()
        for suffix in ("-wal", "-shm"):
            Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
        backup_path = config.DB_PATH.with_suffix(".db.bak")
        if config.DB_PATH.exists():
            shutil.copy2(config.DB_PATH, backup_path)
        shutil.copy2(incoming, config.DB_PATH)

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
                if not profile_dir.is_dir():
                    continue
                dest = config.PROFILES_DIR / profile_dir.name
                dest.mkdir(parents=True, exist_ok=True)
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
