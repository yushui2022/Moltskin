"""定时任务：按天在指定时间批量打开或关闭环境。"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from . import db, launcher

CHECK_INTERVAL = 20  # 秒
GRACE_MINUTES = 10  # 服务重启后，错过不久的任务仍然补跑


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _now_hm() -> tuple[int, int]:
    now = datetime.now().astimezone()
    return now.hour, now.minute


def _due(schedule: dict[str, Any]) -> bool:
    if not schedule["enabled"]:
        return False
    try:
        hour, minute = (int(part) for part in str(schedule["time_of_day"]).split(":"))
    except (ValueError, AttributeError):
        return False
    today = _today()
    if (schedule.get("last_run_at") or "")[:10] == today:
        return False
    weekdays = schedule.get("weekdays") or [1, 2, 3, 4, 5, 6, 7]
    if datetime.now().astimezone().isoweekday() not in weekdays:
        return False
    now_h, now_m = _now_hm()
    scheduled = hour * 60 + minute
    current = now_h * 60 + now_m
    return 0 <= current - scheduled <= GRACE_MINUTES


def resolve_profiles(schedule: dict[str, Any]) -> list[dict[str, Any]]:
    with db.session() as conn:
        if schedule["target_type"] == "ids":
            ids = schedule.get("target_ids") or []
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT id, name, status FROM profiles WHERE id IN ({placeholders})", ids
            ).fetchall()
        elif schedule["target_type"] == "group":
            ids = schedule.get("target_ids") or []
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT id, name, status FROM profiles WHERE group_id IN ({placeholders})",
                ids,
            ).fetchall()
        else:
            rows = conn.execute("SELECT id, name, status FROM profiles").fetchall()
    return [dict(row) for row in rows]


def run_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    profiles = resolve_profiles(schedule)
    action = schedule["action"]
    ok, failed, skipped = 0, 0, 0
    errors: list[str] = []
    for profile in profiles:
        try:
            if action == "launch":
                if profile["status"] == "running":
                    skipped += 1
                    continue
                launcher.launch(profile["id"])
            else:
                if profile["status"] != "running":
                    skipped += 1
                    continue
                launcher.stop(profile["id"])
            ok += 1
        except Exception as exc:  # noqa: BLE001 - 单条失败要记录下来
            failed += 1
            errors.append(f"{profile['name']}: {exc}"[:160])

    result = {"ok": ok, "failed": failed, "skipped": skipped, "errors": errors[:5]}
    with db.session() as conn:
        conn.execute(
            "UPDATE schedules SET last_run_at=?, last_result=?, updated_at=? WHERE id=?",
            (db.now(), json.dumps(result, ensure_ascii=False), db.now(), schedule["id"]),
        )
    db.log("schedule.run", schedule["id"], {"action": action, **result})
    return result


def _tick() -> None:
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM schedules WHERE enabled=1").fetchall()
    for row in rows:
        schedule = dict(row)
        schedule["weekdays"] = json.loads(schedule.get("weekdays") or "[]")
        schedule["target_ids"] = json.loads(schedule.get("target_ids") or "[]")
        if not _due(schedule):
            continue
        try:
            run_schedule(schedule)
        except Exception as exc:  # noqa: BLE001 - 调度线程不能退出
            db.log("schedule.error", schedule["id"], {"error": str(exc)[:300]})


def loop(stop_event: threading.Event) -> None:
    # 启动后稍等，避免与服务初始化抢资源
    stop_event.wait(5)
    while not stop_event.is_set():
        try:
            _tick()
        except Exception:  # noqa: BLE001 - 保持调度线程存活
            pass
        stop_event.wait(CHECK_INTERVAL)


def next_run(schedule: dict[str, Any]) -> str | None:
    """给 UI 用：算出下一次触发时间。"""
    try:
        hour, minute = (int(part) for part in str(schedule["time_of_day"]).split(":"))
    except (ValueError, AttributeError):
        return None
    now = datetime.now().astimezone()
    weekdays = schedule.get("weekdays") or [1, 2, 3, 4, 5, 6, 7]
    for offset in range(0, 8):
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=offset)
        if candidate <= now:
            continue
        if candidate.isoweekday() in weekdays:
            return candidate.isoformat()
    return None


def start(stop_event: threading.Event) -> threading.Thread:
    thread = threading.Thread(target=loop, args=(stop_event,), name="moltskin-scheduler", daemon=True)
    thread.start()
    return thread


__all__ = ["start", "run_schedule", "resolve_profiles", "next_run"]
