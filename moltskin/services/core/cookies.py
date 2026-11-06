"""环境 Cookie 的读写。

直接操作 Firefox 的 ``cookies.sqlite``，这样导入/导出不需要启动浏览器，
也不会污染已经保存的登录态。写入前必须确保环境已关闭。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

SAME_SITE_TO_INT = {
    "unspecified": 0,
    "none": 3,
    "no_restriction": 3,
    "lax": 1,
    "strict": 2,
}
INT_TO_SAME_SITE = {0: "unspecified", 1: "lax", 2: "strict", 3: "no_restriction"}

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
        conn = sqlite3.connect(path, timeout=10)
        conn.executescript(SCHEMA)
        return conn
    if not path.exists():
        raise FileNotFoundError(str(path))
    # 只读并忽略锁，环境运行中也能导出
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def read_cookies(user_data_dir: str | Path, domain: str = "") -> list[dict[str, Any]]:
    path = cookies_path(user_data_dir)
    if not path.exists():
        return []
    conn = _connect(user_data_dir)
    try:
        sql = "SELECT * FROM moz_cookies"
        params: tuple[Any, ...] = ()
        if domain:
            sql += " WHERE host LIKE ?"
            params = (f"%{domain}",)
        sql += " ORDER BY host, name"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    items = []
    for row in rows:
        expiry = row["expiry"] or 0
        items.append(
            {
                "name": row["name"],
                "value": row["value"],
                "domain": row["host"],
                "path": row["path"] or "/",
                "expires": expiry if expiry else -1,
                "secure": bool(row["isSecure"]),
                "httpOnly": bool(row["isHttpOnly"]),
                "sameSite": INT_TO_SAME_SITE.get(row["sameSite"], "unspecified"),
            }
        )
    return items


def _now_us() -> int:
    return int(time.time() * 1_000_000)


def write_cookies(
    user_data_dir: str | Path,
    cookies: Iterable[dict[str, Any]],
    mode: str = "merge",
) -> dict[str, int]:
    """把浏览器格式的 Cookie 写入环境。

    mode="replace" 会先清空；"merge" 则按 (name, host, path) 覆盖同名 Cookie。
    """
    conn = _connect(user_data_dir, writable=True)
    inserted = 0
    try:
        if mode == "replace":
            conn.execute("DELETE FROM moz_cookies")
        for raw in cookies:
            cookie = _normalize(raw)
            if not cookie:
                continue
            expires = cookie["expires"]
            expiry = int(expires) if expires and expires > 0 else 0
            now_us = _now_us()
            conn.execute(
                "INSERT INTO moz_cookies("
                "originAttributes, name, value, host, path, expiry, lastAccessed, creationTime,"
                "isSecure, isHttpOnly, inBrowserElement, sameSite, schemeMap,"
                "isPartitionedAttributeSet, updateTime) "
                "VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, 0, ?) "
                "ON CONFLICT(name, host, path, originAttributes) DO UPDATE SET "
                "value=excluded.value, expiry=excluded.expiry, isSecure=excluded.isSecure,"
                "isHttpOnly=excluded.isHttpOnly, sameSite=excluded.sameSite,"
                "updateTime=excluded.updateTime",
                (
                    cookie["name"],
                    cookie["value"],
                    cookie["domain"],
                    cookie["path"],
                    expiry,
                    now_us,
                    now_us,
                    int(cookie["secure"]),
                    int(cookie["httpOnly"]),
                    SAME_SITE_TO_INT.get(cookie["sameSite"], 0),
                    now_us,
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return {"applied": inserted, "mode": mode}


def _normalize(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = str(raw.get("name") or "").strip()
    domain = str(raw.get("domain") or raw.get("host") or "").strip()
    if not name or not domain:
        return None
    expires = raw.get("expires", raw.get("expirationDate", raw.get("expiry", -1)))
    try:
        expires = float(expires)
    except (TypeError, ValueError):
        expires = -1
    same_site = str(raw.get("sameSite") or "unspecified").lower()
    if same_site in ("no_restriction", "none"):
        same_site = "no_restriction"
    elif same_site in ("lax", "strict"):
        pass
    else:
        same_site = "unspecified"
    return {
        "name": name,
        "value": str(raw.get("value") or ""),
        "domain": domain,
        "path": str(raw.get("path") or "/"),
        "expires": expires,
        "secure": bool(raw.get("secure", False)),
        "httpOnly": bool(raw.get("httpOnly", raw.get("httponly", False))),
        "sameSite": same_site,
    }


def to_netscape(cookies: Iterable[dict[str, Any]]) -> str:
    lines = ["# Netscape HTTP Cookie File", "# Exported by Moltskin", ""]
    for c in cookies:
        domain = c["domain"]
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if c["secure"] else "FALSE"
        expires = int(c["expires"]) if c.get("expires", -1) > 0 else 0
        lines.append(
            "\t".join(
                [domain, include_sub, c["path"], secure, str(expires), c["name"], c["value"]]
            )
        )
    return "\n".join(lines) + "\n"


def parse_netscape(text: str) -> list[dict[str, Any]]:
    cookies = []
    for line in text.splitlines():
        line = line.strip("\n")
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            parts = line.split()
        if len(parts) < 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts[:7]
        try:
            expires = int(float(expires))
        except ValueError:
            expires = -1
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path or "/",
                "expires": expires if expires > 0 else -1,
                "secure": secure.upper() == "TRUE",
                "httpOnly": False,
                "sameSite": "unspecified",
            }
        )
    return cookies


def parse_payload(payload: Any) -> tuple[list[dict[str, Any]], str]:
    """接受 JSON 数组、Playwright storage_state，或 Netscape cookies.txt 文本。"""
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("[") or text.startswith("{"):
            try:
                return parse_payload(json.loads(text))
            except json.JSONDecodeError:
                pass
        return parse_netscape(text), "netscape"
    if isinstance(payload, dict):
        if "cookies" in payload:
            return [c for c in payload["cookies"] if isinstance(c, dict)], "storage_state"
        if "data" in payload and isinstance(payload["data"], list):
            return [c for c in payload["data"] if isinstance(c, dict)], "json"
        return [], "unknown"
    if isinstance(payload, list):
        return [c for c in payload if isinstance(c, dict)], "json"
    return [], "unknown"
