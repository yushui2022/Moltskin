"""批量任务的进度登记。

批量打开/关闭可能持续几十秒，接口立刻返回 job_id，前端轮询进度，
避免一个请求挂在那里、也避免超时后不知道哪些环境成功了。
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
MAX_JOBS = 40


def _trim() -> None:
    if len(_jobs) <= MAX_JOBS:
        return
    finished = sorted(
        (job for job in _jobs.values() if job["state"] != "running"),
        key=lambda job: job["created_at"],
    )
    for job in finished[: len(_jobs) - MAX_JOBS]:
        _jobs.pop(job["id"], None)


def create(action: str, items: list[dict[str, str]], worker: Callable[[str], None],
           workers: int = 4) -> dict[str, Any]:
    """items: [{id, name}], worker(id) 负责执行单条任务。"""
    job_id = "job_" + uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "action": action,
        "state": "running",
        "created_at": time.time(),
        "started_at": time.time(),
        "finished_at": None,
        "total": len(items),
        "done": 0,
        "ok": 0,
        "failed": 0,
        "items": [
            {"id": item["id"], "name": item.get("name", ""), "state": "pending", "error": None}
            for item in items
        ],
    }
    with _lock:
        _jobs[job_id] = job
        _trim()

    def run() -> None:
        def one(index: int) -> None:
            item = job["items"][index]
            with _lock:
                item["state"] = "running"
            try:
                worker(item["id"])
            except Exception as exc:  # noqa: BLE001 - 单条失败要回填原因
                with _lock:
                    item["state"] = "failed"
                    item["error"] = str(exc)[:300]
                    job["failed"] += 1
                    job["done"] += 1
            else:
                with _lock:
                    item["state"] = "done"
                    job["ok"] += 1
                    job["done"] += 1

        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(items) or 1))) as pool:
            list(pool.map(one, range(len(items))))

        with _lock:
            job["state"] = "finished"
            job["finished_at"] = time.time()

    thread = threading.Thread(target=run, name=f"moltskin-{action}", daemon=True)
    thread.start()
    return job


def get(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return _copy(job) if job else None


def latest(limit: int = 5) -> list[dict[str, Any]]:
    with _lock:
        jobs = sorted(_jobs.values(), key=lambda job: job["created_at"], reverse=True)[:limit]
        return [_copy(job) for job in jobs]


def _copy(job: dict[str, Any]) -> dict[str, Any]:
    return {
        **{k: v for k, v in job.items() if k != "items"},
        "items": [dict(item) for item in job["items"]],
    }
