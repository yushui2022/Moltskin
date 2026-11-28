"""备份与恢复。

备份包是普通 zip：SQLite 快照 + 扩展目录 + （可选）每个环境的轻量登录态
（Camoufox 与 Chromium 的 Cookie、LocalStorage、SessionStorage、IndexedDB 与扩展本地存储，
严格剔除各类缓存与进程锁文件）。恢复会替换数据库；只要还有环境在运行就拒绝。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any
import zipfile

from moltskin import __version__

from . import config, cookies as cookie_store, db
from .cookies import CHROMIUM_PORTABLE_FILENAME, SESSION_FILENAME
from .secret_store import decrypt, encrypt, is_encrypted, key_path
from .state_sync import CHROMIUM_EXCLUDED_SUBDIRS, CHROMIUM_STORAGE_DIRS, CHROMIUM_STORAGE_FILES

MANIFEST = "manifest.json"
DB_NAME = "moltskin.db"

# Backups should contain configuration, extensions and opt-in profile storage,
# never a full browser image. Cap the archive so a hostile zip cannot fill the
# disk before validation rejects it.
MAX_BACKUP_MEMBERS = 100_000
MAX_BACKUP_UNCOMPRESSED = 4 * 1024**3

CHROMIUM_ROOT_STATE_FILES = (
    "Last Version",
    "Local State",
    CHROMIUM_PORTABLE_FILENAME,
)
CHROMIUM_DEFAULT_STATE_FILES = (
    "Cookies",
    "Cookies-journal",
    "Preferences",
    "Secure Preferences",
    *CHROMIUM_STORAGE_FILES,
    "Network/Cookies",
    "Network/Cookies-journal",
    "Network/Trust Tokens",
    "Network/Trust Tokens-journal",
)
CHROMIUM_ALLOWED_DEFAULT_DIRS = frozenset(
    (*CHROMIUM_STORAGE_DIRS, "LocalStorage", "SessionStorage", "Extension Rules", "Extension Scripts")
)


def _copy_database(source: Path, destination: Path) -> None:
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


def _iter_chromium_state_files(base: Path) -> Iterator[tuple[Path, str]]:
    if not base.is_dir() or base.is_symlink():
        return
    root = base.resolve()
    for name in CHROMIUM_ROOT_STATE_FILES:
        candidate = base / name
        if candidate.is_file() and not candidate.is_symlink():
            yield candidate, name

    default_dir = base / "Default"
    if not default_dir.is_dir() or default_dir.is_symlink():
        return

    for rel_str in CHROMIUM_DEFAULT_STATE_FILES:
        candidate = default_dir.joinpath(*rel_str.split("/"))
        if candidate.is_file() and not candidate.is_symlink():
            if candidate.resolve().is_relative_to(root):
                yield candidate, f"Default/{rel_str}"

    for dir_name in sorted(CHROMIUM_ALLOWED_DEFAULT_DIRS):
        sub = default_dir / dir_name
        if not sub.is_dir() or sub.is_symlink():
            continue
        for item in sub.rglob("*"):
            if item.is_symlink() or not item.is_file():
                continue
            if not item.resolve().is_relative_to(root):
                continue
            rel = item.relative_to(sub)
            if any(part in CHROMIUM_EXCLUDED_SUBDIRS for part in rel.parts) or rel.name == "LOCK":
                continue
            yield item, f"Default/{dir_name}/{rel.as_posix()}"


def _is_allowed_chromium_restore_rel(rel_posix: str) -> bool:
    if rel_posix in CHROMIUM_ROOT_STATE_FILES:
        return True
    if not rel_posix.startswith("Default/"):
        return False
    inner = rel_posix[len("Default/") :]
    if inner in CHROMIUM_DEFAULT_STATE_FILES:
        return True
    top, sep, rest = inner.partition("/")
    if not sep or not rest or top not in CHROMIUM_ALLOWED_DEFAULT_DIRS:
        return False
    parts = rest.split("/")
    if any(part in CHROMIUM_EXCLUDED_SUBDIRS for part in parts) or parts[-1] == "LOCK":
        return False
    return True


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
            dict(row)
            for row in conn.execute(
                "SELECT id, name, engine, purge_started FROM trashed_profiles ORDER BY trashed_at"
            )
        ]
    all_profiles = profiles + trashed

    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_copy, DB_NAME)
        ext_dir = config.DATA_DIR / "extensions"
        if ext_dir.exists():
            for path in ext_dir.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    zf.write(path, str(Path("extensions") / path.relative_to(ext_dir)))
        secret_key = key_path()
        if secret_key.exists():
            zf.write(secret_key, "secrets/proxy.key")
        if include_profiles:
            for profile in all_profiles:
                if profile.get("purge_started"):
                    continue
                base = config.profile_directory(profile["id"])
                if profile["engine"] == "chromium":
                    for file_path, rel_arc in _iter_chromium_state_files(base):
                        zf.write(file_path, f"profiles/{profile['id']}/{rel_arc}")
                elif profile["engine"] == "camoufox":
                    for name in ("cookies.sqlite", SESSION_FILENAME):
                        cookies = base / name
                        if cookies.is_file() and not cookies.is_symlink():
                            zf.write(cookies, f"profiles/{profile['id']}/{name}")
                    storage = base / "storage"
                    if storage.exists() and not storage.is_symlink():
                        for path in storage.rglob("*"):
                            if path.is_file() and not path.is_symlink():
                                zf.write(
                                    path,
                                    f"profiles/{profile['id']}/storage/{path.relative_to(storage).as_posix()}",
                                )

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
                        p["id"]
                        for p in all_profiles
                        if include_profiles and p.get("purge_started")
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


def _checked_member(name: str) -> None:
    """Reject zip names that could escape the extraction directory."""
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        raise ValueError("备份包包含非法文件路径")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":" and name[0].isalpha()):
        raise ValueError("备份包包含绝对路径")
    trimmed = name[:-1] if name.endswith("/") else name
    if not trimmed or any(part in ("", ".", "..") for part in trimmed.split("/")):
        raise ValueError("备份包包含目录穿越路径")


def _safe_extract(zf: zipfile.ZipFile, destination: Path) -> None:
    """Extract a backup after validating every member, with size limits."""
    infos = zf.infolist()
    if len(infos) > MAX_BACKUP_MEMBERS:
        raise ValueError("备份包文件条目过多")
    total = 0
    for info in infos:
        _checked_member(info.filename)
        if info.flag_bits & 0x1:
            raise ValueError("备份包包含加密文件")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError("备份包包含非法符号链接")
        total += info.file_size
        if total > MAX_BACKUP_UNCOMPRESSED:
            raise ValueError("备份包解压后超过大小限制")

    written = 0
    for info in infos:
        target = destination.joinpath(*info.filename.rstrip("/").split("/"))
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as source, target.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_BACKUP_UNCOMPRESSED:
                    raise ValueError("备份包解压后超过大小限制")
                output.write(chunk)


def _prepare_incoming(incoming: Path, backup_key: bytes | None = None) -> dict[str, str]:
    """Rebase a private backup copy before changing the live database."""
    restorable: dict[str, str] = {}
    with closing(sqlite3.connect(incoming)) as conn, conn:
        conn.execute("PRAGMA trusted_schema=OFF")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(profiles)")}
        engine = "engine" if "engine" in columns else "'camoufox'"
        profiles = conn.execute(f"SELECT id, {engine} FROM profiles").fetchall()
        for profile_id, engine_name in profiles:
            dest = config.profile_directory(profile_id)
            conn.execute("UPDATE profiles SET user_data_dir=? WHERE id=?", (str(dest), profile_id))
            if engine_name in ("camoufox", "chromium"):
                restorable[profile_id] = engine_name

        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trashed_profiles'").fetchone():
            active_ids = {profile_id for profile_id, _ in profiles}
            for ident, name, engine_name, payload, history, extensions, purge_started in conn.execute(
                "SELECT id, name, engine, profile_json, sessions_json, extensions_json, purge_started FROM trashed_profiles"
            ):
                dest = config.profile_directory(ident)
                if not all(isinstance(value, str) for value in (payload, history, extensions)):
                    raise ValueError("备份中回收站环境的配置无效")
                try:
                    profile = json.loads(payload)
                    sessions = json.loads(history)
                    ext_ids = json.loads(extensions)
                except json.JSONDecodeError as exc:
                    raise ValueError("备份中回收站环境的配置无效") from exc
                if (
                    ident in active_ids
                    or not isinstance(profile, dict)
                    or profile.get("id") != ident
                    or profile.get("name") != name
                    or profile.get("engine") != engine_name
                    or engine_name not in ("chromium", "camoufox")
                ):
                    raise ValueError("备份中回收站环境的信息不一致")
                if (
                    not db.valid_archived_sessions(sessions, ident)
                    or not isinstance(ext_ids, list)
                    or any(not isinstance(e, str) for e in ext_ids)
                ):
                    raise ValueError("备份中的回收站会话或扩展记录无效")
                profile["user_data_dir"] = str(dest)
                conn.execute(
                    "UPDATE trashed_profiles SET profile_json=? WHERE id=?",
                    (json.dumps(profile, ensure_ascii=False), ident),
                )
                if not purge_started:
                    restorable[ident] = engine_name

        for proxy_id, password in conn.execute(
            "SELECT id, password FROM proxies WHERE password IS NOT NULL AND password != ''"
        ):
            if not is_encrypted(password):
                continue
            if backup_key is None:
                raise ValueError("备份包含加密代理密码，但缺少 proxy.key")
            conn.execute(
                "UPDATE proxies SET password=? WHERE id=?",
                (encrypt(decrypt(password, backup_key)), proxy_id),
            )

        conn.execute("UPDATE profiles SET status='ready' WHERE status='running'")
        conn.execute("UPDATE profiles SET last_session_id=NULL")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone():
            conn.execute(
                "UPDATE sessions SET state='stopped', pid=NULL, ended_at=COALESCE(ended_at, ?) "
                "WHERE state IN ('starting', 'running', 'stopping')",
                (datetime.now(timezone.utc).isoformat(),),
            )
    return restorable


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
        try:
            _safe_extract(zf, workdir)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
            raise ValueError(f"备份包无法解压：{exc}") from exc
        incoming = workdir / DB_NAME

        backup_key = None
        backup_key_path = workdir / "secrets" / "proxy.key"
        if backup_key_path.is_file():
            backup_key = backup_key_path.read_bytes().strip()
        try:
            restorable = _prepare_incoming(incoming, backup_key)
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
                if not profile_dir.is_dir() or profile_dir.name not in restorable:
                    continue
                engine_name = restorable[profile_dir.name]
                dest = config.profile_directory(profile_dir.name)
                dest.mkdir(mode=0o700, parents=True, exist_ok=True)
                if engine_name == "camoufox":
                    if (profile_dir / "cookies.sqlite").is_file() and not (
                        profile_dir / SESSION_FILENAME
                    ).exists():
                        (dest / SESSION_FILENAME).unlink(missing_ok=True)
                    for item in profile_dir.rglob("*"):
                        if item.is_file():
                            out = dest / item.relative_to(profile_dir)
                            out.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(item, out)
                    restored_profiles += 1
                elif engine_name == "chromium":
                    for sqlite_rel in (
                        "Default/Network/Cookies",
                        "Default/Cookies",
                        "Default/LocalStorage",
                        "Default/SessionStorage",
                    ):
                        if (profile_dir / sqlite_rel).is_file():
                            for suffix in ("-wal", "-shm", "-journal"):
                                Path(str(dest / sqlite_rel) + suffix).unlink(missing_ok=True)
                    copied_any = False
                    for item in profile_dir.rglob("*"):
                        if not item.is_file():
                            continue
                        rel_posix = item.relative_to(profile_dir).as_posix()
                        if not _is_allowed_chromium_restore_rel(rel_posix):
                            continue
                        out = dest / item.relative_to(profile_dir)
                        out.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, out)
                        copied_any = True
                    cookie_store.hydrate_portable_chromium_cookies(dest, backup_key=backup_key)
                    if copied_any:
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
