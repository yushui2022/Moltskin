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

from . import config, db, extensions

ACTIVE_STATES = ("starting", "running", "stopping")


class LaunchError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _read_status(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def _active_session(conn, profile_id: str):
    placeholders = ",".join("?" for _ in ACTIVE_STATES)
    return conn.execute(
        f"SELECT * FROM sessions WHERE profile_id=? AND state IN ({placeholders}) "
        "ORDER BY started_at DESC LIMIT 1",
        (profile_id, *ACTIVE_STATES),
    ).fetchone()


def _finish_session(session_id: str, profile_id: str, state: str, code: str | None = None,
                    message: str | None = None) -> None:
    with db.session() as conn:
        conn.execute(
            "UPDATE sessions SET state=?, error_code=?, error_message=?, ended_at=? WHERE id=?",
            (state, code, message, db.now(), session_id),
        )
        profile_status = "ready" if state in ("stopped", "killed") else "crashed"
        conn.execute(
            "UPDATE profiles SET status=?, updated_at=? WHERE id=?",
            (profile_status, db.now(), profile_id),
        )


def _monitor(session_id: str, profile_id: str, proc: subprocess.Popen,
             status_path: Path, log_handle) -> None:
    code = proc.wait()
    # 给 runner 一点时间把最终状态写盘
    deadline = time.time() + 3
    final = None
    while time.time() < deadline:
        final = _read_status(status_path)
        if final and final.get("state") in ("stopped", "failed"):
            break
        time.sleep(0.1)

    if final and final.get("state") == "failed":
        _finish_session(
            session_id, profile_id, "failed",
            final.get("error_code", "LAUNCH_FAILED"),
            final.get("error_message"),
        )
        state = "crashed"
    else:
        state = "stopped" if code == 0 else "crashed"
        _finish_session(session_id, profile_id, state, None,
                        None if code == 0 else f"进程退出码 {code}")

    with db.session() as conn:
        conn.execute(
            "UPDATE profiles SET status=?, updated_at=? WHERE id=? AND status='running'",
            (state, db.now(), profile_id),
        )
    try:
        log_handle.close()
    except Exception:  # noqa: BLE001
        pass


def launch(profile_id: str, actor: str = "local") -> dict:
    with db.session() as conn:
        profile = conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if profile is None:
            raise LaunchError("PROFILE_NOT_FOUND", "环境不存在", 404)

        session = _active_session(conn, profile_id)
        if session:
            if _pid_alive(session["pid"]):
                raise LaunchError("PROFILE_LOCKED", "该环境正在运行", 409)
            _finish_session(session["id"], profile_id, "crashed", "STALE_SESSION",
                            "上次会话已失效")

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
            opened_today = conn.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE started_at >= ?",
                (db.today_start_utc(),),
            ).fetchone()["n"]
            if opened_today >= daily_limit:
                raise LaunchError(
                    "DAILY_LIMIT",
                    f"今日打开次数已达上限（{daily_limit}），可在设置中调整",
                    429,
                )

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

    session_id = f"ss_{os.urandom(6).hex()}"
    status_path = config.RUN_DIR / f"{session_id}.json"
    plan_path = config.RUN_DIR / f"{session_id}.plan.json"
    log_path = config.RUN_DIR / f"{session_id}.log"

    with db.session() as conn:
        ext_ids = [
            row["ext_id"]
            for row in conn.execute(
                "SELECT ext_id FROM profile_extensions WHERE profile_id=?", (profile_id,)
            ).fetchall()
        ]
    addon_paths = extensions.paths_for(ext_ids)
    first_open = not (profile["open_count"] or 0)
    builtin = extensions.builtin_manager_button(open_sidebar=first_open)
    if builtin:
        addon_paths.append(builtin)

    plan = {
        "profile_id": profile_id,
        "session_id": session_id,
        "user_data_dir": profile["user_data_dir"],
        "os": profile["os"],
        "headless": config.FORCE_HEADLESS
        or bool(profile["headless"])
        or bool(db.get_setting("headless", False)),
        "fingerprint": fingerprint,
        "fingerprint_preset": fingerprint.get("preset"),
        "addons": addon_paths,
        "proxy": proxy,
        "manager_url": config.MANAGER_URL
        if config.OPEN_MANAGER and db.get_setting("open_manager_on_launch", True)
        else None,
        "status_file": str(status_path),
    }
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    plan_path.chmod(0o600)

    with db.session() as conn:
        conn.execute(
            "INSERT INTO sessions(id, profile_id, state, started_at) VALUES (?, ?, 'starting', ?)",
            (session_id, profile_id, db.now()),
        )
        conn.execute(
            "UPDATE profiles SET status='running', last_session_id=?, last_opened_at=?, "
            "open_count=COALESCE(open_count, 0) + 1, updated_at=? WHERE id=?",
            (session_id, db.now(), db.now(), profile_id),
        )

    log_handle = open(log_path, "w", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(
        [sys.executable, "-m", "moltskin.services.core.runner", "--plan", str(plan_path)],
        cwd=str(config.ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    with db.session() as conn:
        conn.execute("UPDATE sessions SET pid=? WHERE id=?", (proc.pid, session_id))

    timeout = float(db.get_setting("launch_timeout_sec", 30))
    deadline = time.time() + timeout
    state = None
    while time.time() < deadline:
        info = _read_status(status_path)
        if info:
            state = info.get("state")
            if state in ("running", "failed"):
                break
        if proc.poll() is not None:
            break
        time.sleep(0.2)

    if state == "failed":
        info = _read_status(status_path) or {}
        _finish_session(session_id, profile_id, "failed",
                        info.get("error_code", "LAUNCH_FAILED"),
                        info.get("error_message"))
        raise LaunchError(
            info.get("error_code", "LAUNCH_FAILED"),
            info.get("error_message") or "启动失败",
            409 if info.get("error_code") == "PROFILE_LOCKED" else 500,
        )

    if state != "running":
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        _finish_session(session_id, profile_id, "failed", "LAUNCH_TIMEOUT", "启动超时")
        raise LaunchError("LAUNCH_TIMEOUT", f"启动超时（{timeout} 秒）", 504)

    with db.session() as conn:
        conn.execute("UPDATE sessions SET state='running' WHERE id=?", (session_id,))

    Thread(
        target=_monitor,
        args=(session_id, profile_id, proc, status_path, log_handle),
        daemon=True,
    ).start()

    db.log("profile.launch", profile_id, {"session_id": session_id}, actor=actor)
    return {"session_id": session_id, "profile_id": profile_id, "state": "running"}


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
        session = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if session is None:
            raise LaunchError("SESSION_NOT_FOUND", "会话不存在", 404)
        if session["state"] not in ACTIVE_STATES:
            return {"session_id": session_id, "state": session["state"]}

    pid = session["pid"]
    if _pid_alive(pid):
        try:
            os.kill(pid, 15)  # SIGTERM
        except OSError:
            pass
        timeout = float(db.get_setting("stop_timeout_sec", 8))
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not _pid_alive(pid):
                break
            time.sleep(0.2)
        if _pid_alive(pid):
            try:
                os.kill(pid, 9)
            except OSError:
                pass

    db.log("session.stop", session["profile_id"], {"session_id": session_id}, actor=actor)
    return {"session_id": session_id, "state": "stopping"}


def status(profile_id: str) -> dict:
    with db.session() as conn:
        profile = conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if profile is None:
            raise LaunchError("PROFILE_NOT_FOUND", "环境不存在", 404)
        session = _active_session(conn, profile_id)
        if session and not _pid_alive(session["pid"]):
            _finish_session(session["id"], profile_id, "crashed", "PROCESS_GONE",
                            "进程已退出")
            session = None
    return {
        "profile_id": profile_id,
        "status": profile["status"],
        "session_id": session["id"] if session else None,
        "pid": session["pid"] if session else None,
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
        output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return 0

    profiles_root = str(config.PROFILES_DIR)
    cleaned = 0
    for line in output.splitlines():
        match = re.search(r"-profile\s+(\S+)", line)
        if not match:
            continue
        profile_dir = match.group(1)
        if not profile_dir.startswith(profiles_root):
            continue
        profile_id = Path(profile_dir).name
        try:
            pid = int(line.strip().split()[0])
        except (ValueError, IndexError):
            continue
        with db.session() as conn:
            active = conn.execute(
                "SELECT 1 FROM sessions WHERE profile_id=? AND state IN ('starting','running','stopping')",
                (profile_id,),
            ).fetchone()
            profile = conn.execute("SELECT status FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if active or (profile and profile["status"] == "running"):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            cleaned += 1
        except OSError:
            pass
    return cleaned


def reap_stale() -> int:
    """周期性回收：会话仍是 running 但进程已消失时，及时改状态。

    没有这一步，被 SIGKILL 或崩溃的 runner 会让环境一直显示"运行中"，
    并发上限、备份和批量操作都会被卡住。
    """
    cleaned = 0
    with db.session() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE state IN ('starting','running','stopping')"
        ).fetchall()
        for row in rows:
            if _pid_alive(row["pid"]):
                continue
            conn.execute(
                "UPDATE sessions SET state='crashed', error_code='PROCESS_GONE', "
                "error_message='进程已退出', ended_at=? WHERE id=?",
                (db.now(), row["id"]),
            )
            conn.execute(
                "UPDATE profiles SET status='crashed', updated_at=? WHERE id=? AND status='running'",
                (db.now(), row["profile_id"]),
            )
            cleaned += 1
    return cleaned


def cleanup_orphans() -> int:
    """服务启动时清理上次异常退出留下的会话。"""
    cleaned = 0
    with db.session() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE state IN ('starting','running','stopping')"
        ).fetchall()
        for row in rows:
            if _pid_alive(row["pid"]):
                continue
            conn.execute(
                "UPDATE sessions SET state='crashed', error_code='ORPHAN', "
                "error_message='服务重启时发现进程已不存在', ended_at=? WHERE id=?",
                (db.now(), row["id"]),
            )
            conn.execute(
                "UPDATE profiles SET status='crashed', updated_at=? WHERE id=? AND status='running'",
                (db.now(), row["profile_id"]),
            )
            cleaned += 1
    return cleaned
