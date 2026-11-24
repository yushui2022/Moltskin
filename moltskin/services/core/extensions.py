"""扩展（Firefox addon）管理。

用户上传 .xpi/.zip，解压到 data/extensions/<id>/（Camoufox 需要的是解压后的
目录，里面必须有 manifest.json），再按环境分配，启动时通过 plan 传给 runner。
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from . import config, db

MAX_SIZE = 40 * 1024 * 1024


def store_dir() -> Path:
    path = config.DATA_DIR / "extensions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def addon_dir(ext_id: str) -> Path:
    return store_dir() / ext_id


def _read_manifest(path: Path) -> dict:
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return {}


def install(ext_id: str, archive: bytes, filename: str) -> dict:
    """解压上传的扩展，返回清单信息。"""
    if len(archive) > MAX_SIZE:
        raise ValueError("扩展文件超过 40MB")
    target = addon_dir(ext_id)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    tmp = target / "_upload.zip"
    tmp.write_bytes(archive)
    try:
        with zipfile.ZipFile(tmp) as zf:
            names = zf.namelist()
            if not names:
                raise ValueError("压缩包是空的")
            # 允许压缩包内套一层目录
            prefix = ""
            if "manifest.json" not in names:
                for name in names:
                    if name.endswith("/manifest.json") and name.count("/") == 1:
                        prefix = name[: -len("manifest.json")]
                        break
            for member in zf.infolist():
                rel = member.filename[len(prefix):] if prefix and member.filename.startswith(prefix) else member.filename
                if not rel or rel.endswith("/"):
                    continue
                if rel.startswith("__MACOSX") or rel.endswith(".DS_Store"):
                    continue
                dest = (target / rel).resolve()
                if not str(dest).startswith(str(target.resolve())):
                    raise ValueError("压缩包路径非法")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
    finally:
        tmp.unlink(missing_ok=True)

    if not (target / "manifest.json").exists():
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError("不是有效的浏览器扩展（缺少 manifest.json）")

    _ensure_addon_id(target, ext_id)

    manifest = _read_manifest(target)
    name = manifest.get("name") or Path(filename).stem or ext_id
    version = manifest.get("version") or ""
    description = manifest.get("description") or ""
    # manifest 里可能是 __MSG_xxx__，简单退化处理
    if isinstance(name, str) and name.startswith("__MSG_"):
        name = Path(filename).stem or ext_id
    if isinstance(description, str) and description.startswith("__MSG_"):
        description = ""
    return {
        "id": ext_id,
        "name": str(name)[:80],
        "version": str(version)[:40],
        "description": str(description)[:200],
        "path": str(target),
        "size": sum(f.stat().st_size for f in target.rglob("*") if f.is_file()),
    }


def _ensure_addon_id(target: Path, ext_id: str) -> None:
    """没有 ID 的扩展在临时安装时会被静默丢弃，这里补一个稳定 ID。"""
    manifest_path = target / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return
    gecko = manifest.setdefault("browser_specific_settings", {}).setdefault("gecko", {})
    if gecko.get("id") or (manifest.get("applications") or {}).get("gecko", {}).get("id"):
        return
    gecko["id"] = f"{ext_id}@moltskin.local"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def builtin_manager_button(open_sidebar: bool = False) -> str | None:
    """把内置的「控制台」扩展铺到 data/builtin/，返回可加载的目录。

    open_sidebar 为真时，浏览器启动后会自动展开左侧栏，让入口按钮可见。
    """
    if not db.get_setting("manager_button", True):
        return None
    source = config.ROOT / "moltskin" / "builtin" / "manager-button"
    if not (source / "manifest.json").exists():
        return None
    target = config.DATA_DIR / "builtin" / "manager-button"
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.is_file():
            shutil.copy2(item, target / item.name)
    (target / "config.json").write_text(
        json.dumps(
            {"url": config.MANAGER_URL, "openSidebarOnStart": bool(open_sidebar)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return str(target)


def remove(ext_id: str) -> None:
    shutil.rmtree(addon_dir(ext_id), ignore_errors=True)


def paths_for(ext_ids: list[str]) -> list[str]:
    """返回可用的扩展目录；已删除的自动跳过。"""
    result = []
    for ext_id in ext_ids or []:
        path = addon_dir(ext_id)
        if (path / "manifest.json").exists():
            result.append(str(path))
    return result


def icon_data_url(ext_id: str) -> str | None:
    """从 manifest 的 icons 里挑一张，返回 data URL 供 UI 显示。"""
    base = addon_dir(ext_id)
    manifest = _read_manifest(base)
    icons = manifest.get("icons") or {}
    candidates = []
    if isinstance(icons, dict):
        candidates = [v for _, v in sorted(icons.items(), key=lambda kv: str(kv[0]))]
    elif isinstance(icons, str):
        candidates = [icons]
    candidates.append(manifest.get("icon"))
    for rel in candidates:
        if not isinstance(rel, str):
            continue
        path = (base / rel.lstrip("/")).resolve()
        if not str(path).startswith(str(base.resolve())) or not path.exists():
            continue
        suffix = path.suffix.lower()
        mime = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(suffix)
        if not mime:
            continue
        import base64

        return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
    return None


def size_of(ext_id: str) -> int:
    base = addon_dir(ext_id)
    if not base.exists():
        return 0
    return sum(f.stat().st_size for f in base.rglob("*") if f.is_file())


def cleanup_orphans(valid_ids: set[str]) -> None:
    for path in store_dir().iterdir():
        if path.is_dir() and path.name not in valid_ids:
            shutil.rmtree(path, ignore_errors=True)
