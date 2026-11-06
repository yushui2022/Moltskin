"""Moltskin 本地服务（M0）。

启动：
    .venv/bin/uvicorn moltskin.services.core.main:app --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
from contextlib import asynccontextmanager
from importlib.metadata import version as pkg_version
from typing import Any

from camoufox.fingerprints import get_random_preset
from camoufox.multiversion import list_installed
from camoufox.pkgman import installed_verstr
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from . import config, db, launcher, proxy_checker, selftest
from .security import get_token

# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    group_id: str | None = None
    proxy_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    remark: str = ""
    os: str = "windows"
    headless: bool = False
    fingerprint: dict[str, Any] = Field(default_factory=dict)


class ProfileUpdate(BaseModel):
    name: str | None = None
    group_id: str | None = None
    proxy_id: str | None = None
    tags: list[str] | None = None
    remark: str | None = None
    os: str | None = None
    headless: bool | None = None
    fingerprint: dict[str, Any] | None = None


class ProxyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    protocol: str = "http"
    host: str = Field(min_length=1)
    port: int = Field(gt=0, lt=65536)
    username: str | None = None
    password: str | None = None


class ProxyUpdate(BaseModel):
    name: str | None = None
    protocol: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_id: str | None = None


class GroupUpdate(BaseModel):
    name: str | None = None
    parent_id: str | None = None


class BatchRequest(BaseModel):
    action: str
    ids: list[str] = Field(min_length=1)
    group_id: str | None = None
    tags: list[str] | None = None
    delete_data: bool = True


class ImportRequest(BaseModel):
    profiles: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def new_id(prefix: str) -> str:
    return f"{prefix}_{os.urandom(6).hex()}"


def profile_query() -> str:
    return """
        SELECT p.*,
               g.name  AS group_name,
               px.name AS proxy_name,
               px.exit_ip AS proxy_exit_ip,
               px.country AS proxy_country,
               px.timezone AS proxy_timezone,
               px.latency_ms AS proxy_latency,
               px.status AS proxy_status
        FROM profiles p
        LEFT JOIN groups g ON g.id = p.group_id
        LEFT JOIN proxies px ON px.id = p.proxy_id
    """


def fingerprint_preview(fingerprint: dict[str, Any], os_name: str) -> dict[str, Any] | None:
    preset = (fingerprint or {}).get("preset")
    if not preset:
        return None
    nav = preset.get("navigator") or {}
    screen = preset.get("screen") or {}
    webgl = preset.get("webgl") or {}
    user_agent = nav.get("userAgent") or ""
    try:
        major = installed_verstr().split(".", 1)[0]
        user_agent = re.sub(r"rv:\d+\.\d+", f"rv:{major}.0", user_agent)
        user_agent = re.sub(r"Firefox/\d+\.\d+", f"Firefox/{major}.0", user_agent)
    except Exception:  # noqa: BLE001 - 预览不应因版本读取失败而报错
        pass
    return {
        "os": os_name,
        "user_agent": user_agent,
        "platform": nav.get("platform"),
        "hardware_concurrency": nav.get("hardwareConcurrency"),
        "screen": f"{screen.get('width')}x{screen.get('height')}",
        "color_depth": screen.get("colorDepth"),
        "webgl_vendor": webgl.get("unmaskedVendor"),
        "webgl_renderer": webgl.get("unmaskedRenderer"),
    }


def ensure_presets() -> int:
    """给历史环境补上指纹预设，保证每次启动身份稳定。"""
    changed = 0
    with db.session() as conn:
        rows = conn.execute("SELECT id, os, fingerprint FROM profiles").fetchall()
        for row in rows:
            fingerprint = json.loads(row["fingerprint"] or "{}")
            if fingerprint.get("preset"):
                continue
            preset = get_random_preset(os=row["os"])
            if not preset:
                continue
            fingerprint["preset"] = preset
            conn.execute(
                "UPDATE profiles SET fingerprint=?, updated_at=? WHERE id=?",
                (json.dumps(fingerprint, ensure_ascii=False), db.now(), row["id"]),
            )
            changed += 1
    return changed


def serialize_profile(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "group": {"id": row["group_id"], "name": row["group_name"]} if row["group_id"] else None,
        "tags": json.loads(row["tags"] or "[]"),
        "remark": row["remark"],
        "os": row["os"],
        "headless": bool(row["headless"]),
        "status": row["status"],
        "fingerprint_preview": fingerprint_preview(
            json.loads(row["fingerprint"] or "{}"), row["os"]
        ),
        "user_data_dir": row["user_data_dir"],
        "proxy": (
            {
                "id": row["proxy_id"],
                "name": row["proxy_name"],
                "exit_ip": row["proxy_exit_ip"],
                "country": row["proxy_country"],
                "timezone": row["proxy_timezone"],
                "latency_ms": row["proxy_latency"],
                "status": row["proxy_status"],
            }
            if row["proxy_id"]
            else None
        ),
        "last_session_id": row["last_session_id"],
        "last_opened_at": row["last_opened_at"],
        "open_count": row["open_count"] if "open_count" in row.keys() else 0,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def serialize_proxy(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "protocol": row["protocol"],
        "host": row["host"],
        "port": row["port"],
        "username": row["username"],
        "has_password": bool(row["password"]),
        "exit_ip": row["exit_ip"],
        "country": row["country"],
        "timezone": row["timezone"],
        "latency_ms": row["latency_ms"],
        "status": row["status"],
        "last_checked_at": row["last_checked_at"],
        "created_at": row["created_at"],
    }


def _fingerprint_with_preset(fingerprint: dict[str, Any], os_name: str) -> dict[str, Any]:
    data = dict(fingerprint or {})
    if not data.get("preset"):
        preset = get_random_preset(os=os_name)
        if preset:
            data["preset"] = preset
    return data


def get_profile_row(profile_id: str) -> sqlite3.Row:
    with db.session() as conn:
        row = conn.execute(profile_query() + " WHERE p.id=?", (profile_id,)).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "PROFILE_NOT_FOUND", "message": "环境不存在"})
    return row


def raise_launch_error(exc: launcher.LaunchError) -> None:
    raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})


# ---------------------------------------------------------------------------
# 应用
# ---------------------------------------------------------------------------


def proxy_health_loop(stop_event: Event) -> None:
    """Periodically re-check proxies that have gone stale."""
    while not stop_event.wait(30):
        minutes = int(db.get_setting("proxy_health_check_minutes", 30) or 0)
        if minutes <= 0:
            continue
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        with db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM proxies WHERE last_checked_at IS NULL OR last_checked_at < ?",
                (cutoff,),
            ).fetchall()
        for row in rows:
            if stop_event.is_set():
                break
            try:
                proxy_checker.check_and_save(dict(row))
            except Exception:  # noqa: BLE001 - 后台巡检失败不应影响服务
                pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.init_db()
    ensure_presets()
    get_token()
    launcher.cleanup_orphans()
    launcher.cleanup_browser_orphans()
    stop_event = Event()
    health_thread = Thread(target=proxy_health_loop, args=(stop_event,), daemon=True)
    health_thread.start()
    try:
        yield
    finally:
        stop_event.set()


app = FastAPI(title="Moltskin", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/v1") and path != "/api/v1/health":
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {get_token()}":
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "UNAUTHORIZED", "message": "token 无效"}},
            )
    return await call_next(request)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        error = detail
    else:
        error = {"code": "HTTP_ERROR", "message": str(detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": error})


@app.get("/", response_class=HTMLResponse)
def index():
    html = (config.WEB_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace("__MOLTSKIN_TOKEN__", get_token())


@app.get("/favicon.ico")
def favicon():
    return FileResponse(config.WEB_DIR / "favicon-32.png", media_type="image/png")


@app.get("/logo.svg")
def logo():
    return FileResponse(config.WEB_DIR / "logo.svg", media_type="image/svg+xml")


@app.get("/logo.png")
def logo_png():
    return FileResponse(config.WEB_DIR / "logo.png", media_type="image/png")


@app.get("/apple-touch-icon.png")
def apple_touch_icon():
    return FileResponse(config.WEB_DIR / "apple-touch-icon.png", media_type="image/png")


@app.get("/favicon-16.png")
@app.get("/favicon-32.png")
def favicon_png(request: Request):
    name = request.url.path.lstrip("/")
    return FileResponse(config.WEB_DIR / name, media_type="image/png")


@app.get("/fonts/{name}")
def font(name: str):
    path = config.WEB_DIR / "fonts" / name
    if not path.is_file():
        raise HTTPException(404, detail={"code": "FONT_NOT_FOUND", "message": "字体不存在"})
    return FileResponse(path, media_type="font/woff2")


# ---------------------------------------------------------------------------
# 健康 / 统计 / 设置
# ---------------------------------------------------------------------------


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "version": "0.2.0"}


@app.get("/api/v1/export/profiles")
def export_profiles():
    with db.session() as conn:
        rows = conn.execute(profile_query() + " ORDER BY p.created_at").fetchall()
        groups = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM groups").fetchall()}
        proxies = {}
        for row in conn.execute("SELECT * FROM proxies").fetchall():
            proxies[row["id"]] = {
                "name": row["name"], "protocol": row["protocol"], "host": row["host"],
                "port": row["port"], "username": row["username"],
            }
    items = []
    for row in rows:
        fingerprint = json.loads(row["fingerprint"] or "{}")
        items.append({
            "name": row["name"],
            "group": groups.get(row["group_id"]),
            "tags": json.loads(row["tags"] or "[]"),
            "remark": row["remark"],
            "os": row["os"],
            "headless": bool(row["headless"]),
            "fingerprint": fingerprint,
            "proxy": proxies.get(row["proxy_id"]),
        })
    payload = {"version": 1, "exported_at": db.now(), "profiles": items}
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="moltskin-profiles.json"'},
    )


@app.post("/api/v1/import/profiles")
def import_profiles(payload: ImportRequest):
    if not payload.profiles:
        raise HTTPException(422, detail={"code": "NOTHING_TO_IMPORT", "message": "没有可导入的环境"})
    created = 0
    skipped: list[dict[str, str]] = []
    with db.session() as conn:
        existing_names = {row["name"] for row in conn.execute("SELECT name FROM profiles").fetchall()}
        groups = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM groups").fetchall()}
        proxies: dict[tuple, str] = {}
        for row in conn.execute("SELECT id, host, port, username FROM proxies").fetchall():
            proxies[(row["host"], row["port"], row["username"] or "")] = row["id"]

        for item in payload.profiles:
            name = str(item.get("name") or "").strip()
            if not name:
                skipped.append({"name": "", "reason": "名称为空"})
                continue
            final_name = name
            suffix = 2
            while final_name in existing_names:
                final_name = f"{name} ({suffix})"
                suffix += 1

            group_id = None
            group_name = str(item.get("group") or "").strip()
            if group_name:
                if group_name not in groups:
                    gid = new_id("grp")
                    ts = db.now()
                    conn.execute(
                        "INSERT INTO groups(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        (gid, group_name, ts, ts),
                    )
                    groups[group_name] = gid
                group_id = groups[group_name]

            proxy_id = None
            proxy = item.get("proxy") or {}
            if proxy.get("host") and proxy.get("port"):
                key = (proxy["host"], int(proxy["port"]), proxy.get("username") or "")
                proxy_id = proxies.get(key)
                if proxy_id is None:
                    proxy_id = new_id("px")
                    ts = db.now()
                    conn.execute(
                        "INSERT INTO proxies(id, name, protocol, host, port, username, password, status, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, '', 'unchecked', ?, ?)",
                        (proxy_id, proxy.get("name") or proxy["host"], proxy.get("protocol") or "http",
                         proxy["host"], int(proxy["port"]), proxy.get("username"), ts, ts),
                    )
                    proxies[key] = proxy_id

            profile_id = new_id("pf")
            user_data_dir = config.PROFILES_DIR / profile_id
            user_data_dir.mkdir(parents=True, exist_ok=True)
            fingerprint = _fingerprint_with_preset(item.get("fingerprint") or {}, item.get("os") or "windows")
            ts = db.now()
            conn.execute(
                "INSERT INTO profiles(id, name, group_id, proxy_id, tags, remark, os, fingerprint, "
                "user_data_dir, headless, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)",
                (
                    profile_id, final_name, group_id, proxy_id,
                    json.dumps(item.get("tags") or [], ensure_ascii=False),
                    str(item.get("remark") or ""), item.get("os") or "windows",
                    json.dumps(fingerprint, ensure_ascii=False), str(user_data_dir),
                    int(bool(item.get("headless"))), ts, ts,
                ),
            )
            existing_names.add(final_name)
            created += 1
    db.log("profiles.import", None, {"created": created, "skipped": len(skipped)})
    return {"data": {"created": created, "skipped": skipped}}


@app.get("/api/v1/stats")
def stats():
    today_start = (
        datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    )
    with db.session() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()["n"]
        running = conn.execute(
            "SELECT COUNT(*) AS n FROM profiles WHERE status='running'"
        ).fetchone()["n"]
        crashed = conn.execute(
            "SELECT COUNT(*) AS n FROM profiles WHERE status='crashed'"
        ).fetchone()["n"]
        today_opened = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE started_at >= ?", (today_start,)
        ).fetchone()["n"]
        opened = conn.execute(
            "SELECT COUNT(*) AS n FROM profiles WHERE last_opened_at IS NOT NULL"
        ).fetchone()["n"]
        proxy_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM proxies GROUP BY status"
        ).fetchall()
        groups = conn.execute("SELECT COUNT(*) AS n FROM groups").fetchone()["n"]
        tags = conn.execute(
            "SELECT COUNT(DISTINCT value) AS n FROM profiles, json_each(profiles.tags)"
        ).fetchone()["n"]
    proxy_stats = {"ok": 0, "slow": 0, "failed": 0, "unchecked": 0}
    for row in proxy_rows:
        if row["status"] in proxy_stats:
            proxy_stats[row["status"]] = row["n"]
    proxy_total = sum(proxy_stats.values())
    return {
        "data": {
            "profiles_total": total,
            "profiles_running": running,
            "profiles_crashed": crashed,
            "profiles_opened": opened,
            "today_opened": today_opened,
            "max_concurrent": db.get_setting("max_concurrent", 10),
            "profile_limit": db.get_setting("profile_limit", 200),
            "daily_open_limit": db.get_setting("daily_open_limit", 20000),
            "groups_total": groups,
            "tags_total": tags,
            "proxies_total": proxy_total,
            "proxies_ok": proxy_stats["ok"] + proxy_stats["slow"],
            "proxies_failed": proxy_stats["failed"],
        }
    }


@app.get("/api/v1/engine")
def engine():
    """直接读取 Camoufox 的版本与已安装内核信息。"""
    browsers = []
    for installed in list_installed():
        browsers.append(
            {
                "version": installed.version.full_string,
                "build": installed.version.build,
                "path": str(installed.path),
                "active": installed.is_active,
                "prerelease": installed.is_prerelease,
            }
        )
    return {
        "data": {
            "camoufox": pkg_version("camoufox"),
            "playwright": pkg_version("playwright"),
            "browsers": browsers,
        }
    }


@app.get("/api/v1/settings")
def get_settings():
    with db.session() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    data = {row["key"]: json.loads(row["value"]) for row in rows}
    data["data_dir"] = str(config.DATA_DIR)
    return {"data": data}


@app.put("/api/v1/settings")
def put_settings(payload: dict[str, Any]):
    allowed = set(config.DEFAULTS)
    for key, value in payload.items():
        if key in allowed:
            db.set_setting(key, value)
    return get_settings()


# ---------------------------------------------------------------------------
# 环境
# ---------------------------------------------------------------------------


@app.get("/api/v1/profiles")
def list_profiles(q: str = "", status: str = "", group_id: str = "",
                  tag: str = "", opened: str = "", field: str = "all",
                  op: str = "contains", sort: str = "last_opened", order: str = "desc",
                  page: int = 1, page_size: int = 50):
    """环境列表：支持关键词 + 字段/运算符、分组、标签、打开状态、排序与分页。"""
    where, params = [], []

    if q:
        like = f"%{q}%"
        if op == "equals":
            expr = "= ?"
            operand = q
        elif op == "not_contains":
            expr = "NOT LIKE ?"
            operand = like
        else:
            expr = "LIKE ?"
            operand = like
        if field == "name":
            where.append(f"p.name {expr}")
            params.append(operand)
        elif field == "remark":
            where.append(f"p.remark {expr}")
            params.append(operand)
        elif field == "proxy":
            where.append(f"COALESCE(px.exit_ip,'') {expr}")
            params.append(operand)
        elif field == "group":
            where.append(f"COALESCE(g.name,'') {expr}")
            params.append(operand)
        else:
            where.append(
                f"(p.name {expr} OR p.remark {expr} OR COALESCE(px.exit_ip,'') {expr} "
                f"OR COALESCE(g.name,'') {expr})"
            )
            params += [operand] * 4

    if status == "opened":
        where.append("p.last_opened_at IS NOT NULL")
    elif status == "unopened":
        where.append("p.last_opened_at IS NULL")
    elif status == "stopped":
        where.append("p.status <> 'running'")
    elif status:
        where.append("p.status = ?")
        params.append(status)

    if group_id == "__none__":
        where.append("p.group_id IS NULL")
    elif group_id:
        where.append("p.group_id = ?")
        params.append(group_id)

    if tag:
        where.append("EXISTS (SELECT 1 FROM json_each(p.tags) WHERE value = ?)")
        params.append(tag)

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    offset = max(page - 1, 0) * page_size

    sort_map = {
        "seq": "p.created_at",
        "name": "p.name",
        "group": "COALESCE(g.name,'')",
        "proxy": "COALESCE(px.exit_ip,'')",
        "status": "p.status",
        "created_at": "p.created_at",
        "last_opened": "COALESCE(p.last_opened_at, p.created_at)",
        "open_count": "COALESCE(p.open_count, 0)",
    }
    order_sql = "DESC" if str(order).lower() != "asc" else "ASC"
    sort_sql = sort_map.get(sort, sort_map["last_opened"])
    if sort == "status":
        # 运行中的排前面，其余按时间
        sort_sql = "(p.status = 'running')"

    with db.session() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM profiles p "
            "LEFT JOIN groups g ON g.id = p.group_id "
            "LEFT JOIN proxies px ON px.id = p.proxy_id" + clause,
            params,
        ).fetchone()["n"]
        rows = conn.execute(
            profile_query() + clause +
            f" ORDER BY {sort_sql} {order_sql}, p.name ASC LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()

    return {
        "data": {
            "items": [serialize_profile(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
    }


@app.post("/api/v1/profiles", status_code=201)
def create_profile(payload: ProfileCreate):
    profile_id = new_id("pf")
    user_data_dir = config.PROFILES_DIR / profile_id
    user_data_dir.mkdir(parents=True, exist_ok=True)
    ts = db.now()
    try:
        with db.session() as conn:
            conn.execute(
                "INSERT INTO profiles(id, name, group_id, proxy_id, tags, remark, os, "
                "fingerprint, user_data_dir, headless, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)",
                (
                    profile_id,
                    payload.name.strip(),
                    payload.group_id or None,
                    payload.proxy_id or None,
                    json.dumps(payload.tags, ensure_ascii=False),
                    payload.remark,
                    payload.os,
                    json.dumps(_fingerprint_with_preset(payload.fingerprint, payload.os), ensure_ascii=False),
                    str(user_data_dir),
                    int(payload.headless),
                    ts,
                    ts,
                ),
            )
    except sqlite3.IntegrityError:
        shutil.rmtree(user_data_dir, ignore_errors=True)
        raise HTTPException(409, detail={"code": "PROFILE_NAME_EXISTS", "message": "环境名称已存在"})
    db.log("profile.create", profile_id, {"name": payload.name})
    return {"data": serialize_profile(get_profile_row(profile_id))}


@app.get("/api/v1/profiles/{profile_id}")
def get_profile(profile_id: str):
    return {"data": serialize_profile(get_profile_row(profile_id))}


@app.patch("/api/v1/profiles/{profile_id}")
def update_profile(profile_id: str, payload: ProfileUpdate):
    row = get_profile_row(profile_id)
    fields: dict[str, Any] = {}
    for key in ("name", "group_id", "proxy_id", "remark", "os", "headless"):
        value = getattr(payload, key)
        if value is not None:
            fields[key] = value
    if payload.tags is not None:
        fields["tags"] = json.dumps(payload.tags, ensure_ascii=False)
    if payload.fingerprint is not None:
        if row["status"] == "running":
            raise HTTPException(
                409, detail={"code": "PROFILE_RUNNING", "message": "运行中的环境不能修改指纹"}
            )
        fields["fingerprint"] = json.dumps(payload.fingerprint, ensure_ascii=False)
    if not fields:
        return {"data": serialize_profile(row)}

    fields["updated_at"] = db.now()
    assignments = ", ".join(f"{key}=?" for key in fields)
    try:
        with db.session() as conn:
            conn.execute(
                f"UPDATE profiles SET {assignments} WHERE id=?",
                (*fields.values(), profile_id),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(409, detail={"code": "PROFILE_NAME_EXISTS", "message": "环境名称已存在"})
    db.log("profile.update", profile_id, {"fields": list(fields)})
    return {"data": serialize_profile(get_profile_row(profile_id))}


@app.post("/api/v1/profiles/{profile_id}/reset-fingerprint")
def reset_fingerprint(profile_id: str):
    """重新生成指纹预设（身份变化，Cookie 保留）。"""
    row = get_profile_row(profile_id)
    if row["status"] == "running":
        raise HTTPException(409, detail={"code": "PROFILE_RUNNING", "message": "请先关闭环境"})
    fingerprint = json.loads(row["fingerprint"] or "{}")
    preset = get_random_preset(os=row["os"])
    if not preset:
        raise HTTPException(500, detail={"code": "PRESET_FAILED", "message": "无法生成指纹预设"})
    fingerprint["preset"] = preset
    with db.session() as conn:
        conn.execute(
            "UPDATE profiles SET fingerprint=?, updated_at=? WHERE id=?",
            (json.dumps(fingerprint, ensure_ascii=False), db.now(), profile_id),
        )
    db.log("profile.reset_fingerprint", profile_id, {"os": row["os"]})
    return {"data": serialize_profile(get_profile_row(profile_id))}


@app.delete("/api/v1/profiles/{profile_id}")
def delete_profile(profile_id: str, delete_data: bool = False):
    row = get_profile_row(profile_id)
    if row["status"] == "running":
        raise HTTPException(409, detail={"code": "PROFILE_RUNNING", "message": "请先关闭环境"})
    with db.session() as conn:
        conn.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
    if delete_data:
        shutil.rmtree(row["user_data_dir"], ignore_errors=True)
    db.log("profile.delete", profile_id, {"delete_data": delete_data})
    return {"data": {"id": profile_id, "deleted": True}}


@app.post("/api/v1/profiles/{profile_id}/duplicate")
def duplicate_profile(profile_id: str):
    row = get_profile_row(profile_id)
    new_profile_id = new_id("pf")
    user_data_dir = config.PROFILES_DIR / new_profile_id
    user_data_dir.mkdir(parents=True, exist_ok=True)
    ts = db.now()
    source_fp = json.loads(row["fingerprint"] or "{}")
    source_fp.pop("preset", None)  # 复制必须得到新身份，不能共用指纹
    duplicated_fp = _fingerprint_with_preset(source_fp, row["os"])
    with db.session() as conn:
        conn.execute(
            "INSERT INTO profiles(id, name, group_id, proxy_id, tags, remark, os, "
            "fingerprint, user_data_dir, headless, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)",
            (
                new_profile_id,
                f"{row['name']} 副本",
                row["group_id"],
                row["proxy_id"],
                row["tags"],
                row["remark"],
                row["os"],
                json.dumps(duplicated_fp, ensure_ascii=False),
                str(user_data_dir),
                row["headless"],
                ts,
                ts,
            ),
        )
    db.log("profile.duplicate", new_profile_id, {"from": profile_id})
    return {"data": serialize_profile(get_profile_row(new_profile_id))}


@app.post("/api/v1/profiles/batch")
def batch_profiles(payload: BatchRequest):
    if len(payload.ids) > 500:
        raise HTTPException(422, detail={"code": "BATCH_TOO_LARGE", "message": "一次最多操作 500 个环境"})
    action = payload.action
    if action not in ("launch", "stop", "delete", "move", "tag"):
        raise HTTPException(422, detail={"code": "INVALID_ACTION", "message": "不支持的操作"})

    results: list[dict[str, Any]] = []

    if action == "launch":
        workers = max(1, int(db.get_setting("batch_concurrency", 5)))

        def run(profile_id: str) -> dict[str, Any]:
            try:
                launcher.launch(profile_id)
                return {"id": profile_id, "ok": True}
            except launcher.LaunchError as exc:
                return {"id": profile_id, "ok": False, "error": {"code": exc.code, "message": exc.message}}
            except Exception as exc:  # noqa: BLE001
                return {"id": profile_id, "ok": False, "error": {"code": "INTERNAL", "message": str(exc)}}

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(run, payload.ids))

    elif action == "stop":
        for profile_id in payload.ids:
            with db.session() as conn:
                session = conn.execute(
                    "SELECT id FROM sessions WHERE profile_id=? AND state IN ('starting','running','stopping') "
                    "ORDER BY started_at DESC LIMIT 1",
                    (profile_id,),
                ).fetchone()
            if session is None:
                results.append({"id": profile_id, "ok": False,
                                "error": {"code": "NOT_RUNNING", "message": "该环境未在运行"}})
                continue
            try:
                launcher.stop(session["id"])
                results.append({"id": profile_id, "ok": True})
            except launcher.LaunchError as exc:
                results.append({"id": profile_id, "ok": False,
                                "error": {"code": exc.code, "message": exc.message}})

    elif action == "move":
        with db.session() as conn:
            for profile_id in payload.ids:
                conn.execute(
                    "UPDATE profiles SET group_id=?, updated_at=? WHERE id=?",
                    (payload.group_id, db.now(), profile_id),
                )
                results.append({"id": profile_id, "ok": True})

    elif action == "tag":
        with db.session() as conn:
            for profile_id in payload.ids:
                conn.execute(
                    "UPDATE profiles SET tags=?, updated_at=? WHERE id=?",
                    (json.dumps(payload.tags or [], ensure_ascii=False), db.now(), profile_id),
                )
                results.append({"id": profile_id, "ok": True})

    elif action == "delete":
        with db.session() as conn:
            for profile_id in payload.ids:
                row = conn.execute("SELECT status, user_data_dir FROM profiles WHERE id=?", (profile_id,)).fetchone()
                if row is None:
                    results.append({"id": profile_id, "ok": False,
                                    "error": {"code": "PROFILE_NOT_FOUND", "message": "环境不存在"}})
                    continue
                if row["status"] == "running":
                    results.append({"id": profile_id, "ok": False,
                                    "error": {"code": "PROFILE_RUNNING", "message": "请先关闭环境"}})
                    continue
                conn.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
                if payload.delete_data:
                    import shutil as _shutil
                    _shutil.rmtree(row["user_data_dir"], ignore_errors=True)
                results.append({"id": profile_id, "ok": True})

    db.log("profiles.batch", None, {"action": action, "count": len(payload.ids), "ok": sum(1 for r in results if r["ok"])})
    return {"data": {"results": results, "ok": sum(1 for r in results if r["ok"]), "failed": sum(1 for r in results if not r["ok"])}}


@app.post("/api/v1/profiles/{profile_id}/launch")
def launch_profile(profile_id: str):
    get_profile_row(profile_id)
    try:
        result = launcher.launch(profile_id)
    except launcher.LaunchError as exc:
        raise_launch_error(exc)
    return {"data": result}


@app.post("/api/v1/profiles/{profile_id}/stop")
def stop_profile(profile_id: str):
    with db.session() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE profile_id=? AND state IN ('starting','running','stopping') "
            "ORDER BY started_at DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(409, detail={"code": "NOT_RUNNING", "message": "该环境未在运行"})
    try:
        result = launcher.stop(row["id"])
    except launcher.LaunchError as exc:
        raise_launch_error(exc)
    return {"data": result}


@app.get("/api/v1/profiles/{profile_id}/status")
def profile_status(profile_id: str):
    try:
        return {"data": launcher.status(profile_id)}
    except launcher.LaunchError as exc:
        raise_launch_error(exc)


def _ua_os(user_agent: str) -> str | None:
    ua = user_agent or ""
    if "Windows" in ua:
        return "windows"
    if "Macintosh" in ua or "Mac OS X" in ua:
        return "macos"
    if "Linux" in ua or "X11" in ua:
        return "linux"
    return None


@app.post("/api/v1/profiles/{profile_id}/verify")
def verify_profile(profile_id: str):
    row = get_profile_row(profile_id)
    fingerprint = json.loads(row["fingerprint"] or "{}")
    preview = fingerprint_preview(fingerprint, row["os"])
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []

    if preview:
        ua_os = _ua_os(preview.get("user_agent") or "")
        ok = ua_os == row["os"]
        checks.append({"name": "系统 / UA", "ok": ok,
                       "detail": f"配置 {row['os']}，UA 显示 {ua_os or '未知'}"})
        if not ok:
            warnings.append("指纹 UA 与配置的操作系统不一致")
        checks.append({"name": "屏幕", "ok": True,
                       "detail": f"{preview['screen']} @ {preview['color_depth']}bit"})
        checks.append({"name": "WebGL", "ok": True,
                       "detail": preview.get("webgl_renderer") or "未知"})
    else:
        checks.append({"name": "指纹", "ok": False, "detail": "尚未生成指纹预设"})
        warnings.append("尚未生成指纹预设")

    proxy_result = None
    if not row["proxy_id"]:
        checks.append({"name": "代理", "ok": False, "detail": "未绑定代理，将使用本机 IP"})
        warnings.append("未绑定代理，出口 IP 与本机一致")
    else:
        with db.session() as conn:
            proxy = conn.execute("SELECT * FROM proxies WHERE id=?", (row["proxy_id"],)).fetchone()
        if proxy is None:
            checks.append({"name": "代理", "ok": False, "detail": "代理记录不存在"})
            warnings.append("绑定的代理不存在")
        else:
            proxy_result = proxy_checker.check_and_save(dict(proxy))
            ok = proxy_result["status"] not in ("failed", "mismatch")
            detail = (
                f"{proxy_result.get('exit_ip')} · {proxy_result.get('country') or '?'} · "
                f"{proxy_result.get('latency_ms') or '?'}ms"
                if ok else f"不可用：{proxy_result.get('error') or proxy_result['status']}"
            )
            checks.append({"name": "代理连通", "ok": ok, "detail": detail})
            if not ok:
                warnings.append(f"代理不可用：{proxy_result.get('error') or proxy_result['status']}")

            explicit_tz = (fingerprint.get("overrides") or {}).get("timezone") or fingerprint.get("timezone")
            proxy_tz = proxy_result.get("timezone")
            if explicit_tz and proxy_tz and explicit_tz != proxy_tz:
                checks.append({"name": "时区", "ok": False,
                               "detail": f"手动 {explicit_tz} / 代理 {proxy_tz}"})
                warnings.append("手动设置的时区与代理出口不一致")
            elif proxy_tz:
                checks.append({"name": "时区", "ok": True, "detail": "跟随代理出口"})

            if proxy_result.get("locale"):
                checks.append({"name": "国家 / 语言", "ok": True,
                               "detail": f"{proxy_result.get('country') or '?'} · {proxy_result['locale']}"})

    ok = all(check["ok"] for check in checks)
    return {
        "data": {
            "profile_id": profile_id,
            "ok": ok,
            "checks": checks,
            "warnings": warnings,
            "preview": preview,
            "proxy": proxy_result,
        }
    }


@app.post("/api/v1/profiles/{profile_id}/self-test")
def self_test(profile_id: str):
    row = get_profile_row(profile_id)
    if row["status"] == "running":
        raise HTTPException(409, detail={"code": "PROFILE_RUNNING", "message": "请先关闭环境再运行自检"})
    proxy = None
    if row["proxy_id"]:
        with db.session() as conn:
            proxy_row = conn.execute("SELECT * FROM proxies WHERE id=?", (row["proxy_id"],)).fetchone()
        proxy = dict(proxy_row) if proxy_row else None
    try:
        result = selftest.run(dict(row), proxy)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail={"code": "SELF_TEST_FAILED", "message": str(exc)})
    db.log("profile.selftest", profile_id, {"image": result["image"]})
    return {"data": {**result, "image_url": f"/selftest/{result['image']}"}}


@app.get("/selftest/{name}")
def selftest_file(name: str):
    if "/" in name or ".." in name:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "文件不存在"})
    path = config.SELFTEST_DIR / name
    if not path.is_file():
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "文件不存在"})
    media = "image/png" if name.endswith(".png") else "application/json"
    return FileResponse(path, media_type=media)


# ---------------------------------------------------------------------------
# 分组
# ---------------------------------------------------------------------------


@app.get("/api/v1/groups")
def list_groups():
    with db.session() as conn:
        rows = conn.execute(
            "SELECT g.*, COUNT(p.id) AS profile_count FROM groups g "
            "LEFT JOIN profiles p ON p.group_id = g.id "
            "GROUP BY g.id ORDER BY g.sort_order, g.created_at"
        ).fetchall()
    return {"data": [dict(row) for row in rows]}


@app.post("/api/v1/groups", status_code=201)
def create_group(payload: GroupCreate):
    group_id = new_id("grp")
    ts = db.now()
    with db.session() as conn:
        conn.execute(
            "INSERT INTO groups(id, name, parent_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (group_id, payload.name, payload.parent_id, ts, ts),
        )
    db.log("group.create", group_id, {"name": payload.name})
    return {"data": {"id": group_id, "name": payload.name}}


@app.patch("/api/v1/groups/{group_id}")
def update_group(group_id: str, payload: GroupUpdate):
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if fields:
        fields["updated_at"] = db.now()
        assignments = ", ".join(f"{k}=?" for k in fields)
        with db.session() as conn:
            conn.execute(f"UPDATE groups SET {assignments} WHERE id=?", (*fields.values(), group_id))
    return {"data": {"id": group_id}}


@app.delete("/api/v1/groups/{group_id}")
def delete_group(group_id: str):
    with db.session() as conn:
        conn.execute("UPDATE profiles SET group_id=NULL WHERE group_id=?", (group_id,))
        conn.execute("DELETE FROM groups WHERE id=?", (group_id,))
    db.log("group.delete", group_id, {})
    return {"data": {"id": group_id, "deleted": True}}


@app.get("/api/v1/tags")
def list_tags():
    """所有标签及使用次数，用于筛选项。"""
    with db.session() as conn:
        rows = conn.execute(
            "SELECT value AS tag, COUNT(*) AS count FROM profiles, json_each(profiles.tags) "
            "GROUP BY value ORDER BY count DESC, value ASC"
        ).fetchall()
    return {"data": [dict(row) for row in rows]}


# ---------------------------------------------------------------------------
# 代理
# ---------------------------------------------------------------------------


@app.get("/api/v1/proxies")
def list_proxies():
    with db.session() as conn:
        rows = conn.execute(
            "SELECT px.*, (SELECT COUNT(*) FROM profiles p WHERE p.proxy_id = px.id) AS used_by "
            "FROM proxies px ORDER BY px.created_at DESC"
        ).fetchall()
    data = []
    for row in rows:
        item = serialize_proxy(row)
        item["used_by"] = row["used_by"]
        data.append(item)
    return {"data": data}


@app.post("/api/v1/proxies", status_code=201)
def create_proxy(payload: ProxyCreate):
    if payload.protocol not in ("http", "https", "socks5"):
        raise HTTPException(422, detail={"code": "INVALID_PROXY", "message": "协议必须是 http/https/socks5"})
    proxy_id = new_id("px")
    ts = db.now()
    with db.session() as conn:
        conn.execute(
            "INSERT INTO proxies(id, name, protocol, host, port, username, password, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'unchecked', ?, ?)",
            (proxy_id, payload.name, payload.protocol, payload.host, payload.port,
             payload.username, payload.password, ts, ts),
        )
    db.log("proxy.create", proxy_id, {"name": payload.name})
    return {"data": {"id": proxy_id, "name": payload.name, "status": "unchecked"}}


@app.patch("/api/v1/proxies/{proxy_id}")
def update_proxy(proxy_id: str, payload: ProxyUpdate):
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if fields:
        fields["updated_at"] = db.now()
        fields["status"] = "unchecked"
        assignments = ", ".join(f"{k}=?" for k in fields)
        with db.session() as conn:
            conn.execute(f"UPDATE proxies SET {assignments} WHERE id=?", (*fields.values(), proxy_id))
    return {"data": {"id": proxy_id}}


@app.delete("/api/v1/proxies/{proxy_id}")
def delete_proxy(proxy_id: str):
    with db.session() as conn:
        conn.execute("UPDATE profiles SET proxy_id=NULL WHERE proxy_id=?", (proxy_id,))
        conn.execute("DELETE FROM proxies WHERE id=?", (proxy_id,))
    return {"data": {"id": proxy_id, "deleted": True}}


@app.post("/api/v1/proxies/{proxy_id}/check")
def check_proxy(proxy_id: str):
    with db.session() as conn:
        row = conn.execute("SELECT * FROM proxies WHERE id=?", (proxy_id,)).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "PROXY_NOT_FOUND", "message": "代理不存在"})
    result = proxy_checker.check_and_save(dict(row))
    db.log("proxy.check", proxy_id, {"status": result["status"]})
    return {"data": result}


@app.post("/api/v1/proxies/check-batch")
def check_proxies(payload: dict[str, Any]):
    ids = payload.get("ids") or []
    workers = int(db.get_setting("batch_concurrency", 5))

    def run(proxy_id: str) -> dict:
        with db.session() as conn:
            row = conn.execute("SELECT * FROM proxies WHERE id=?", (proxy_id,)).fetchone()
        if row is None:
            return {"id": proxy_id, "status": "failed", "error": "not found"}
        return proxy_checker.check_and_save(dict(row))

    if not ids:
        return {"data": []}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return {"data": list(pool.map(run, ids))}


# ---------------------------------------------------------------------------
# 会话 / 日志
# ---------------------------------------------------------------------------


@app.get("/api/v1/sessions")
def list_sessions(profile_id: str = "", page: int = 1, page_size: int = 50):
    where, params = "", []
    if profile_id:
        where = " WHERE profile_id=?"
        params.append(profile_id)
    offset = max(page - 1, 0) * page_size
    with db.session() as conn:
        rows = conn.execute(
            f"SELECT * FROM sessions{where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()
    return {"data": [dict(row) for row in rows]}


@app.get("/api/v1/sessions/{session_id}")
def get_session(session_id: str):
    with db.session() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND", "message": "会话不存在"})
    return {"data": dict(row)}


@app.post("/api/v1/sessions/{session_id}/stop")
def stop_session(session_id: str):
    try:
        return {"data": launcher.stop(session_id)}
    except launcher.LaunchError as exc:
        raise_launch_error(exc)


@app.get("/api/v1/logs")
def list_logs(response: Response, page: int = 1, page_size: int = 50, action: str = ""):
    where, params = "", []
    if action:
        where = " WHERE action LIKE ?"
        params.append(f"%{action}%")
    offset = max(page - 1, 0) * page_size
    with db.session() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM logs{where}", params).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM logs{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Page"] = str(page)
    response.headers["X-Page-Size"] = str(page_size)
    return {"data": [dict(row) for row in rows]}
