"""环境之间的登录态同步。

Cookie 直接使用 cookies.sqlite；localStorage / IndexedDB / CacheStorage 在
Firefox 的 ``storage/`` 目录里。两个环境都必须是关闭状态，否则数据库可能
正在被写入。同步是"覆盖"语义：目标环境会变成源环境的样子。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Iterable

from . import cookies as cookie_store

STORAGE_DIRS = ("storage",)
STORAGE_FILES = ("webappsstore.sqlite", "permissions.sqlite")


def _copy_file(src: Path, dst: Path) -> int:
    """拷贝单个 SQLite 文件，顺带清掉目标残留的 -wal/-shm。"""
    if not src.exists():
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(dst) + suffix).unlink(missing_ok=True)
    shutil.copy2(src, dst)
    return dst.stat().st_size


def _copy_tree(src: Path, dst: Path) -> int:
    if not src.exists():
        return 0
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, symlinks=False, ignore_dangling_symlinks=True)
    return sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())


def sync(
    target_dir: str | Path,
    source_dir: str | Path,
    parts: Iterable[str] = ("cookies", "storage"),
) -> dict[str, Any]:
    target = Path(target_dir)
    source = Path(source_dir)
    parts = set(parts) or {"cookies"}
    result: dict[str, Any] = {"parts": sorted(parts), "bytes": 0, "cookies": 0, "storage": False}

    if "cookies" in parts:
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

    if "storage" in parts:
        total = 0
        for name in STORAGE_DIRS:
            total += _copy_tree(source / name, target / name)
        for name in STORAGE_FILES:
            total += _copy_file(source / name, target / name)
        result["storage"] = total > 0
        result["bytes"] += total

    return result


def size_of(user_data_dir: str | Path) -> dict[str, int]:
    base = Path(user_data_dir)
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
