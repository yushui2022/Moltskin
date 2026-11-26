"""Launcher：环境进程的启动、停止、状态回收。"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread

from camoufox.fingerprints import get_random_preset

from . import chromium, config, db, extensions
from .proxy_settings import chromium_proxy_server

ACTIVE_STATES = ("starting", "running", "stopping")


class LaunchError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _read_status(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _pid_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or not 0 < pid <= 2**31 - 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


SESSION_QUERY = (
    "SELECT s.*, p.engine, p.user_data_dir FROM sessions s "
    "JOIN profiles p ON p.id=s.profile_id "
)


def _session_alive(session) -> bool:
    if not _pid_alive(session["pid"]):
        return False
    if session["engine"] == "chromium":
        return chromium.matches_process(Path(session["user_data_dir"]), session["pid"])
    # A runner is launched with a unique plan per session. A live PID alone
    # is not evidence that the process still belongs to this session.
    expected = f" -m moltskin.services.core.runner --plan {config.RUN_DIR / (session['id'] + '.plan.json')}"
    try:
        proc = subprocess.run(["ps", "-ww", "-p", str(session["pid"]), "-o", "command="],
                              capture_output=True, text=True, errors="replace", timeout=3)
    except (OSError, subprocess.SubprocessError) as exc:
        raise LaunchError("PROCESS_CHECK_FAILED", "无法确认浏览器进程归属，请稍后重试", 503) from exc
    return proc.returncode == 0 and proc.stdout.rstrip("\n").endswith(expected)


def _active_session(conn, profile_id: str):
    placeholders = ",".join("?" for _ in ACTIVE_STATES)
    return conn.execute(
        SESSION_QUERY + f"WHERE s.profile_id=? AND s.state IN ({placeholders}) "
        "ORDER BY started_at DESC LIMIT 1",
        (profile_id, *ACTIVE_STATES),
    ).fetchone()


def _finish_session(session_id: str, profile_id: str, state: str, code: str | None = None,
                    message: str | None = None, *, observed_exit: bool = False) -> None:
    with db.session() as conn:
        changed = conn.execute(
            "UPDATE sessions SET state=?, error_code=?, error_message=?, ended_at=COALESCE(ended_at, ?) "
            "WHERE id=? AND (state IN ('starting','running','stopping') OR (? AND error_code='EXIT_UNCONFIRMED'))",
            (state, code, message, db.now(), session_id, observed_exit),
        )
        if not changed.rowcount:
            return
        profile_status = "ready" if state in ("stopped", "killed") else "crashed"
        conn.execute(
            "UPDATE profiles SET status=?, updated_at=? WHERE id=? AND last_session_id=?",
            (profile_status, db.now(), profile_id, session_id),
        )


def _recover_exit(session, code: str, message: str) -> None:
    sid, profile_id = session["id"], session["profile_id"]
    if session["engine"] == "camoufox" and re.fullmatch(r"ss_[a-f0-9]{12}", sid):
        report = _read_status(config.RUN_DIR / f"{sid}.json") or {}
        if report.get("state") == "failed":
            _finish_session(sid, profile_id, "failed", report.get("error_code") or "LAUNCH_FAILED",
                            report.get("error_message"))
            return
        if report.get("state") == "stopped":
            _finish_session(sid, profile_id, "stopped")
            return
    if session["state"] == "stopping":
        _finish_session(sid, profile_id, "stopped")
    elif session["engine"] == "chromium":
        # A restarted service cannot wait() on a browser started by its predecessor.
        _finish_session(sid, profile_id, "stopped", "EXIT_UNCONFIRMED", "浏览器进程已退出，未取得退出码")
    else:
        _finish_session(sid, profile_id, "crashed", code, message)


def _monitor(session_id: str, profile_id: str, proc: subprocess.Popen,
             status_path: Path | None) -> None:
    code = proc.wait()
    final = _read_status(status_path) if status_path else None
    if final and final.get("state") == "failed":
        _finish_session(session_id, profile_id, "failed", final.get("error_code", "LAUNCH_FAILED"),
                        final.get("error_message"))
    else:
        state = "stopped" if code in (0, -signal.SIGTERM) else "crashed"
        if code in (-signal.SIGKILL, -signal.SIGINT):
            with db.session() as conn:
                session = conn.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
            if session and session["state"] == "stopping":
                state = "killed" if code == -signal.SIGKILL else "stopped"
        _finish_session(session_id, profile_id, state, None,
                        f"进程退出码 {code}" if state == "crashed" else None, observed_exit=True)


def _camoufox_command(profile, proxy, session_id, headless, status_path):
    profile_id = profile["id"]
    fingerprint = json.loads(profile["fingerprint"] or "{}")
    if not fingerprint.get("preset"):
        preset = get_random_preset(os=profile["os"])
        if preset:
            fingerprint["preset"] = preset
            with db.session() as conn:
                conn.execute(
                    "UPDATE profiles SET fingerprint=?, updated_at=? WHERE id=?",
                    (json.dumps(fingerprint, ensure_ascii=False), db.now(), profile_id),
                )

    with db.session() as conn:
        ext_ids = [
            row["ext_id"]
            for row in conn.execute(
                "SELECT ext_id FROM profile_extensions WHERE profile_id=?", (profile_id,)
            ).fetchall()
        ]
    addon_paths = extensions.paths_for(ext_ids)
    first_open = not (profile["open_count"] or 0)
    startup_page = profile["startup_page"]
    manager_url = None
    if config.OPEN_MANAGER:
        if startup_page == "dashboard":
            manager_url = config.local_dashboard_url()
        elif startup_page == "default" and db.get_setting("open_manager_on_launch", True):
            manager_url = config.MANAGER_URL
    builtin = extensions.builtin_manager_button(open_sidebar=first_open and startup_page == "default")
    if builtin:
        addon_paths.append(builtin)

    plan = {
        "profile_id": profile_id,
        "session_id": session_id,
        "user_data_dir": profile["user_data_dir"],
        "os": profile["os"],
        "headless": headless,
        "fingerprint": fingerprint,
        "fingerprint_preset": fingerprint.get("preset"),
        "addons": addon_paths,
        "proxy": proxy,
        "manager_url": manager_url,
        "startup_page": startup_page,
        "block_images": bool(profile["block_images"]),
        "status_file": str(status_path),
    }
    plan_path = config.RUN_DIR / f"{session_id}.plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    plan_path.chmod(0o600)

    return [sys.executable, "-m", "moltskin.services.core.runner", "--plan", str(plan_path)]


def _chromium_directory(profile) -> Path:
    try:
        return config.stored_profile_directory(profile["id"], profile["user_data_dir"])
    except (ValueError, OSError, RuntimeError) as exc:
        raise LaunchError("INVALID_PROFILE_DIRECTORY", "环境目录异常，请检查数据目录。") from exc


def launch(profile_id: str, actor: str = "local") -> dict:
    with db.session() as conn:
        profile = conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if profile is None:
            raise LaunchError("PROFILE_NOT_FOUND", "环境不存在", 404)
        is_chromium = profile["engine"] == "chromium"
        if is_chromium:
            profile_dir = _chromium_directory(profile)

        session = _active_session(conn, profile_id)
        if session:
            if _session_alive(session):
                raise LaunchError("PROFILE_LOCKED", "该环境正在运行", 409)
            _recover_exit(session, "STALE_SESSION", "上次会话已失效")

        proxy = None
        if profile["proxy_id"]:
            row = conn.execute("SELECT * FROM proxies WHERE id=?", (profile["proxy_id"],)).fetchone()
            if row is None:
                raise LaunchError("PROXY_FAILED", "绑定的代理不存在", 422)
            # 坏代理一定要拦：带着坏代理打开环境会直接暴露本机 IP
            if row["status"] in ("failed", "mismatch"):
                raise LaunchError("PROXY_FAILED", "代理不可用，请先重新检测", 422)
            if db.get_setting("require_proxy_ok", False) and row["status"] not in ("ok", "slow"):
                raise LaunchError(
                    "PROXY_UNCHECKED", "严格模式下代理必须先检测通过", 422
                )
            proxy = dict(row)

    session_id = f"ss_{os.urandom(6).hex()}"
    status_path = config.RUN_DIR / f"{session_id}.json"
    log_path = config.RUN_DIR / f"{session_id}.log"

    headless = config.FORCE_HEADLESS or bool(profile["headless"]) or bool(db.get_setting("headless", False))
    browser_version = None
    process_env = None
    if is_chromium:
        try:
            proxy_server = chromium_proxy_server(proxy) if proxy else None
        except ValueError as exc:
            raise LaunchError("INVALID_CHROMIUM_PROXY", str(exc), 422) from exc
        try:
            process_env = chromium.launch_environment(profile["browser_timezone"])
            open_workbench = (not headless and config.OPEN_MANAGER and profile["startup_page"] == "default"
                              and db.get_setting("open_manager_on_launch", True))
            command, browser_version = chromium.prepare_launch(
                profile_dir, profile["local_fonts"], headless,
                chromium_version=profile["chromium_version"],
                chromium_user_agent=profile["chromium_user_agent"],
                proxy_server=proxy_server,
                open_dashboard=config.OPEN_MANAGER and profile["startup_page"] == "dashboard",
                workbench_session=session_id if open_workbench else None,
                block_images=bool(profile["block_images"]),
                chromium_js_heap_mb=profile["chromium_js_heap_mb"],
                chromium_mute_audio=bool(profile["chromium_mute_audio"]),
                chromium_renderer_limit=profile["chromium_renderer_limit"],
                media_capture=profile["media_capture"], geolocation=profile["geolocation"],
                hardware_reporting=profile["hardware_reporting"],
                canvas_readback=profile["canvas_readback"], webgl_debug_info=profile["webgl_debug_info"],
                browser_language=profile["browser_language"], browser_window_size=profile["browser_window_size"],
            )
        except BlockingIOError as exc:
            raise LaunchError("PROFILE_LOCKED", str(exc)) from exc
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise LaunchError("CHROMIUM_UNAVAILABLE", f"无法启动 Moltskin Chromium：{exc}", 503) from exc
        status_path = None
    else:
        if profile["media_capture"] != "standard":
            raise LaunchError("ENGINE_OPTIONS", "阻止摄像头与麦克风访问仅支持 Chromium 环境", 422)
        if profile["geolocation"] != "standard":
            raise LaunchError("ENGINE_OPTIONS", "阻止地理位置访问仅支持 Chromium 环境", 422)
        if profile["hardware_reporting"] != "standard":
            raise LaunchError("ENGINE_OPTIONS", "硬件信息限制仅支持 Chromium 环境", 422)
        if profile["canvas_readback"] != "standard" or profile["webgl_debug_info"] != "standard":
            raise LaunchError("ENGINE_OPTIONS", "此图形隐私设置仅支持 Chromium 环境", 422)
        command = _camoufox_command(profile, proxy, session_id, headless, status_path)

    # Serialize reservation and PID publication across API, batch, and scheduled launches.
    with db.session() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if current is None:
            raise LaunchError("PROFILE_NOT_FOUND", "环境不存在", 404)
        if _active_session(conn, profile_id):
            raise LaunchError("PROFILE_LOCKED", "该环境正在启动或运行")
        launch_fields = ("engine", "chromium_version", "local_fonts", "media_capture", "geolocation", "headless",
                         "chromium_user_agent", "chromium_js_heap_mb", "chromium_mute_audio", "chromium_renderer_limit",
                         "hardware_reporting", "canvas_readback", "webgl_debug_info", "proxy_id", "startup_page", "block_images", "browser_language",
                         "browser_timezone", "browser_window_size")
        if any(current[k] != profile[k] for k in launch_fields):
            raise LaunchError("PROFILE_CHANGED", "环境设置已更新，请重新启动")
        if is_chromium and proxy:
            endpoint = conn.execute("SELECT * FROM proxies WHERE id=?", (current["proxy_id"],)).fetchone()
            if not endpoint or any(endpoint[k] != proxy[k] for k in
                                   ("host", "port", "protocol", "username", "password", "status")):
                raise LaunchError("PROFILE_CHANGED", "指定代理已更新，请重新启动")
        if is_chromium and _chromium_directory(current) != profile_dir:
            raise LaunchError("INVALID_PROFILE_DIRECTORY", "环境目录已变更，请检查数据目录。")

        if is_chromium:
            try:
                chromium.check_profile_version(profile_dir, browser_version, current["browser_version"])
            except (ValueError, OSError) as exc:
                raise LaunchError("CHROMIUM_PROFILE_VERSION", str(exc), 409) from exc

        active_count = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE state IN ('starting','running','stopping')"
        ).fetchone()["n"]
        max_concurrent = int(db.get_setting("max_concurrent", 10))
        if active_count >= max_concurrent:
            raise LaunchError(
                "MAX_CONCURRENCY",
                f"已达到并发上限（{max_concurrent}），请先关闭其他环境",
                429,
            )

        daily_limit = int(db.get_setting("daily_open_limit", 20000))
        if daily_limit > 0:
            opened_today = db.count_launches_since(conn, db.today_start_utc())
            if opened_today >= daily_limit:
                raise LaunchError(
                    "DAILY_LIMIT",
                    f"今日打开次数已达上限（{daily_limit}），可在设置中调整",
                    429,
                )

        conn.execute(
            "INSERT INTO sessions(id, profile_id, state, started_at, browser_version) VALUES (?, ?, 'starting', ?, ?)",
            (session_id, profile_id, db.now(), browser_version),
        )
        conn.execute(
            "UPDATE profiles SET status='running', last_session_id=?, last_opened_at=?, "
            "open_count=COALESCE(open_count, 0) + 1, updated_at=? WHERE id=?",
            (session_id, db.now(), db.now(), profile_id),
        )
        try:
            if is_chromium:
                profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            with log_path.open("w", encoding="utf-8") as log_handle:
                proc = subprocess.Popen(command, cwd=config.ROOT, stdout=log_handle,
                                        stderr=subprocess.STDOUT, start_new_session=True, env=process_env)
        except OSError as exc:
            raise LaunchError("LAUNCH_FAILED", f"无法启动浏览器进程：{exc}", 500) from exc
        conn.execute("UPDATE sessions SET pid=? WHERE id=?", (proc.pid, session_id))

    timeout = float(db.get_setting("launch_timeout_sec", 30))
    deadline = time.time() + timeout
    state = None
    info = None
    while time.time() < deadline:
        if status_path is not None:
            info = _read_status(status_path)
            state = info.get("state") if info else None
        elif chromium.owns_lock(profile_dir, proc.pid):
            state = "running"
        if state in ("running", "failed") or proc.poll() is not None:
            break
        time.sleep(0.1)

    if state == "failed" or proc.poll() is not None:
        info = info or {}
        code = info.get("error_code", "LAUNCH_FAILED")
        message = info.get("error_message") or f"浏览器在启动完成前退出（{proc.returncode}）"
        with db.session() as conn:
            row = conn.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row and row["state"] in ("stopping", "stopped", "killed"):
            _finish_session(session_id, profile_id, "stopped")
            raise LaunchError("LAUNCH_CANCELLED", "启动已取消")
        _finish_session(session_id, profile_id, "failed", code, message)
        raise LaunchError(code, message, 409 if code == "PROFILE_LOCKED" else 500)

    if state != "running":
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        _finish_session(session_id, profile_id, "failed", "LAUNCH_TIMEOUT", "启动超时")
        raise LaunchError("LAUNCH_TIMEOUT", f"启动超时（{timeout} 秒）", 504)

    with db.session() as conn:
        if not is_chromium and info:
            browser_version = info.get("browser_version")
        started = conn.execute(
            "UPDATE sessions SET state='running', browser_version=? WHERE id=? AND state='starting'",
            (browser_version, session_id),
        ).rowcount
        if started:
            conn.execute("UPDATE profiles SET browser_version=? WHERE id=? AND last_session_id=?",
                         (browser_version, profile_id, session_id))

    Thread(
        target=_monitor,
        args=(session_id, profile_id, proc, status_path),
        daemon=True,
    ).start()

    if not started:
        raise LaunchError("LAUNCH_CANCELLED", "启动已取消")
    db.log("profile.launch", profile_id, {"session_id": session_id}, actor=actor)
    return {"session_id": session_id, "profile_id": profile_id, "state": "running",
            "browser_version": browser_version}


def stop_profile(profile_id: str, actor: str = "local") -> dict:
    """按环境关闭：找到该环境当前活跃的会话再停。"""
    with db.session() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE profile_id=? AND state IN ('starting','running','stopping') "
            "ORDER BY started_at DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
    if row is None:
        raise LaunchError("NOT_RUNNING", "该环境未在运行", 409)
    return stop(row["id"], actor=actor)


def stop(session_id: str, actor: str = "local") -> dict:
    with db.session() as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = conn.execute(
            SESSION_QUERY + "WHERE s.id=?", (session_id,),
        ).fetchone()
        if session is None:
            raise LaunchError("SESSION_NOT_FOUND", "会话不存在", 404)
        if session["state"] not in ACTIVE_STATES:
            return {"session_id": session_id, "state": session["state"]}
        pid = session["pid"]
        alive = _session_alive(session)
        if session["engine"] == "chromium" and alive:
            if not chromium.owns_lock(Path(session["user_data_dir"]), pid):
                if session["state"] == "starting":
                    raise LaunchError("PROFILE_STARTING", "浏览器正在初始化，请稍后再关闭")
                if session["state"] == "stopping":
                    return {"session_id": session_id, "state": "stopping"}
                # An old session PID can belong to an unrelated process after restart.
                raise LaunchError("PROCESS_CHANGED", "进程与当前环境不匹配，未发送关闭信号")
        if alive:
            conn.execute("UPDATE sessions SET state='stopping' WHERE id=?", (session_id,))

    forced = False
    if not alive:
        _recover_exit(session, "PROCESS_GONE", "进程已退出")
    elif _session_alive(session):
        try:
            # Chromium treats SIGTERM as an OS logout and skips some storage writes.
            # SIGINT uses its normal browser exit path on Linux and macOS.
            normal_exit = signal.SIGINT if session["engine"] == "chromium" else signal.SIGTERM
            os.kill(pid, normal_exit)
        except ProcessLookupError:
            pass
        timeout = float(db.get_setting("stop_timeout_sec", 8))
        deadline = time.monotonic() + timeout
        while _session_alive(session) and time.monotonic() < deadline:
            time.sleep(0.1)
        if _session_alive(session):
            if session["engine"] != "chromium" or chromium.owns_lock(Path(session["user_data_dir"]), pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                    forced = True
                except ProcessLookupError:
                    pass

    if alive and not _session_alive(session):
        _finish_session(session_id, session["profile_id"], "killed" if forced else "stopped")
    db.log("session.stop", session["profile_id"], {"session_id": session_id}, actor=actor)
    with db.session() as conn:
        result = conn.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
    return {"session_id": session_id, "state": result["state"] if result else "stopped"}


def status(profile_id: str) -> dict:
    with db.session() as conn:
        profile = conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if profile is None:
            raise LaunchError("PROFILE_NOT_FOUND", "环境不存在", 404)
        session = _active_session(conn, profile_id)
        if session and not _session_alive(session):
            _recover_exit(session, "PROCESS_GONE", "进程已退出")
            profile = conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
            session = None
    return {
        "profile_id": profile_id,
        "status": profile["status"],
        "session_id": session["id"] if session else None,
        "pid": session["pid"] if session else None,
        "browser_version": session["browser_version"] if session else None,
        "state": session["state"] if session else None,
    }


def cleanup_browser_orphans() -> int:
    """Kill Camoufox processes whose profile has no active session.

    Covers the case where the service or a runner was killed with SIGKILL and the
    Firefox process outlived it. Only processes using Moltskin's own profile
    directory are considered.
    """
    if os.name == "nt":
        return 0
    try:
        output = subprocess.check_output(["ps", "-ww", "-axo", "pid=,command="], text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return 0

    # A Playwright-launched main browser has both flags. Child processes may
    # also carry -profile, and data roots can have spaces.
    owned_profile = re.compile(r" -profile (" + re.escape(str(config.PROFILES_DIR)) +
                               r"/[^/\s]+) -juggler-pipe(?: |$)")
    cleaned = 0
    for line in output.splitlines():
        match = owned_profile.search(line)
        if not match:
            continue
        profile_dir = match.group(1)
        try:
            pid = int(line.strip().split()[0])
        except (ValueError, IndexError):
            continue
        with db.session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            profile = conn.execute("SELECT id FROM profiles WHERE engine='camoufox' AND user_data_dir=?",
                                   (profile_dir,)).fetchone()
            if not profile or _active_session(conn, profile["id"]):
                continue
            try:
                executable = subprocess.check_output(["ps", "-p", str(pid), "-o", "comm="], text=True, timeout=3)
                if Path(executable.strip()).name not in ("camoufox", "camoufox-bin"):
                    continue
                current = subprocess.check_output(["ps", "-ww", "-p", str(pid), "-o", "command="],
                                                  text=True, timeout=3)
                if current.strip() != line.strip().split(None, 1)[1]:
                    continue
                os.kill(pid, signal.SIGTERM)
                cleaned += 1
            except (OSError, subprocess.SubprocessError):
                continue
    return cleaned


def _reap_missing(code: str, message: str) -> int:
    with db.session() as conn:
        rows = conn.execute(SESSION_QUERY + "WHERE s.state IN ('starting','running','stopping')").fetchall()
    cleaned = 0
    for row in rows:
        if not _session_alive(row):
            _recover_exit(row, code, message)
            cleaned += 1
    return cleaned


def reap_stale() -> int:
    """回收失效会话；保留新启动的会话及其环境状态。"""
    return _reap_missing("PROCESS_GONE", "进程已退出")


def cleanup_orphans() -> int:
    """服务启动时清理上次异常退出留下的会话。"""
    return _reap_missing("ORPHAN", "服务重启时发现进程已不存在")
