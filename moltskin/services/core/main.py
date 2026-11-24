"""Moltskin 本地服务（M0）。

启动：
    .venv/bin/uvicorn moltskin.services.core.main:app --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
from contextlib import ExitStack, asynccontextmanager
from importlib.metadata import version as pkg_version
from typing import Any, Literal

from camoufox.fingerprints import get_random_preset
from camoufox.multiversion import list_installed
from camoufox.pkgman import installed_verstr
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field, ValidationError

from chromium import native as chromium_native

from . import backup as backup_store
from . import config, db, extensions, jobs, launcher, observation, proxy_checker, scheduler, selftest, state_sync
from . import cookies as cookie_store
from .security import get_token

# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------


Engine = Literal["camoufox", "chromium"]
FontPolicy = Literal["standard", "restricted"]


class ProfileCreate(BaseModel):
    engine: Engine = "camoufox"
    local_fonts: FontPolicy = "standard"
    name: str = Field(min_length=1, max_length=120)
    group_id: str | None = None
    proxy_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    remark: str = ""
    os: str = "windows"
    headless: bool = False
    fingerprint: dict[str, Any] = Field(default_factory=dict)


class ProfileUpdate(BaseModel):
    engine: Engine | None = None
    local_fonts: FontPolicy | None = None
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
    # Each row is validated separately so one invalid entry can be reported by row number.
    profiles: list[Any] = Field(default_factory=list)


class ImportedProxy(BaseModel):
    name: str | None = None
    protocol: Literal["http", "https", "socks5"] = "http"
    host: str = Field(min_length=1)
    port: int = Field(gt=0, lt=65536)
    username: str | None = None


class ImportedProfile(ProfileCreate):
    group: str | None = None
    proxy: ImportedProxy | None = None


class ProfileSelection(BaseModel):
    ids: list[str] = Field(min_length=1)


class CookiePayload(BaseModel):
    cookies: Any = None
    mode: str = "merge"
    text: str | None = None


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    action: str = "launch"
    mode: str = "daily"
    interval_minutes: int = 60
    time_of_day: str = "09:00"
    weekdays: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7])
    target_type: str = "all"
    target_ids: list[str] = Field(default_factory=list)
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    name: str | None = None
    action: str | None = None
    mode: str | None = None
    interval_minutes: int | None = None
    time_of_day: str | None = None
    weekdays: list[int] | None = None
    target_type: str | None = None
    target_ids: list[str] | None = None
    enabled: bool | None = None


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
        "screen": f"{screen['width']}x{screen['height']}" if screen.get("width") and screen.get("height") else None,
        "color_depth": screen.get("colorDepth"),
        "webgl_vendor": webgl.get("unmaskedVendor"),
        "webgl_renderer": webgl.get("unmaskedRenderer"),
    }


def ensure_presets() -> int:
    """给历史环境补上指纹预设，保证每次启动身份稳定。"""
    changed = 0
    with db.session() as conn:
        rows = conn.execute("SELECT id, os, fingerprint FROM profiles WHERE engine='camoufox'").fetchall()
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
        "engine": row["engine"],
        "local_fonts": row["local_fonts"],
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


def camoufox_profile(profile_id: str) -> sqlite3.Row:
    row = get_profile_row(profile_id)
    if row["engine"] != "camoufox":
        raise HTTPException(422, detail={"code": "ENGINE_UNSUPPORTED", "message": "此操作仅支持 Camoufox 环境"})
    return row


def _check_engine_options(engine: str, local_fonts: str, proxy: Any, fingerprint: Any) -> None:
    if engine not in ("camoufox", "chromium") or local_fonts not in ("standard", "restricted"):
        message = "不支持的浏览器内核或字体访问设置"
    elif engine == "camoufox" and local_fonts != "standard":
        message = "本机字体访问设置仅支持 Chromium 环境"
    elif engine == "chromium" and (proxy or fingerprint):
        message = "当前 Chromium 预览版不支持绑定代理或使用 Camoufox 指纹预设"
    else:
        return
    raise HTTPException(422, detail={"code": "ENGINE_OPTIONS", "message": message})


def raise_launch_error(exc: launcher.LaunchError) -> None:
    raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})


# ---------------------------------------------------------------------------
# 应用
# ---------------------------------------------------------------------------


def reap_loop(stop_event: Event) -> None:
    while not stop_event.wait(15):
        try:
            cleaned = launcher.reap_stale()
            if cleaned:
                db.log("session.reap", None, {"count": cleaned})
        except Exception:  # noqa: BLE001 - 后台巡检不能影响服务
            pass


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
    Thread(target=reap_loop, args=(stop_event,), daemon=True).start()
    scheduler.start(stop_event)
    try:
        yield
    finally:
        stop_event.set()


app = FastAPI(title="Moltskin", version="0.4.1", lifespan=lifespan)


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
def health(panel_challenge: str | None = None):
    result = {"status": "ok", "version": "0.4.1"}
    if panel_challenge and re.fullmatch(r"[0-9a-f]{32}", panel_challenge):
        # The local panel launcher can verify this service without sending its API token.
        message = b"moltskin-panel-v1\x00" + panel_challenge.encode("ascii")
        result["panel_proof"] = hmac.digest(get_token().strip().encode(), message, "sha256").hex()
    return result


@app.get("/api/v1/export/profiles")
def export_profiles():
    return _profile_export()


@app.post("/api/v1/export/profiles")
def export_selected_profiles(selection: ProfileSelection):
    return _profile_export(selection.ids)


def _profile_export(ids: list[str] | None = None):
    query = """
        SELECT p.*, g.name AS group_name, px.name AS proxy_name,
               px.protocol, px.host, px.port, px.username
        FROM profiles p
        LEFT JOIN groups g ON g.id = p.group_id
        LEFT JOIN proxies px ON px.id = p.proxy_id
    """
    params = ()
    if ids is not None:
        query += " WHERE p.id IN (SELECT value FROM json_each(?))"
        params = (json.dumps(ids),)
    with db.session() as conn:
        rows = conn.execute(query + " ORDER BY p.created_at, p.id", params).fetchall()
    if ids is not None:
        missing = sorted(set(ids) - {row["id"] for row in rows})
        if missing:
            raise HTTPException(409, detail={
                "code": "PROFILE_SELECTION_CHANGED", "missing_ids": missing,
                "message": f"选中的环境有 {len(missing)} 个已不存在，请刷新后重新选择",
            })
    items = []
    for row in rows:
        fingerprint = json.loads(row["fingerprint"] or "{}")
        items.append({
            "name": row["name"],
            "engine": row["engine"],
            "local_fonts": row["local_fonts"],
            "group": row["group_name"],
            "tags": json.loads(row["tags"] or "[]"),
            "remark": row["remark"],
            "os": row["os"],
            "headless": bool(row["headless"]),
            "fingerprint": fingerprint,
            "proxy": {
                "name": row["proxy_name"], "protocol": row["protocol"], "host": row["host"],
                "port": row["port"], "username": row["username"],
            } if row["proxy_name"] is not None else None,
        })
    payload = {"version": 1, "exported_at": db.now(), "profiles": items}
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="moltskin-profiles.json"'},
    )


def _read_import_profile(raw: Any) -> ImportedProfile:
    item = ImportedProfile.model_validate(raw)
    item.name = item.name.strip()
    if not item.name:
        raise ValueError("名称为空")
    _check_engine_options(item.engine, item.local_fonts, item.proxy, item.fingerprint)
    if item.engine == "chromium":
        if "os" in item.model_fields_set and item.os != "native":
            raise ValueError("Chromium 使用本机操作系统")
        item.os = "native"
    else:
        if item.os not in ("windows", "macos", "linux"):
            raise ValueError("不支持的环境操作系统")
        preset = item.fingerprint.get("preset")
        if preset is not None:
            if not isinstance(preset, dict) or any(
                key in preset and not isinstance(preset[key], dict) for key in ("navigator", "screen", "webgl")
            ):
                raise ValueError("指纹预设格式不正确")
        item.fingerprint = _fingerprint_with_preset(item.fingerprint, item.os)
    return item


def _store_import_profile(item: ImportedProfile) -> str:
    ts = db.now()
    profile_id = new_id("pf")
    user_data_dir = config.PROFILES_DIR / profile_id
    with ExitStack() as cleanup:
        with db.session() as conn:
            # Name allocation, shared references and the profile commit together for each row.
            conn.execute("BEGIN IMMEDIATE")
            name = item.name
            suffix = 2
            while conn.execute("SELECT 1 FROM profiles WHERE name=?", (name,)).fetchone():
                ending = f" ({suffix})"
                name = item.name[:120 - len(ending)] + ending
                suffix += 1

            group_id = None
            group_name = (item.group or "").strip()
            if group_name:
                group = conn.execute("SELECT id FROM groups WHERE name=?", (group_name,)).fetchone()
                if group:
                    group_id = group["id"]
                else:
                    group_id = new_id("grp")
                    conn.execute(
                        "INSERT INTO groups(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        (group_id, group_name, ts, ts),
                    )

            proxy_id = None
            proxy = item.proxy
            if proxy is not None:
                match = conn.execute(
                    "SELECT id FROM proxies WHERE protocol=? AND host=? AND port=? AND COALESCE(username,'')=? "
                    "ORDER BY (name=?) DESC, id LIMIT 1",
                    (proxy.protocol, proxy.host, proxy.port, proxy.username or "", proxy.name or proxy.host),
                ).fetchone()
                if match:
                    proxy_id = match["id"]
                else:
                    proxy_id = new_id("px")
                    conn.execute(
                        "INSERT INTO proxies(id, name, protocol, host, port, username, password, status, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, '', 'unchecked', ?, ?)",
                        (proxy_id, proxy.name or proxy.host, proxy.protocol, proxy.host, proxy.port, proxy.username, ts, ts),
                    )

            conn.execute(
                "INSERT INTO profiles(id, name, group_id, proxy_id, tags, remark, os, fingerprint, "
                "user_data_dir, headless, status, created_at, updated_at, engine, local_fonts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?)",
                (profile_id, name, group_id, proxy_id, json.dumps(item.tags, ensure_ascii=False), item.remark,
                 item.os, json.dumps(item.fingerprint, ensure_ascii=False), str(user_data_dir),
                 int(item.headless), ts, ts, item.engine, item.local_fonts),
            )
            user_data_dir.mkdir(mode=0o700)
            cleanup.callback(user_data_dir.rmdir)
        cleanup.pop_all()
    return name


@app.post("/api/v1/import/profiles")
def import_profiles(payload: ImportRequest):
    if not payload.profiles:
        raise HTTPException(422, detail={"code": "NOTHING_TO_IMPORT", "message": "没有可导入的环境"})
    created = 0
    renamed = []
    skipped = []
    for index, raw in enumerate(payload.profiles, start=1):
        try:
            item = _read_import_profile(raw)
            name = _store_import_profile(item)
        except ValidationError as exc:
            fields = [".".join(map(str, err["loc"])) or "环境" for err in exc.errors()]
            reason = f"格式不正确的字段：{'、'.join(fields)}"
        except HTTPException as exc:
            reason = exc.detail["message"]
        except ValueError as exc:
            reason = str(exc)
        except (OSError, sqlite3.Error) as exc:
            reason = f"无法保存环境：{exc}"
        else:
            created += 1
            if name != item.name:
                renamed.append({"from": item.name, "to": name})
            continue
        label = raw.get("name") if isinstance(raw, dict) else None
        skipped.append({"row": index, "name": label if isinstance(label, str) else "", "reason": reason})

    db.log("profiles.import", None, {"created": created, "renamed": len(renamed), "skipped": len(skipped)})
    return {"data": {"created": created, "renamed": renamed, "skipped": skipped}}


@app.get("/api/v1/stats")
def stats():
    launcher.reap_stale()
    today_start = db.today_start_utc()
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
    """Report installed builds and whether this host can launch the Chromium preview."""
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
    chromium_version = None
    problem = None
    if sys.platform not in {"darwin", "linux"}:
        problem = "Chromium 预览版目前支持 macOS 和 Linux。"
    else:
        try:
            _, chromium_version = chromium_native.probe(chromium_native.default_binary())
        except FileNotFoundError:
            problem = "未找到 Moltskin Chromium，可通过 MOLTSKIN_CHROMIUM_BINARY 指定开发包。"
        except ValueError:
            problem = "此 Chromium 缺少 Moltskin 本机字体访问补丁。"
        except (OSError, subprocess.SubprocessError):
            problem = "无法执行 Moltskin Chromium，请检查开发包路径和执行权限。"
    return {
        "data": {
            "camoufox": pkg_version("camoufox"),
            "playwright": pkg_version("playwright"),
            "browsers": browsers,
            "chromium": {"available": problem is None, "version": chromium_version, "message": problem},
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
    launcher.reap_stale()
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
    _check_engine_options(payload.engine, payload.local_fonts, payload.proxy_id, payload.fingerprint)
    if payload.engine == "chromium" and "os" in payload.model_fields_set and payload.os != "native":
        raise HTTPException(422, detail={"code": "ENGINE_OPTIONS", "message": "Chromium 使用本机操作系统"})
    limit = int(db.get_setting("profile_limit", 200) or 0)
    if limit > 0:
        with db.session() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()["n"]
        if total >= limit:
            raise HTTPException(
                409,
                detail={
                    "code": "PROFILE_LIMIT",
                    "message": f"环境数量已达上限（{limit}），可在设置中调整",
                },
            )
    fingerprint = {} if payload.engine == "chromium" else _fingerprint_with_preset(payload.fingerprint, payload.os)
    profile_id = new_id("pf")
    user_data_dir = config.PROFILES_DIR / profile_id
    user_data_dir.mkdir(parents=True, exist_ok=True)
    ts = db.now()
    try:
        with db.session() as conn:
            conn.execute(
                "INSERT INTO profiles(id, name, group_id, proxy_id, tags, remark, os, "
                "fingerprint, user_data_dir, headless, status, created_at, updated_at, engine, local_fonts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?)",
                (
                    profile_id,
                    payload.name.strip(),
                    payload.group_id or None,
                    payload.proxy_id or None,
                    json.dumps(payload.tags, ensure_ascii=False),
                    payload.remark,
                    "native" if payload.engine == "chromium" else payload.os,
                    json.dumps(fingerprint, ensure_ascii=False),
                    str(user_data_dir),
                    int(payload.headless),
                    ts,
                    ts,
                    payload.engine,
                    payload.local_fonts,
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
    # exclude_unset 才能区分“字段没传”和“显式传 null（解绑代理 / 移出分组）”
    provided = payload.model_dump(exclude_unset=True)
    if "engine" in provided and provided["engine"] != row["engine"]:
        raise HTTPException(409, detail={"code": "ENGINE_IMMUTABLE", "message": "环境创建后不能更换内核"})
    _check_engine_options(row["engine"], provided.get("local_fonts", row["local_fonts"]),
                          provided.get("proxy_id"), provided.get("fingerprint"))
    if row["engine"] == "chromium" and "os" in provided and provided["os"] != "native":
        raise HTTPException(422, detail={"code": "ENGINE_OPTIONS", "message": "Chromium 使用本机操作系统"})
    fields: dict[str, Any] = {}
    if "local_fonts" in provided and payload.local_fonts != row["local_fonts"]:
        fields["local_fonts"] = payload.local_fonts
    for key in ("name", "group_id", "proxy_id", "remark", "os", "headless"):
        if key in provided:
            value = provided[key]
            if key in ("group_id", "proxy_id") and value == "":
                value = None
            fields[key] = value
    if "tags" in provided and payload.tags is not None:
        fields["tags"] = json.dumps(payload.tags, ensure_ascii=False)
    if payload.fingerprint is not None:
        if row["status"] == "running":
            raise HTTPException(
                409, detail={"code": "PROFILE_RUNNING", "message": "运行中的环境不能修改指纹"}
            )
        fields["fingerprint"] = json.dumps(payload.fingerprint, ensure_ascii=False)
    elif "os" in fields and fields["os"] != row["os"]:
        # 换了系统就必须换预设，否则 UA / 平台与系统对不上
        fingerprint = json.loads(row["fingerprint"] or "{}")
        preset = get_random_preset(os=fields["os"])
        if preset:
            fingerprint["preset"] = preset
            fields["fingerprint"] = json.dumps(fingerprint, ensure_ascii=False)
    if not fields:
        return {"data": serialize_profile(row)}

    fields["updated_at"] = db.now()
    assignments = ", ".join(f"{key}=?" for key in fields)
    try:
        with db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if "local_fonts" in fields and launcher._active_session(conn, profile_id):
                raise HTTPException(409, detail={"code": "PROFILE_RUNNING", "message": "请先关闭环境再修改字体访问设置"})
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
    row = camoufox_profile(profile_id)
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
    return {"data": serialize_profile(camoufox_profile(profile_id))}


@app.delete("/api/v1/profiles/{profile_id}")
def delete_profile(profile_id: str, delete_data: bool = False):
    with db.session() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status, user_data_dir FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "PROFILE_NOT_FOUND", "message": "环境不存在"})
        if row["status"] == "running":
            raise HTTPException(409, detail={"code": "PROFILE_RUNNING", "message": "请先关闭环境"})
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
    duplicated_fp = _fingerprint_with_preset(source_fp, row["os"]) if row["engine"] == "camoufox" else {}
    with db.session() as conn:
        conn.execute(
            "INSERT INTO profiles(id, name, group_id, proxy_id, tags, remark, os, "
            "fingerprint, user_data_dir, headless, status, created_at, updated_at, engine, local_fonts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?)",
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
                row["engine"],
                row["local_fonts"],
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
            conn.execute("BEGIN IMMEDIATE")
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


def _runtime_checks(preview: dict, values: dict) -> list[dict]:
    fields = (
        ("user_agent", "User-Agent"), ("platform", "平台"), ("hardware_concurrency", "CPU 核数"),
        ("screen", "屏幕尺寸"), ("color_depth", "色深"), ("webgl_vendor", "WebGL 厂商"),
        ("webgl_renderer", "WebGL 渲染器"),
    )
    checks = []
    for key, name in fields:
        expected, observed = preview.get(key), values.get(key)
        if expected in (None, "") or observed in (None, ""):
            status, ok = "unverified", None
        elif str(expected) == str(observed):
            status, ok = "match", True
        else:
            status, ok = "mismatch", False
        checks.append({
            "name": name, "source": "runtime", "status": status, "ok": ok,
            "expected": expected, "observed": observed,
            "detail": f"预设 {expected if expected not in (None, '') else '—'} / "
                      f"实测 {observed if observed not in (None, '') else '未取得'}",
        })
    return checks


@app.post("/api/v1/profiles/{profile_id}/verify")
def verify_profile(profile_id: str):
    row = camoufox_profile(profile_id)
    fingerprint = json.loads(row["fingerprint"] or "{}")
    preview = fingerprint_preview(fingerprint, row["os"])
    sample = observation.read_current(row)
    checks = _runtime_checks(preview or {}, sample["values"] if sample else {})
    warnings: list[str] = []

    if preview:
        ua_os = _ua_os(preview.get("user_agent") or "")
        ok = ua_os == row["os"]
        checks.append({"name": "系统 / UA", "source": "configuration", "ok": ok,
                       "detail": f"环境配置 {row['os']}，预设 UA 的系统为 {ua_os or '未知'}"})
        if not ok:
            warnings.append("预设 UA 与配置的操作系统不一致")
    else:
        checks.append({"name": "指纹", "source": "configuration", "ok": False, "detail": "尚未生成指纹预设"})
        warnings.append("尚未生成指纹预设")

    proxy_result = None
    if not row["proxy_id"]:
        checks.append({"name": "代理", "source": "configuration", "ok": False, "detail": "环境未绑定代理"})
        warnings.append("环境未绑定代理")
    else:
        with db.session() as conn:
            proxy = conn.execute("SELECT * FROM proxies WHERE id=?", (row["proxy_id"],)).fetchone()
        if proxy is None:
            checks.append({"name": "代理", "source": "configuration", "ok": False, "detail": "代理记录不存在"})
            warnings.append("绑定的代理不存在")
        else:
            proxy_result = proxy_checker.check_and_save(dict(proxy))
            ok = proxy_result["status"] not in ("failed", "mismatch")
            detail = (
                f"{proxy_result.get('exit_ip')} · {proxy_result.get('country') or '?'} · "
                f"{proxy_result.get('latency_ms') or '?'}ms"
                if ok else f"不可用：{proxy_result.get('error') or proxy_result['status']}"
            )
            checks.append({"name": "代理连通", "source": "proxy", "ok": ok, "detail": detail})
            if not ok:
                warnings.append(f"代理不可用：{proxy_result.get('error') or proxy_result['status']}")

            explicit_tz = (fingerprint.get("overrides") or {}).get("timezone") or fingerprint.get("timezone")
            proxy_tz = proxy_result.get("timezone")
            if explicit_tz and proxy_tz and explicit_tz != proxy_tz:
                checks.append({"name": "时区", "source": "proxy", "ok": False,
                               "detail": f"手动 {explicit_tz} / 代理 {proxy_tz}"})
                warnings.append("手动设置的时区与代理出口不一致")
            elif proxy_tz:
                checks.append({"name": "时区", "source": "proxy", "ok": True, "detail": f"代理出口时区 {proxy_tz}"})

            if proxy_result.get("locale"):
                checks.append({"name": "国家 / 语言", "source": "proxy", "ok": True,
                               "detail": f"{proxy_result.get('country') or '?'} · {proxy_result['locale']}"})

    ok = all(check["ok"] for check in checks)
    return {
        "data": {
            "profile_id": profile_id,
            "ok": ok,
            "measured_at": sample["measured_at"] if sample else None,
            "checks": checks,
            "warnings": warnings,
            "preview": preview,
            "proxy": proxy_result,
        }
    }


@app.post("/api/v1/profiles/{profile_id}/self-test")
def self_test(profile_id: str):
    row = camoufox_profile(profile_id)
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
    fields = payload.model_dump(exclude_unset=True)
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


@app.post("/api/v1/proxies/bulk")
def bulk_create_proxies(payload: dict[str, Any]):
    """批量导入代理：[{name, protocol, host, port, username, password}]"""
    items = payload.get("proxies") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(422, detail={"code": "INVALID_PAYLOAD", "message": "proxies 不能为空"})
    created, skipped = [], []
    ts = db.now()
    with db.session() as conn:
        existing = {
            (row["protocol"], row["host"], row["port"])
            for row in conn.execute("SELECT protocol, host, port FROM proxies")
        }
        for index, item in enumerate(items[:500], start=1):
            protocol = str(item.get("protocol") or "http").lower()
            host = str(item.get("host") or "").strip()
            try:
                port = int(item.get("port"))
            except (TypeError, ValueError):
                skipped.append({"line": index, "reason": "端口无效"})
                continue
            if protocol not in ("http", "https", "socks5") or not host or not 0 < port < 65536:
                skipped.append({"line": index, "reason": "格式无效"})
                continue
            if (protocol, host, port) in existing:
                skipped.append({"line": index, "reason": "已存在"})
                continue
            proxy_id = new_id("px")
            name = str(item.get("name") or f"{host}:{port}")
            conn.execute(
                "INSERT INTO proxies(id, name, protocol, host, port, username, password, "
                "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'unchecked', ?, ?)",
                (proxy_id, name, protocol, host, port,
                 item.get("username") or None, item.get("password") or None, ts, ts),
            )
            existing.add((protocol, host, port))
            created.append({"id": proxy_id, "name": name})
    if created:
        db.log("proxy.bulk_create", None, {"count": len(created)})
    return {"data": {"created": len(created), "items": created, "skipped": skipped}}


@app.patch("/api/v1/proxies/{proxy_id}")
def update_proxy(proxy_id: str, payload: ProxyUpdate):
    fields = payload.model_dump(exclude_unset=True)
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


# ---------------------------------------------------------------------------
# Cookie
# ---------------------------------------------------------------------------


def _require_stopped(row: sqlite3.Row, action: str = "修改") -> None:
    if row["status"] == "running":
        raise HTTPException(
            409, detail={"code": "PROFILE_RUNNING", "message": f"请先关闭环境再{action} Cookie"}
        )


@app.get("/api/v1/profiles/{profile_id}/cookies")
def list_cookies(profile_id: str, domain: str = ""):
    row = camoufox_profile(profile_id)
    try:
        items = cookie_store.read_cookies(row["user_data_dir"], domain)
    except FileNotFoundError:
        items = []
    return {"data": {"cookies": items, "total": len(items)}}


@app.get("/api/v1/profiles/{profile_id}/cookies/export")
def export_cookies(profile_id: str, format: str = "json"):
    row = camoufox_profile(profile_id)
    try:
        items = cookie_store.read_cookies(row["user_data_dir"])
    except FileNotFoundError:
        items = []
    safe_name = re.sub(r"[^\w\-.]+", "_", row["name"])[:40] or profile_id
    if format == "netscape":
        body = cookie_store.to_netscape(items)
        media, suffix = "text/plain", "txt"
    else:
        body = json.dumps({"cookies": items, "exported_at": db.now()}, ensure_ascii=False, indent=2)
        media, suffix = "application/json", "json"
    return Response(
        content=body,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="cookies-{safe_name}.{suffix}"'
        },
    )


@app.put("/api/v1/profiles/{profile_id}/cookies")
def import_cookies(profile_id: str, payload: CookiePayload):
    row = camoufox_profile(profile_id)
    _require_stopped(row, "导入")
    raw = payload.cookies if payload.cookies is not None else payload.text
    if raw is None:
        raise HTTPException(422, detail={"code": "EMPTY_PAYLOAD", "message": "没有 Cookie 内容"})
    items, source = cookie_store.parse_payload(raw)
    if not items:
        raise HTTPException(422, detail={"code": "NO_COOKIES", "message": "没有解析到有效 Cookie"})
    mode = "replace" if payload.mode == "replace" else "merge"
    result = cookie_store.write_cookies(row["user_data_dir"], items, mode)
    db.log("profile.cookies_import", profile_id, {"count": result["applied"], "mode": mode, "source": source})
    return {"data": {**result, "parsed": len(items), "source": source}}


@app.post("/api/v1/profiles/{profile_id}/cookies/copy-from/{source_id}")
def copy_cookies(profile_id: str, source_id: str, mode: str = "merge"):
    target = camoufox_profile(profile_id)
    source = camoufox_profile(source_id)
    _require_stopped(target, "导入")
    try:
        items = cookie_store.read_cookies(source["user_data_dir"])
    except FileNotFoundError:
        items = []
    if not items:
        raise HTTPException(422, detail={"code": "NO_COOKIES", "message": "源环境没有 Cookie"})
    result = cookie_store.write_cookies(target["user_data_dir"], items, mode)
    db.log("profile.cookies_copy", profile_id, {"from": source_id, "count": result["applied"]})
    return {"data": result}


@app.delete("/api/v1/profiles/{profile_id}/cookies")
def clear_cookies(profile_id: str):
    row = camoufox_profile(profile_id)
    _require_stopped(row, "清除")
    result = cookie_store.write_cookies(row["user_data_dir"], [], mode="replace")
    db.log("profile.cookies_clear", profile_id, {})
    return {"data": result}


# ---------------------------------------------------------------------------
# 扩展
# ---------------------------------------------------------------------------


def _serialize_extension(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "version": row["version"],
        "description": row["description"],
        "filename": row["filename"],
        "size": row["size"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "icon": extensions.icon_data_url(row["id"]),
        "profiles": row["profiles"] if "profiles" in row.keys() else 0,
    }


@app.get("/api/v1/extensions")
def list_extensions():
    with db.session() as conn:
        rows = conn.execute(
            "SELECT e.*, (SELECT COUNT(*) FROM profile_extensions pe WHERE pe.ext_id = e.id) AS profiles "
            "FROM extensions e ORDER BY e.created_at DESC"
        ).fetchall()
    return {"data": [_serialize_extension(row) for row in rows]}


@app.post("/api/v1/extensions", status_code=201)
def upload_extension(payload: dict[str, Any]):
    filename = str(payload.get("filename") or "extension.zip")
    content = payload.get("content") or payload.get("content_base64")
    if not content:
        raise HTTPException(422, detail={"code": "EMPTY_FILE", "message": "没有文件内容"})
    try:
        import base64

        archive = base64.b64decode(content.split(",")[-1])
    except Exception:  # noqa: BLE001
        raise HTTPException(422, detail={"code": "BAD_BASE64", "message": "文件内容无法解码"})
    ext_id = new_id("ext")
    try:
        info = extensions.install(ext_id, archive, filename)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "BAD_EXTENSION", "message": str(exc)})
    ts = db.now()
    with db.session() as conn:
        conn.execute(
            "INSERT INTO extensions(id, name, version, description, filename, size, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (ext_id, info["name"], info["version"], info["description"], filename,
             extensions.size_of(ext_id), ts),
        )
    db.log("extension.upload", ext_id, {"name": info["name"]})
    row = None
    with db.session() as conn:
        row = conn.execute("SELECT * FROM extensions WHERE id=?", (ext_id,)).fetchone()
    return {"data": _serialize_extension(row)}


@app.patch("/api/v1/extensions/{ext_id}")
def update_extension(ext_id: str, payload: dict[str, Any]):
    if "enabled" in payload:
        with db.session() as conn:
            conn.execute(
                "UPDATE extensions SET enabled=? WHERE id=?",
                (1 if payload["enabled"] else 0, ext_id),
            )
    return {"data": {"id": ext_id}}


@app.delete("/api/v1/extensions/{ext_id}")
def delete_extension(ext_id: str):
    extensions.remove(ext_id)
    with db.session() as conn:
        conn.execute("DELETE FROM profile_extensions WHERE ext_id=?", (ext_id,))
        conn.execute("DELETE FROM extensions WHERE id=?", (ext_id,))
    db.log("extension.delete", ext_id, {})
    return {"data": {"id": ext_id, "deleted": True}}


@app.get("/api/v1/profiles/{profile_id}/extensions")
def profile_extensions(profile_id: str):
    camoufox_profile(profile_id)
    with db.session() as conn:
        ids = [
            row["ext_id"]
            for row in conn.execute(
                "SELECT ext_id FROM profile_extensions WHERE profile_id=?", (profile_id,)
            ).fetchall()
        ]
    return {"data": {"ids": ids}}


@app.put("/api/v1/profiles/{profile_id}/extensions")
def set_profile_extensions(profile_id: str, payload: dict[str, Any]):
    row = camoufox_profile(profile_id)
    if row["status"] == "running":
        raise HTTPException(
            409, detail={"code": "PROFILE_RUNNING", "message": "请先关闭环境再修改扩展"}
        )
    ids = [str(x) for x in (payload.get("ids") or [])]
    with db.session() as conn:
        valid = {
            item["id"]
            for item in conn.execute(
                f"SELECT id FROM extensions WHERE id IN ({','.join('?' for _ in ids)})", ids
            ).fetchall()
        } if ids else set()
        conn.execute("DELETE FROM profile_extensions WHERE profile_id=?", (profile_id,))
        for ext_id in valid:
            conn.execute(
                "INSERT INTO profile_extensions(profile_id, ext_id) VALUES (?, ?)",
                (profile_id, ext_id),
            )
    db.log("profile.extensions", profile_id, {"ids": sorted(valid)})
    return {"data": {"ids": sorted(valid)}}


# ---------------------------------------------------------------------------
# 批量任务进度
# ---------------------------------------------------------------------------


@app.get("/api/v1/jobs")
def list_jobs():
    return {"data": jobs.latest(8)}


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, detail={"code": "JOB_NOT_FOUND", "message": "任务不存在或已过期"})
    return {"data": job}


@app.post("/api/v1/profiles/batch-async")
def batch_async(payload: BatchRequest):
    """批量打开/关闭并返回 job_id，前端轮询进度。"""
    if payload.action not in ("launch", "stop"):
        raise HTTPException(422, detail={"code": "INVALID_ACTION", "message": "只有打开/关闭支持进度"})
    names = {}
    with db.session() as conn:
        rows = conn.execute(
            f"SELECT id, name FROM profiles WHERE id IN ({','.join('?' for _ in payload.ids)})",
            payload.ids,
        ).fetchall()
        names = {row["id"]: row["name"] for row in rows}
    items = [{"id": pid, "name": names.get(pid, pid)} for pid in payload.ids]

    def worker(profile_id: str) -> None:
        if payload.action == "launch":
            launcher.launch(profile_id)
        else:
            launcher.stop_profile(profile_id)

    workers = max(1, int(db.get_setting("batch_concurrency", 5)))
    job = jobs.create(payload.action, items, worker, workers=workers)
    return {"data": job}


# ---------------------------------------------------------------------------
# 定时任务
# ---------------------------------------------------------------------------


def _serialize_schedule(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["weekdays"] = json.loads(data.get("weekdays") or "[]")
    data["target_ids"] = json.loads(data.get("target_ids") or "[]")
    data.setdefault("mode", "daily")
    data.setdefault("interval_minutes", 60)
    data["enabled"] = bool(data["enabled"])
    try:
        data["last_result"] = json.loads(data["last_result"]) if data.get("last_result") else None
    except json.JSONDecodeError:
        data["last_result"] = None
    data["next_run_at"] = scheduler.next_run(data) if data["enabled"] else None
    return data


def _validate_schedule(fields: dict[str, Any]) -> None:
    if "action" in fields and fields["action"] not in ("launch", "stop"):
        raise HTTPException(422, detail={"code": "INVALID_ACTION", "message": "动作必须是 launch 或 stop"})
    if "time_of_day" in fields:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(fields["time_of_day"])):
            raise HTTPException(422, detail={"code": "INVALID_TIME", "message": "时间格式应为 HH:MM"})
    if "target_type" in fields and fields["target_type"] not in ("all", "group", "ids"):
        raise HTTPException(422, detail={"code": "INVALID_TARGET", "message": "目标类型无效"})
    if "mode" in fields and fields["mode"] not in ("daily", "interval"):
        raise HTTPException(422, detail={"code": "INVALID_MODE", "message": "模式必须是 daily 或 interval"})
    if "interval_minutes" in fields:
        try:
            minutes = int(fields["interval_minutes"])
        except (TypeError, ValueError):
            raise HTTPException(422, detail={"code": "INVALID_INTERVAL", "message": "间隔必须是数字"})
        if not 1 <= minutes <= 1440:
            raise HTTPException(422, detail={"code": "INVALID_INTERVAL", "message": "间隔范围 1–1440 分钟"})


@app.get("/api/v1/schedules")
def list_schedules():
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM schedules ORDER BY time_of_day, created_at").fetchall()
    return {"data": [_serialize_schedule(row) for row in rows]}


@app.post("/api/v1/schedules", status_code=201)
def create_schedule(payload: ScheduleCreate):
    fields = payload.model_dump()
    _validate_schedule(fields)
    schedule_id = new_id("sch")
    ts = db.now()
    with db.session() as conn:
        conn.execute(
            "INSERT INTO schedules(id, name, action, mode, interval_minutes, time_of_day, weekdays, "
            "target_type, target_ids, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                schedule_id,
                fields["name"],
                fields["action"],
                fields.get("mode", "daily"),
                int(fields.get("interval_minutes", 60)),
                fields["time_of_day"],
                json.dumps(sorted(set(fields["weekdays"] or [1, 2, 3, 4, 5, 6, 7]))),
                fields["target_type"],
                json.dumps(fields["target_ids"] or []),
                1 if fields["enabled"] else 0,
                ts,
                ts,
            ),
        )
    db.log("schedule.create", schedule_id, {"name": fields["name"]})
    with db.session() as conn:
        row = conn.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
    return {"data": _serialize_schedule(row)}


@app.patch("/api/v1/schedules/{schedule_id}")
def update_schedule(schedule_id: str, payload: ScheduleUpdate):
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    _validate_schedule(fields)
    if not fields:
        raise HTTPException(422, detail={"code": "EMPTY_PAYLOAD", "message": "没有要更新的字段"})
    if "weekdays" in fields:
        fields["weekdays"] = json.dumps(sorted(set(fields["weekdays"])))
    if "target_ids" in fields:
        fields["target_ids"] = json.dumps(fields["target_ids"])
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0
    fields["updated_at"] = db.now()
    assignments = ", ".join(f"{key}=?" for key in fields)
    with db.session() as conn:
        conn.execute(
            f"UPDATE schedules SET {assignments} WHERE id=?", (*fields.values(), schedule_id)
        )
        row = conn.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "SCHEDULE_NOT_FOUND", "message": "定时任务不存在"})
    return {"data": _serialize_schedule(row)}


@app.delete("/api/v1/schedules/{schedule_id}")
def delete_schedule(schedule_id: str):
    with db.session() as conn:
        conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
    db.log("schedule.delete", schedule_id, {})
    return {"data": {"id": schedule_id, "deleted": True}}


@app.post("/api/v1/schedules/{schedule_id}/run")
def run_schedule_now(schedule_id: str):
    with db.session() as conn:
        row = conn.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "SCHEDULE_NOT_FOUND", "message": "定时任务不存在"})
    schedule = _serialize_schedule(row)
    result = scheduler.run_schedule(schedule)
    return {"data": result}


# ---------------------------------------------------------------------------
# 环境同步
# ---------------------------------------------------------------------------


@app.get("/api/v1/profiles/{profile_id}/state-size")
def profile_state_size(profile_id: str):
    row = camoufox_profile(profile_id)
    return {"data": state_sync.size_of(row["user_data_dir"])}


@app.post("/api/v1/profiles/{profile_id}/sync-from/{source_id}")
def sync_profile_state(profile_id: str, source_id: str, parts: str = "cookies,storage"):
    """把源环境的 Cookie / localStorage 覆盖到目标环境（两个都要先关闭）。"""
    target = camoufox_profile(profile_id)
    source = camoufox_profile(source_id)
    if profile_id == source_id:
        raise HTTPException(422, detail={"code": "SAME_PROFILE", "message": "不能从自己同步"})
    for row, label in ((target, "目标"), (source, "源")):
        if row["status"] == "running":
            raise HTTPException(
                409,
                detail={"code": "PROFILE_RUNNING", "message": f"请先关闭{label}环境「{row['name']}」"},
            )
    wanted = [part.strip() for part in (parts or "").split(",") if part.strip()]
    if not wanted:
        wanted = ["cookies"]
    result = state_sync.sync(target["user_data_dir"], source["user_data_dir"], wanted)
    db.log("profile.sync", profile_id, {"from": source_id, **result})
    return {"data": result}


# ---------------------------------------------------------------------------
# 备份 / 恢复
# ---------------------------------------------------------------------------


@app.get("/api/v1/backup")
def download_backup(include_profiles: bool = False):
    # 只备份数据库时可以用 SQLite 在线快照，不必关闭环境；
    # 带上 Cookie / localStorage 时才要求全部关闭。
    if include_profiles:
        with db.session() as conn:
            running = conn.execute(
                "SELECT COUNT(*) AS n FROM profiles WHERE status='running'"
            ).fetchone()["n"]
        if running:
            raise HTTPException(
                409,
                detail={
                    "code": "PROFILES_RUNNING",
                    "message": f"还有 {running} 个环境在运行，请先关闭再导出登录态",
                },
            )
    path = backup_store.create(include_profiles=include_profiles)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    db.log("backup.create", None, {"include_profiles": include_profiles})
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"moltskin-backup-{stamp}.zip",
        background=BackgroundTask(lambda: shutil.rmtree(path.parent, ignore_errors=True)),
    )


@app.post("/api/v1/backup/restore")
def restore_backup(file: UploadFile = File(...)):
    with db.session() as conn:
        running = conn.execute("SELECT COUNT(*) AS n FROM profiles WHERE status='running'").fetchone()["n"]
    if running:
        raise HTTPException(
            409, detail={"code": "PROFILES_RUNNING", "message": f"还有 {running} 个环境在运行，请先关闭"}
        )
    archive = file.file.read()
    if len(archive) > 2 * 1024 * 1024 * 1024:
        raise HTTPException(413, detail={"code": "TOO_LARGE", "message": "备份文件过大"})
    try:
        result = backup_store.restore(archive)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "BAD_BACKUP", "message": str(exc)})
    db.init_db()
    ensure_presets()
    db.log("backup.restore", None, {"profiles": result["profiles"]})
    return {"data": result}
