"""环境 Cookie 的读写（支持 Camoufox 与 Chromium）。

Camoufox 持久 Cookie 写入 Firefox 的 ``cookies.sqlite``；会话 Cookie 在下次启动时交给浏览器。
Chromium 持久 Cookie 写入 ``Default/Network/Cookies``（v24 明文迁移兼容格式）并同步加密保存
``moltskin-portable-cookies.enc`` 便携快照，实现跨机器备份恢复与环境间复制。
写入前必须确保环境已关闭。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from ipaddress import IPv6Address
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

from . import secret_store

SESSION_FILENAME = "moltskin-session-cookies.sqlite"
CHROMIUM_PORTABLE_FILENAME = "moltskin-portable-cookies.enc"
CHROMIUM_EPOCH_DELTA = 11644473600

CHROMIUM_SAME_SITE_TO_INT = {
    "unspecified": -1,
    "none": 0,
    "no_restriction": 0,
    "lax": 1,
    "strict": 2,
}
CHROMIUM_INT_TO_SAME_SITE = {
    -1: "unspecified",
    0: "no_restriction",
    1: "lax",
    2: "strict",
}

SAME_SITE_TO_INT = {
    "unspecified": 256,
    "none": 0,
    "no_restriction": 0,
    "lax": 1,
    "strict": 2,
}
INT_TO_SAME_SITE = {0: "no_restriction", 1: "lax", 2: "strict", 256: "unspecified"}


class CookieImportError(ValueError):
    """An import error safe to show without disclosing cookie contents."""


class CookieScopeExportError(ValueError):
    """The requested format cannot represent a cookie's scope."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS moz_cookies (
  id INTEGER PRIMARY KEY,
  originAttributes TEXT NOT NULL DEFAULT '',
  name TEXT, value TEXT, host TEXT, path TEXT,
  expiry INTEGER, lastAccessed INTEGER, creationTime INTEGER,
  isSecure INTEGER, isHttpOnly INTEGER, inBrowserElement INTEGER DEFAULT 0,
  sameSite INTEGER DEFAULT 0, schemeMap INTEGER DEFAULT 0,
  isPartitionedAttributeSet INTEGER DEFAULT 0, updateTime INTEGER,
  CONSTRAINT moz_uniqueid UNIQUE (name, host, path, originAttributes)
);
"""


def cookies_path(user_data_dir: str | Path) -> Path:
    return Path(user_data_dir) / "cookies.sqlite"


def _connect(user_data_dir: str | Path, writable: bool = False) -> sqlite3.Connection:
    path = cookies_path(user_data_dir)
    if writable:
        path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(path, timeout=10)
    if not path.exists():
        raise FileNotFoundError(str(path))
    # 优先按普通只读打开，这样能看到 WAL 里刚写入但未 checkpoint 的 Cookie；
    # 打不开（例如被完全锁住）再退回 immutable，至少能导出旧数据。
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        conn.execute("SELECT 1 FROM moz_cookies LIMIT 1")
    except sqlite3.Error:
        conn.close()
        conn = sqlite3.connect(uri + "&immutable=1", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def read_cookies(
    user_data_dir: str | Path, domain: str = "", *, engine: str = "camoufox"
) -> list[dict[str, Any]]:
    if engine == "chromium":
        return read_chromium_cookies(user_data_dir, domain)
    path = cookies_path(user_data_dir)
    if not path.exists():
        return []
    conn = _connect(user_data_dir)
    try:
        session_path = Path(user_data_dir) / SESSION_FILENAME
        has_session_file = session_path.is_file()
        if has_session_file:
            conn.execute(
                "ATTACH DATABASE ? AS session_import", (session_path.resolve().as_uri() + "?mode=ro",)
            )
        conn.execute("BEGIN")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        condition = " WHERE host LIKE ?" if domain else ""
        params = (f"%{domain}",) if domain else ()
        rows = conn.execute("SELECT * FROM moz_cookies" + condition, params)
        columns = {column[0] for column in rows.description}
        items = []
        for row in rows:
            expiry = row["expiry"] or 0
            if version >= 16:
                expiry = expiry / 1000
            same_site = INT_TO_SAME_SITE.get(row["sameSite"], "unspecified")
            if version == 0:
                same_site = {0: "unspecified", 3: "no_restriction"}.get(row["sameSite"], same_site)
            item = {
                "name": row["name"],
                "value": row["value"],
                "domain": row["host"],
                "path": row["path"] or "/",
                "expires": expiry if version else (expiry or -1),
                "secure": bool(row["isSecure"]),
                "httpOnly": bool(row["isHttpOnly"]),
                "sameSite": same_site,
            }
            if "originAttributes" in columns and row["originAttributes"]:
                item["originAttributes"] = row["originAttributes"]
            if "isPartitionedAttributeSet" in columns and row["isPartitionedAttributeSet"]:
                item["isPartitionedAttributeSet"] = True
            items.append(item)
        if has_session_file and conn.execute(
            "SELECT 1 FROM session_import.sqlite_master WHERE name='moltskin_pending_session'"
        ).fetchone():
            queued = conn.execute(
                "SELECT cookie FROM session_import.moltskin_pending_session" + condition, params
            )
            items.extend(json.loads(row[0]) for row in queued)
        return sorted(items, key=lambda item: (item["domain"], item["name"], item["path"]))
    finally:
        conn.close()


def _has_pending_table(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='moltskin_pending_session'"
        ).fetchone()
        is not None
    )


def apply_pending_session_cookies(user_data_dir: str | Path, context: Any) -> None:
    """Deliver a session-only import once. No persistent expiry is fabricated."""
    # Firefox locks its own database while running. The import queue lives
    # alongside it and a successful delivery is acknowledged immediately.
    path = Path(user_data_dir) / SESSION_FILENAME
    if not path.is_file():
        return
    conn = sqlite3.connect(path, timeout=10)
    try:
        if not _has_pending_table(conn):
            return
        rows = conn.execute("SELECT rowid, cookie FROM moltskin_pending_session").fetchall()
        if not rows:
            return
        values = []
        for _, encoded in rows:
            item = json.loads(encoded)
            item.pop("expires")
            same_site = item.pop("sameSite")
            if same_site != "unspecified":
                item["sameSite"] = {"no_restriction": "None", "lax": "Lax", "strict": "Strict"}[
                    same_site
                ]
            values.append(item)
        try:
            context.add_cookies(values)
        except Exception:  # noqa: BLE001 - redact Playwright diagnostics
            # Playwright exceptions can contain full Cookie values.
            raise RuntimeError("无法将导入的会话 Cookie 加载到环境中，请检查文件格式。") from None
        conn.executemany("DELETE FROM moltskin_pending_session WHERE rowid=? AND cookie=?", rows)
        conn.commit()
    finally:
        conn.close()


def _now_us() -> int:
    return int(time.time() * 1_000_000)


def write_cookies(
    user_data_dir: str | Path,
    cookies: Iterable[dict[str, Any]],
    mode: str = "merge",
    *,
    engine: str = "camoufox",
) -> dict[str, int | str]:
    """把浏览器格式的 Cookie 写入环境。

    mode="replace" 会先清空；"merge" 则按 (name, host, path) 覆盖同名 Cookie。
    """
    if engine == "chromium":
        return write_chromium_cookies(user_data_dir, cookies, mode=mode)
    items = [_normalize(raw) for raw in cookies]
    conn = _connect(user_data_dir, writable=True)
    inserted = 0
    try:
        # Rollback journaling, rather than WAL, allows SQLite's super-journal
        # to atomically commit changes to both databases on a stopped profile.
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute(
            "ATTACH DATABASE ? AS session_import", (str(Path(user_data_dir) / SESSION_FILENAME),)
        )
        conn.execute("PRAGMA session_import.journal_mode=DELETE")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(SCHEMA)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS session_import.moltskin_pending_session "
            "(host TEXT, path TEXT, name TEXT, cookie TEXT NOT NULL, "
            "PRIMARY KEY (host, path, name))"
        )
        if mode == "replace":
            conn.execute("DELETE FROM moz_cookies")
            conn.execute("DELETE FROM session_import.moltskin_pending_session")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            # Older Moltskin imports used native v17 columns but stored seconds
            # and a different SameSite encoding. Upgrade within the import transaction.
            for name, value, host, path, secure, http_only, same_site in conn.execute(
                "SELECT name,value,host,path,isSecure,isHttpOnly,sameSite FROM moz_cookies "
                "WHERE expiry=0 AND originAttributes='' AND COALESCE(isPartitionedAttributeSet,0)=0"
            ):
                legacy = {
                    "name": name,
                    "value": value,
                    "domain": host,
                    "path": path,
                    "secure": bool(secure),
                    "httpOnly": bool(http_only),
                    "expires": -1,
                    "sameSite": {0: "unspecified", 1: "lax", 2: "strict", 3: "no_restriction"}.get(
                        same_site, "unspecified"
                    ),
                }
                conn.execute(
                    "INSERT OR REPLACE INTO session_import.moltskin_pending_session VALUES (?, ?, ?, ?)",
                    (host, path, name, json.dumps(legacy)),
                )
            conn.execute("DELETE FROM moz_cookies WHERE expiry=0 AND originAttributes='' AND COALESCE(isPartitionedAttributeSet,0)=0")
            conn.execute(
                "UPDATE moz_cookies SET expiry=expiry*1000, "
                "sameSite=CASE sameSite WHEN 3 THEN 0 WHEN 0 THEN 256 ELSE sameSite END"
            )
            conn.execute("PRAGMA user_version=17")
            version = 17
        expiry_unit = 1000 if version >= 16 else 1
        for cookie in items:
            key = (cookie["domain"], cookie["path"], cookie["name"])
            if cookie["expires"] == -1:
                conn.execute(
                    "DELETE FROM moz_cookies WHERE host=? AND path=? AND name=? AND originAttributes=''",
                    key,
                )
                conn.execute(
                    "INSERT OR REPLACE INTO session_import.moltskin_pending_session VALUES (?, ?, ?, ?)",
                    (*key, json.dumps(cookie)),
                )
                inserted += 1
                continue
            conn.execute(
                "DELETE FROM session_import.moltskin_pending_session WHERE host=? AND path=? AND name=?",
                key,
            )
            expiry = round(cookie["expires"] * expiry_unit)
            now_us = _now_us()
            # The cookie key includes originAttributes. Imported, unpartitioned
            # rows must not overwrite a browser-owned partition with the same name.
            record = {
                "name": cookie["name"],
                "value": cookie["value"],
                "host": cookie["domain"],
                "path": cookie["path"],
                "expiry": expiry,
                "lastAccessed": now_us,
                "creationTime": now_us,
                "isSecure": int(cookie["secure"]),
                "isHttpOnly": int(cookie["httpOnly"]),
                "sameSite": SAME_SITE_TO_INT[cookie["sameSite"]],
            }
            if version >= 17:
                record["updateTime"] = now_us
            columns = ", ".join(record)
            placeholders = ", ".join("?" for _ in record)
            updates = ", ".join(
                f"{key}=excluded.{key}"
                for key in record
                if key not in {"name", "host", "path", "creationTime"}
            )
            conn.execute(
                f"INSERT INTO moz_cookies ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(name, host, path, originAttributes) DO UPDATE SET {updates}",
                tuple(record.values()),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return {"applied": inserted, "mode": mode}


def _flag(value: Any) -> bool:
    if isinstance(value, (bool, str, int)):
        value = str(value).lower()
        if value in ("1", "true"):
            return True
        if value in ("0", "false", ""):
            return False
    raise CookieImportError("Cookie 布尔字段格式无效")


def _expiry(value: Any) -> float:
    if value in (None, ""):
        return -1
    if isinstance(value, bool):
        raise CookieImportError("Cookie 到期时间格式无效")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        try:
            date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            result = (date if date.tzinfo else date.replace(tzinfo=timezone.utc)).timestamp()
        except (ValueError, OverflowError, OSError):
            raise CookieImportError("Cookie 到期时间格式无效") from None
    # JSON timestamps use Unix seconds. Keep millisecond precision in SQLite.
    if not math.isfinite(result) or result > 253402300799:
        raise CookieImportError("Cookie 到期时间超出范围")
    return -1 if result == -1 else max(result, 0)


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CookieImportError("每条 Cookie 必须是一个对象")
    fields = {key.lower(): val for key, val in raw.items()}
    if len(fields) != len(raw):
        raise CookieImportError("Cookie 字段名称重复")
    name = fields.get("name")
    domain = fields.get("domain") or fields.get("host")
    if (
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(domain, str)
        or not domain.strip()
    ):
        raise CookieImportError("Cookie 缺少有效的名称或域名")
    path = fields.get("path", "/")
    if path in ("", None):
        path = "/"
    if not isinstance(path, str) or not path.startswith("/"):
        raise CookieImportError("Cookie 路径格式无效")
    value = fields.get("value")
    if not isinstance(value, str):
        raise CookieImportError("Cookie 缺少有效的值")
    if any(
        ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF
        for field in (name, domain, path, value)
        for c in field
    ):
        raise CookieImportError("Cookie 字段包含无效字符")
    name = name.strip()
    domain = domain.strip()
    if any(c in "=;" for c in name):
        raise CookieImportError("Cookie 名称格式无效")
    host = domain.removeprefix(".")
    if ":" in host:
        try:
            if host.startswith("[") != host.endswith("]"):
                raise ValueError
            IPv6Address(host.removeprefix("[").removesuffix("]"))
        except ValueError:
            raise CookieImportError("Cookie 域名格式无效") from None
    elif not all(label and all(c.isalnum() or c in "-_" for c in label) for label in host.removesuffix(".").split(".")):
        raise CookieImportError("Cookie 域名格式无效")
    if "hostonly" in fields:
        domain = host if _flag(fields["hostonly"]) else "." + host
    if (
        fields.get("partitionkey") not in (None, "")
        or _flag(fields.get("firstpartyonly", False))
        or fields.get("firstpartydomain")
        or fields.get("originattributes")
        or _flag(fields.get("partitioned", False))
        or _flag(fields.get("ispartitioned", False))
        or _flag(fields.get("ispartitionedattributeset", False))
    ):
        raise CookieImportError("此批 Cookie 含有分区、容器或第一方限制，暂不支持导入或复制")
    has_expiry = _flag(fields.get("hasexpires", True))
    persistent = _flag(fields.get("persistent", True))
    session = _flag(fields.get("session", False)) or not has_expiry or not persistent
    expires = (
        -1
        if session
        else _expiry(fields.get("expires", fields.get("expirationdate", fields.get("expiry", -1))))
    )
    same_site = str(fields.get("samesite", "unspecified")).lower()
    same_site = {
        "-1": "unspecified",
        "0": "no_restriction",
        "1": "lax",
        "2": "strict",
        "none": "no_restriction",
        "": "unspecified",
    }.get(same_site, same_site)
    if same_site not in SAME_SITE_TO_INT:
        raise CookieImportError("Cookie SameSite 字段无效")
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path,
        "expires": expires,
        "secure": _flag(fields.get("secure", False)),
        "httpOnly": _flag(fields.get("httponly", False)),
        "sameSite": same_site,
    }


def to_netscape(cookies: Iterable[dict[str, Any]]) -> str:
    lines = ["# Netscape HTTP Cookie File", "# Exported by Moltskin", ""]
    for c in cookies:
        if c.get("originAttributes") or c.get("isPartitionedAttributeSet"):
            raise CookieScopeExportError("Netscape 格式无法保存 Cookie 的分区或容器信息，请导出 JSON。")
        domain = c["domain"]
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if c["secure"] else "FALSE"
        expires = int(c["expires"]) if c.get("expires", -1) > 0 else 0
        if c["httpOnly"]:
            domain = "#HttpOnly_" + domain
        lines.append(
            "\t".join([domain, include_sub, c["path"], secure, str(expires), c["name"], c["value"]])
        )
    return "\n".join(lines) + "\n"


def parse_netscape(text: str) -> list[dict[str, Any]]:
    cookies = []
    for index, line in enumerate(text.splitlines(), start=1):
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line.removeprefix("#HttpOnly_")
        elif not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t", 6)
        if len(parts) < 7:
            parts = line.split(maxsplit=6)
        if len(parts) != 7:
            raise CookieImportError(f"第 {index} 行的 Netscape Cookie 格式无效")
        domain, include_subdomains, path, secure, expires, name, value = parts
        if include_subdomains.upper() not in ("TRUE", "FALSE") or secure.upper() not in (
            "TRUE",
            "FALSE",
        ):
            raise CookieImportError(f"第 {index} 行的 Netscape Cookie 标志无效")
        if include_subdomains.upper() == "TRUE" and not domain.startswith("."):
            domain = "." + domain
        elif include_subdomains.upper() == "FALSE":
            domain = domain.lstrip(".")
        expires_at = _expiry(expires)
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path or "/",
                "expires": expires_at if expires_at > 0 else -1,
                "secure": secure.upper() == "TRUE",
                "httpOnly": http_only,
                "sameSite": "unspecified",
            }
        )
    if not cookies and not any(
        line.strip() in ("# Netscape HTTP Cookie File", "# HTTP Cookie File") for line in text.splitlines()
    ):
        raise CookieImportError("无法识别此 Cookie 导出文件")
    return cookies


def parse_payload(payload: Any) -> tuple[list[dict[str, Any]], str]:
    """Accept native JSON, storage_state, Netscape and Hubstudio per-profile exports."""
    source = "json"
    # Hubstudio returns a JSON-encoded array in the data field of its API envelope.
    for _ in range(6):
        if isinstance(payload, list):
            if any(isinstance(row, dict) and "Name" in row for row in payload):
                source = "hubstudio"
            items = []
            for index, row in enumerate(payload, start=1):
                try:
                    items.append(_normalize(row))
                except CookieImportError as exc:
                    raise CookieImportError(f"第 {index} 条 Cookie：{exc}") from None
            return items, source
        if isinstance(payload, dict):
            if str(payload.get("code", 0)) != "0":
                raise CookieImportError("导出接口返回错误，无法导入 Cookie")
            if "cookies" in payload:
                source = "storage_state"
                payload = payload["cookies"]
            elif "data" in payload:
                payload = payload["data"]
            else:
                break
        elif isinstance(payload, str):
            text = payload.lstrip("\ufeff")
            if text.lstrip().startswith(("[", "{", '"')):
                try:
                    payload = json.loads(text)
                except (ValueError, RecursionError):
                    raise CookieImportError("Cookie JSON 格式无效") from None
            else:
                return [_normalize(row) for row in parse_netscape(text)], "netscape"
        else:
            break
    raise CookieImportError("无法识别此 Cookie 导出文件")


CHROMIUM_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
  key LONGVARCHAR NOT NULL UNIQUE PRIMARY KEY,
  value LONGVARCHAR
);
CREATE TABLE IF NOT EXISTS cookies(
  creation_utc INTEGER NOT NULL,
  host_key TEXT NOT NULL,
  top_frame_site_key TEXT NOT NULL,
  name TEXT NOT NULL,
  value TEXT NOT NULL,
  encrypted_value BLOB NOT NULL,
  path TEXT NOT NULL,
  expires_utc INTEGER NOT NULL,
  is_secure INTEGER NOT NULL,
  is_httponly INTEGER NOT NULL,
  last_access_utc INTEGER NOT NULL,
  has_expires INTEGER NOT NULL,
  is_persistent INTEGER NOT NULL,
  priority INTEGER NOT NULL,
  samesite INTEGER NOT NULL,
  source_scheme INTEGER NOT NULL,
  source_port INTEGER NOT NULL,
  last_update_utc INTEGER NOT NULL,
  source_type INTEGER NOT NULL,
  has_cross_site_ancestor INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS cookies_unique_index
ON cookies(host_key, top_frame_site_key, has_cross_site_ancestor, name, path, source_scheme, source_port);
"""


def chromium_cookies_path(user_data_dir: str | Path) -> Path:
    base = Path(user_data_dir)
    network_path = base / "Default" / "Network" / "Cookies"
    legacy_path = base / "Default" / "Cookies"
    if network_path.exists() or not legacy_path.exists():
        return network_path
    return legacy_path


def _chromium_now_utc() -> int:
    return round((time.time() + CHROMIUM_EPOCH_DELTA) * 1_000_000)


def _unix_to_chromium_utc(unix_sec: float) -> int:
    if unix_sec <= 0:
        return 0
    return round((unix_sec + CHROMIUM_EPOCH_DELTA) * 1_000_000)


def _chromium_utc_to_unix(chrome_us: int) -> float:
    if not chrome_us or chrome_us <= 0:
        return -1
    return max(0.0, chrome_us / 1_000_000 - CHROMIUM_EPOCH_DELTA)


def save_portable_chromium_cookies(
    user_data_dir: str | Path,
    cookies: Iterable[dict[str, Any]],
    *,
    key: bytes | None = None,
) -> int:
    base = Path(user_data_dir)
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    normalized: list[dict[str, Any]] = []
    for raw in cookies:
        try:
            normalized.append(_normalize(raw))
        except CookieImportError:
            continue
    target = base / CHROMIUM_PORTABLE_FILENAME
    if not normalized:
        target.unlink(missing_ok=True)
        return 0
    encoded = secret_store.encrypt(json.dumps(normalized, ensure_ascii=False), key=key)
    assert encoded is not None
    target.write_text(encoded, encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return len(normalized)


def _read_portable_chromium_cookies(
    user_data_dir: str | Path, *, key: bytes | None = None
) -> list[dict[str, Any]]:
    path = Path(user_data_dir) / CHROMIUM_PORTABLE_FILENAME
    if not path.is_file() or path.is_symlink():
        return []
    try:
        raw_text = secret_store.decrypt(path.read_text(encoding="utf-8").strip(), key=key)
        if not raw_text:
            return []
        parsed = json.loads(raw_text)
        if not isinstance(parsed, list):
            return []
        return [_normalize(item) for item in parsed if isinstance(item, dict)]
    except (OSError, ValueError, json.JSONDecodeError, CookieImportError):
        return []


def read_chromium_cookies(user_data_dir: str | Path, domain: str = "") -> list[dict[str, Any]]:
    base = Path(user_data_dir)
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in _read_portable_chromium_cookies(base):
        merged[(item["domain"], item["path"], item["name"])] = item

    for candidate in (base / "Default" / "Network" / "Cookies", base / "Default" / "Cookies"):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        uri = candidate.resolve().as_uri() + "?mode=ro"
        conn = None
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT host_key, name, value, encrypted_value, path, expires_utc, "
                "is_secure, is_httponly, has_expires, is_persistent, samesite "
                "FROM cookies WHERE top_frame_site_key=''"
            ).fetchall()
            for row in rows:
                val = row["value"] or ""
                enc = row["encrypted_value"] or b""
                # Only overwrite/insert from SQLite when plaintext is available.
                if enc and not val:
                    continue
                is_session = not row["has_expires"] or not row["is_persistent"]
                expires = -1 if is_session else _chromium_utc_to_unix(int(row["expires_utc"] or 0))
                key = (row["host_key"], row["path"] or "/", row["name"])
                merged[key] = {
                    "name": row["name"],
                    "value": val,
                    "domain": row["host_key"],
                    "path": row["path"] or "/",
                    "expires": expires,
                    "secure": bool(row["is_secure"]),
                    "httpOnly": bool(row["is_httponly"]),
                    "sameSite": CHROMIUM_INT_TO_SAME_SITE.get(row["samesite"], "unspecified"),
                }
        except sqlite3.Error:
            continue
        finally:
            if conn is not None:
                conn.close()

    needle = domain.lower().lstrip(".") if domain else ""
    items = [
        item
        for item in merged.values()
        if not needle or needle in item["domain"].lower()
    ]
    return sorted(items, key=lambda item: (item["domain"], item["name"], item["path"]))


def _write_chromium_sqlite(
    db_path: Path, items: list[dict[str, Any]], *, replace_all: bool
) -> None:
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("BEGIN IMMEDIATE")
        conn.executescript(CHROMIUM_SCHEMA)
        for meta_key, meta_val in (
            ("mmap_status", "-1"),
            ("version", "24"),
            ("last_compatible_version", "24"),
        ):
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
                (meta_key, meta_val),
            )
        if replace_all:
            conn.execute("DELETE FROM cookies")
        now_utc = _chromium_now_utc()
        for cookie in items:
            conn.execute(
                "DELETE FROM cookies WHERE host_key=? AND path=? AND name=? AND top_frame_site_key=''",
                (cookie["domain"], cookie["path"], cookie["name"]),
            )
            is_persistent = 1 if cookie["expires"] != -1 else 0
            expires_utc = _unix_to_chromium_utc(cookie["expires"]) if is_persistent else 0
            source_scheme = 2 if cookie["secure"] else 1
            source_port = 443 if cookie["secure"] else 80
            conn.execute(
                "INSERT INTO cookies ("
                "creation_utc, host_key, top_frame_site_key, name, value, encrypted_value, "
                "path, expires_utc, is_secure, is_httponly, last_access_utc, has_expires, "
                "is_persistent, priority, samesite, source_scheme, source_port, "
                "last_update_utc, source_type, has_cross_site_ancestor"
                ") VALUES (?, ?, '', ?, ?, X'', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 1, 0)",
                (
                    now_utc,
                    cookie["domain"],
                    cookie["name"],
                    cookie["value"],
                    cookie["path"],
                    expires_utc,
                    int(cookie["secure"]),
                    int(cookie["httpOnly"]),
                    now_utc,
                    is_persistent,
                    is_persistent,
                    CHROMIUM_SAME_SITE_TO_INT[cookie["sameSite"]],
                    source_scheme,
                    source_port,
                    now_utc,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def write_chromium_cookies(
    user_data_dir: str | Path,
    cookies: Iterable[dict[str, Any]],
    mode: str = "merge",
) -> dict[str, int | str]:
    base = Path(user_data_dir)
    items = [_normalize(raw) for raw in cookies]
    replace_all = mode == "replace"
    if replace_all:
        legacy = base / "Default" / "Cookies"
        if legacy.exists():
            legacy.unlink(missing_ok=True)
            Path(str(legacy) + "-journal").unlink(missing_ok=True)
    db_path = chromium_cookies_path(base)
    _write_chromium_sqlite(db_path, items, replace_all=replace_all)

    if replace_all:
        portable = items
    else:
        by_key = {(c["domain"], c["path"], c["name"]): c for c in _read_portable_chromium_cookies(base)}
        for c in items:
            by_key[(c["domain"], c["path"], c["name"])] = c
        portable = list(by_key.values())
    save_portable_chromium_cookies(base, portable)
    return {"applied": len(items), "mode": mode}


def hydrate_portable_chromium_cookies(
    user_data_dir: str | Path, *, backup_key: bytes | None = None
) -> int:
    """Re-encrypt portable Chromium cookies after backup restore and materialize
    plaintext v24 SQLite rows so cross-machine restores work without the source
    machine's OS Keychain/DPAPI secret.
    """
    base = Path(user_data_dir)
    portable_path = base / CHROMIUM_PORTABLE_FILENAME
    if not portable_path.is_file() or portable_path.is_symlink():
        return 0
    items = _read_portable_chromium_cookies(base, key=backup_key)
    if not items:
        return 0
    save_portable_chromium_cookies(base, items)
    db_path = chromium_cookies_path(base)
    _write_chromium_sqlite(db_path, items, replace_all=False)
    return len(items)
