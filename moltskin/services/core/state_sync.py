"""环境之间的登录态同步（支持 Camoufox 与 Chromium）。

Camoufox 使用 ``cookies.sqlite`` 与 ``storage/``；
Chromium 使用 ``Default/Network/Cookies``（及便携快照）与 ``Default/LocalStorage``、
``Default/IndexedDB``、``Default/WebStorage``、``Default/Local Extension Settings`` 等目录。
两个环境都必须是关闭状态，否则数据库可能正在被写入。同步是"覆盖"语义。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import shutil
from typing import Any

from . import cookies as cookie_store

STORAGE_DIRS = ("storage",)
STORAGE_FILES = ("webappsstore.sqlite", "permissions.sqlite")

CHROMIUM_STORAGE_FILES = (
    "LocalStorage",
    "LocalStorage-journal",
    "SessionStorage",
    "SessionStorage-journal",
)
CHROMIUM_STORAGE_DIRS = (
    "Local Storage",
    "Session Storage",
    "IndexedDB",
    "WebStorage",
    "Local Extension Settings",
    "Sync Extension Settings",
    "Managed Extension Settings",
    "Extension State",
)
CHROMIUM_EXCLUDED_SUBDIRS = frozenset(
    {"Cache", "Code Cache", "CacheStorage", "GPUCache", "ScriptCache", "blob_storage"}
)


def _copy_file(src: Path, dst: Path) -> int:
    """拷贝单个 SQLite 文件，顺带清掉目标残留的 -wal/-shm/-journal。"""
    if not src.is_file() or src.is_symlink():
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(dst) + suffix).unlink(missing_ok=True)
    shutil.copy2(src, dst)
    return dst.stat().st_size


def _copy_tree(src: Path, dst: Path, *, excluded_names: frozenset[str] = frozenset()) -> int:
    if not src.is_dir() or src.is_symlink():
        return 0
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    total = 0
    for item in src.rglob("*"):
        if item.is_symlink() or not item.is_file():
            continue
        rel = item.relative_to(src)
        if any(part in excluded_names for part in rel.parts) or rel.name == "LOCK":
            continue
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, out)
        total += out.stat().st_size
    return total


def sync(
    target_dir: str | Path,
    source_dir: str | Path,
    parts: Iterable[str] = ("cookies", "storage"),
    *,
    engine: str = "camoufox",
) -> dict[str, Any]:
    target = Path(target_dir)
    source = Path(source_dir)
    wanted = set(parts) or {"cookies"}
    result: dict[str, Any] = {"parts": sorted(wanted), "bytes": 0, "cookies": 0, "storage": False}

    if engine == "chromium":
        if "cookies" in wanted:
            items = cookie_store.read_chromium_cookies(source)
            copied_bytes = 0
            for rel in (
                Path("Default") / "Network" / "Cookies",
                Path("Default") / "Cookies",
                Path(cookie_store.CHROMIUM_PORTABLE_FILENAME),
            ):
                src_file = source / rel
                dst_file = target / rel
                if src_file.is_file() and not src_file.is_symlink():
                    copied_bytes += _copy_file(src_file, dst_file)
                else:
                    dst_file.unlink(missing_ok=True)
            if items:
                cookie_store.write_chromium_cookies(target, items, mode="merge")
                result["cookies"] = len(items)
            elif copied_bytes > 0:
                result["cookies"] = 1
            result["bytes"] += copied_bytes

        if "storage" in wanted:
            total = 0
            src_default = source / "Default"
            dst_default = target / "Default"
            for fname in ("LocalStorage", "SessionStorage"):
                src_f = src_default / fname
                dst_f = dst_default / fname
                if src_f.is_file() and not src_f.is_symlink():
                    total += _copy_file(src_f, dst_f)
                else:
                    for suffix in ("", "-wal", "-shm", "-journal"):
                        Path(str(dst_f) + suffix).unlink(missing_ok=True)
            for name in CHROMIUM_STORAGE_DIRS:
                src_sub = src_default / name
                dst_sub = dst_default / name
                if src_sub.is_dir():
                    total += _copy_tree(src_sub, dst_sub, excluded_names=CHROMIUM_EXCLUDED_SUBDIRS)
                elif dst_sub.exists():
                    shutil.rmtree(dst_sub, ignore_errors=True)
            result["storage"] = total > 0
            result["bytes"] += total
        return result

    if "cookies" in wanted:
        items = []
        try:
            items = cookie_store.read_cookies(source)
        except FileNotFoundError:
            items = []
        if items:
            written = cookie_store.write_cookies(target, items, mode="replace")
            result["cookies"] = written["applied"]
        else:
            result["cookies"] = 0

    if "storage" in wanted:
        total = 0
        for name in STORAGE_DIRS:
            total += _copy_tree(source / name, target / name)
        for name in STORAGE_FILES:
            total += _copy_file(source / name, target / name)
        result["storage"] = total > 0
        result["bytes"] += total

    return result


def size_of(user_data_dir: str | Path, *, engine: str = "camoufox") -> dict[str, int]:
    base = Path(user_data_dir)
    if engine == "chromium":
        cookies_bytes = 0
        for rel in (
            Path("Default") / "Network" / "Cookies",
            Path("Default") / "Cookies",
            Path(cookie_store.CHROMIUM_PORTABLE_FILENAME),
        ):
            p = base / rel
            if p.is_file() and not p.is_symlink():
                try:
                    cookies_bytes += p.stat().st_size
                except OSError:
                    pass
        storage_bytes = 0
        default_dir = base / "Default"
        if default_dir.is_dir():
            for fname in CHROMIUM_STORAGE_FILES:
                fp = default_dir / fname
                if fp.is_file() and not fp.is_symlink():
                    try:
                        storage_bytes += fp.stat().st_size
                    except OSError:
                        pass
            for name in CHROMIUM_STORAGE_DIRS:
                sub = default_dir / name
                if sub.is_dir() and not sub.is_symlink():
                    for f in sub.rglob("*"):
                        if f.is_file() and not f.is_symlink():
                            if any(part in CHROMIUM_EXCLUDED_SUBDIRS for part in f.relative_to(sub).parts):
                                continue
                            try:
                                storage_bytes += f.stat().st_size
                            except OSError:
                                pass
        return {"cookies_bytes": cookies_bytes, "storage_bytes": storage_bytes}

    cookies = 0
    try:
        cookies = cookie_store.cookies_path(base).stat().st_size if cookie_store.cookies_path(base).exists() else 0
    except OSError:
        cookies = 0
    storage = 0
    storage_path = base / "storage"
    if storage_path.exists():
        storage = sum(f.stat().st_size for f in storage_path.rglob("*") if f.is_file())
    return {"cookies_bytes": cookies, "storage_bytes": storage}
