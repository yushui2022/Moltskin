"""浏览器扩展管理（支持 Chromium .crx/.zip MV2/MV3 与 Camoufox .xpi/.zip）。

用户上传 .crx / .zip / .xpi，安全校验并剥离 CRX2/CRX3 二进制头后解压到
data/extensions/<id>/（内部必须包含 manifest.json），补齐稳定扩展公钥/ID，
再按环境分配并在启动时挂载到 Chromium（--load-extension）或 Camoufox。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import shutil
import struct
import zipfile
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from . import config, db

MAX_SIZE = 40 * 1024 * 1024
MAX_EXTRACTED_SIZE = 200 * 1024 * 1024
MAX_MEMBERS = 20_000

_P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_MSG_RE = re.compile(r"^__MSG_([A-Za-z0-9_@]+)__$")


def store_dir() -> Path:
    path = config.DATA_DIR / "extensions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def addon_dir(ext_id: str) -> Path:
    return store_dir() / ext_id


def _read_manifest(path: Path) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < len(buf):
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("CRX 头部 protobuf 字段异常")
    raise ValueError("CRX 头部不完整")


def _iter_protobuf_fields(buf: bytes):
    pos = 0
    end = len(buf)
    while pos < end:
        tag, pos = _read_varint(buf, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:
            val, pos = _read_varint(buf, pos)
            yield field_num, wire_type, val
        elif wire_type == 1:
            if pos + 8 > end:
                raise ValueError("CRX 头部不完整")
            yield field_num, wire_type, buf[pos : pos + 8]
            pos += 8
        elif wire_type == 2:
            length, pos = _read_varint(buf, pos)
            if pos + length > end:
                raise ValueError("CRX 头部长度越界")
            yield field_num, wire_type, buf[pos : pos + length]
            pos += length
        elif wire_type == 5:
            if pos + 4 > end:
                raise ValueError("CRX 头部不完整")
            yield field_num, wire_type, buf[pos : pos + 4]
            pos += 4
        else:
            raise ValueError("不支持的 CRX 头部编码")


def _extract_crx3_pubkey(header_bytes: bytes) -> bytes | None:
    """Extract the first RSA or ECDSA SubjectPublicKeyInfo from CrxFileHeader."""
    rsa_keys: list[bytes] = []
    ecdsa_keys: list[bytes] = []
    for field_num, wire_type, payload in _iter_protobuf_fields(header_bytes):
        if wire_type != 2 or field_num not in (2, 3):
            continue
        for sub_num, sub_wire, sub_val in _iter_protobuf_fields(payload):
            if sub_num == 1 and sub_wire == 2 and sub_val:
                if field_num == 2:
                    rsa_keys.append(sub_val)
                else:
                    ecdsa_keys.append(sub_val)
    if rsa_keys:
        return rsa_keys[0]
    if ecdsa_keys:
        return ecdsa_keys[0]
    return None


def _strip_crx_header(archive: bytes) -> tuple[bytes, bytes | None]:
    """If archive is CRX2 or CRX3, return (zip_payload, spki_der_pubkey)."""
    if not archive.startswith(b"Cr24"):
        return archive, None
    if len(archive) < 12:
        raise ValueError("CRX 扩展文件损坏（头部过短）")
    version = struct.unpack("<I", archive[4:8])[0]
    if version == 2:
        if len(archive) < 16:
            raise ValueError("CRX2 扩展文件损坏")
        pubkey_len, sig_len = struct.unpack("<II", archive[8:16])
        zip_offset = 16 + pubkey_len + sig_len
        if zip_offset > len(archive):
            raise ValueError("CRX2 扩展头部长度越界")
        pubkey = archive[16 : 16 + pubkey_len] if pubkey_len else None
        return archive[zip_offset:], pubkey
    if version == 3:
        header_len = struct.unpack("<I", archive[8:12])[0]
        zip_offset = 12 + header_len
        if zip_offset > len(archive):
            raise ValueError("CRX3 扩展头部长度越界")
        pubkey = _extract_crx3_pubkey(archive[12:zip_offset])
        return archive[zip_offset:], pubkey
    raise ValueError(f"不支持的 CRX 扩展格式版本（v{version}）")


def _deterministic_spki_der(ext_id: str) -> bytes:
    digest = hashlib.sha256(f"moltskin-chrome-ext:{ext_id}".encode("utf-8")).digest()
    scalar = (int.from_bytes(digest, "big") % (_P256_ORDER - 1)) + 1
    private_key = ec.derive_private_key(scalar, ec.SECP256R1())
    return private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def chrome_id_from_pubkey_bytes(pubkey_der: bytes) -> str:
    digest = hashlib.sha256(pubkey_der).digest()[:16]
    return "".join(chr(ord("a") + nibble) for byte in digest for nibble in (byte >> 4, byte & 0x0F))


def chrome_id(ext_id: str) -> str | None:
    manifest = _read_manifest(addon_dir(ext_id))
    key_b64 = manifest.get("key")
    if isinstance(key_b64, str) and key_b64.strip():
        try:
            raw = base64.b64decode(key_b64.strip())
            if raw:
                return chrome_id_from_pubkey_bytes(raw)
        except Exception:  # noqa: BLE001
            return None
    return None


def _member_parts(name: str) -> list[str]:
    """Return safe relative path parts, or reject the member."""
    if not name or "\x00" in name or "\\" in name:
        raise ValueError("压缩包路径非法")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":" and name[0].isalpha()):
        raise ValueError("压缩包路径非法")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("压缩包路径非法")
    return parts


def _extract(zf: zipfile.ZipFile, target: Path, prefix: str) -> None:
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ValueError("扩展压缩包文件条目过多")
    if sum(member.file_size for member in infos) > MAX_EXTRACTED_SIZE:
        raise ValueError("扩展解压后超过大小限制")

    root = target.resolve()
    written = 0
    for member in infos:
        # Reject UNIX symlink entries inside archives.
        if (member.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError("压缩包路径非法")
        if member.is_dir():
            continue
        rel = member.filename[len(prefix):] if prefix and member.filename.startswith(prefix) else member.filename
        if rel.startswith("__MACOSX") or rel.endswith(".DS_Store"):
            continue
        parts = _member_parts(rel)
        dest = target.joinpath(*parts)
        resolved = dest.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("压缩包路径非法")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(resolved, "wb") as out:
            while chunk := src.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_EXTRACTED_SIZE:
                    raise ValueError("扩展解压后超过大小限制")
                out.write(chunk)


def _resolve_i18n(base: Path, manifest: dict[str, Any], value: Any) -> str:
    if not isinstance(value, str):
        return ""
    match = _MSG_RE.match(value.strip())
    if not match:
        return value
    msg_key = match.group(1)
    locales: list[str] = []
    default_locale = manifest.get("default_locale")
    if isinstance(default_locale, str) and default_locale.strip():
        locales.append(default_locale.strip())
    for fallback in ("zh_CN", "zh", "en_US", "en"):
        if fallback not in locales:
            locales.append(fallback)
    root = base.resolve()
    for loc in locales:
        if "/" in loc or "\\" in loc or ".." in loc:
            continue
        msg_file = (base / "_locales" / loc / "messages.json").resolve()
        if not msg_file.is_relative_to(root) or not msg_file.is_file() or msg_file.is_symlink():
            continue
        try:
            messages = json.loads(msg_file.read_text(encoding="utf-8-sig", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(messages, dict):
            continue
        entry = messages.get(msg_key)
        if entry is None:
            lower = msg_key.lower()
            entry = next((v for k, v in messages.items() if k.lower() == lower), None)
        if isinstance(entry, dict) and isinstance(entry.get("message"), str) and entry["message"].strip():
            return entry["message"].strip()
    return ""


def _ensure_extension_ids(
    target: Path, ext_id: str, crx_pubkey: bytes | None, is_crx: bool
) -> None:
    """补齐稳定扩展标识：
    - Chromium 通过 manifest.key 固定 32 位扩展 ID，保证重装或跨机迁移后
      Local Extension Settings / IndexedDB 依然落回同一扩展 ID。
    - Camoufox 通过 browser_specific_settings.gecko.id 固定 Firefox 临时扩展 ID。
    """
    manifest_path = target / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(manifest, dict):
        return

    changed = False
    existing_key = manifest.get("key")
    if not isinstance(existing_key, str) or not existing_key.strip():
        pubkey_der = crx_pubkey or _deterministic_spki_der(ext_id)
        manifest["key"] = base64.b64encode(pubkey_der).decode("ascii")
        changed = True

    if not is_crx:
        gecko = manifest.setdefault("browser_specific_settings", {}).setdefault("gecko", {})
        if isinstance(gecko, dict) and not (
            gecko.get("id") or (manifest.get("applications") or {}).get("gecko", {}).get("id")
        ):
            gecko["id"] = f"{ext_id}@moltskin.local"
            changed = True

    if changed:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def supported_engines(manifest: dict[str, Any], filename: str = "") -> list[str]:
    lower_name = (filename or "").lower()
    if lower_name.endswith(".crx"):
        return ["chromium"]
    mv = manifest.get("manifest_version")
    bg = manifest.get("background")
    has_gecko = bool(
        (manifest.get("browser_specific_settings") or {}).get("gecko")
        or (manifest.get("applications") or {}).get("gecko")
    )
    if mv == 3 and isinstance(bg, dict) and "service_worker" in bg and "scripts" not in bg and not has_gecko:
        return ["chromium"]
    return ["chromium", "camoufox"]


def metadata(ext_id: str, filename: str = "") -> dict[str, Any]:
    manifest = _read_manifest(addon_dir(ext_id))
    mv = manifest.get("manifest_version")
    manifest_version = mv if isinstance(mv, int) else 2
    return {
        "manifest_version": manifest_version,
        "chrome_id": chrome_id(ext_id),
        "engines": supported_engines(manifest, filename),
    }


def install(ext_id: str, archive: bytes, filename: str) -> dict[str, Any]:
    """解压上传的 .crx / .zip / .xpi 扩展，返回清单信息。"""
    if len(archive) > MAX_SIZE:
        raise ValueError("扩展文件超过 40MB")
    zip_bytes, crx_pubkey = _strip_crx_header(archive)
    is_crx = archive.startswith(b"Cr24") or (filename or "").lower().endswith(".crx")

    target = addon_dir(ext_id)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    try:
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as exc:
            raise ValueError("不是有效的扩展压缩包（支持 .crx / .zip / .xpi）") from exc
        with zf:
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
            _extract(zf, target, prefix)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise

    if not (target / "manifest.json").exists():
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError("不是有效的浏览器扩展（缺少 manifest.json）")

    _ensure_extension_ids(target, ext_id, crx_pubkey, is_crx)

    manifest = _read_manifest(target)
    if not manifest:
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError("扩展 manifest.json 格式无效")

    raw_name = _resolve_i18n(target, manifest, manifest.get("name"))
    raw_desc = _resolve_i18n(target, manifest, manifest.get("description"))
    name = raw_name or Path(filename).stem or ext_id
    version = manifest.get("version") or ""
    description = raw_desc or ""
    meta = metadata(ext_id, filename)
    return {
        "id": ext_id,
        "name": str(name)[:80],
        "version": str(version)[:40],
        "description": str(description)[:200],
        "path": str(target),
        "size": sum(f.stat().st_size for f in target.rglob("*") if f.is_file()),
        **meta,
    }


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


def paths_for(ext_ids: list[str], engine: str | None = None) -> list[str]:
    """返回可用的扩展目录；已删除或不兼容目标内核的自动跳过。"""
    result = []
    for ext_id in ext_ids or []:
        path = addon_dir(ext_id)
        manifest = _read_manifest(path)
        if not manifest:
            continue
        if engine == "camoufox":
            bg = manifest.get("background")
            has_gecko = bool(
                (manifest.get("browser_specific_settings") or {}).get("gecko")
                or (manifest.get("applications") or {}).get("gecko")
            )
            if (
                manifest.get("manifest_version") == 3
                and isinstance(bg, dict)
                and "service_worker" in bg
                and "scripts" not in bg
                and not has_gecko
            ):
                continue
        result.append(str(path.resolve()))
    return result


def icon_data_url(ext_id: str) -> str | None:
    """从 manifest 的 icons / action 里挑一张，返回 data URL 供 UI 显示。"""
    base = addon_dir(ext_id)
    manifest = _read_manifest(base)
    icons = manifest.get("icons") or {}
    candidates: list[Any] = []
    if isinstance(icons, dict):
        candidates = [v for _, v in sorted(icons.items(), key=lambda kv: str(kv[0]), reverse=True)]
    elif isinstance(icons, str):
        candidates = [icons]
    for action_key in ("action", "browser_action"):
        action_icons = (manifest.get(action_key) or {}).get("default_icon") if isinstance(manifest.get(action_key), dict) else None
        if isinstance(action_icons, dict):
            candidates.extend(v for _, v in sorted(action_icons.items(), key=lambda kv: str(kv[0]), reverse=True))
        elif isinstance(action_icons, str):
            candidates.append(action_icons)
    candidates.append(manifest.get("icon"))
    root = base.resolve()
    for rel in candidates:
        if not isinstance(rel, str) or not rel:
            continue
        candidate = base / rel.lstrip("/")
        if candidate.is_symlink():
            continue
        path = candidate.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            continue
        suffix = path.suffix.lower()
        mime = {
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix)
        if not mime:
            continue
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
