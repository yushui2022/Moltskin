"""端到端闭环测试：直接打本地服务，覆盖环境生命周期、指纹稳定性、
Cookie、扩展、同步、批量任务、定时任务、备份和错误路径。

    python tests/test_e2e.py            # 需要服务已在 8765 运行
    MOLTSKIN_API=http://127.0.0.1:8765 python tests/test_e2e.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import httpx

API = os.environ.get("MOLTSKIN_API", "http://127.0.0.1:8765").rstrip("/") + "/api/v1"
ROOT = Path(__file__).resolve().parents[1]
TOKEN = os.environ.get("MOLTSKIN_TOKEN") or (
    Path(os.environ.get("MOLTSKIN_DATA_DIR", ROOT / "data")) / "api.token"
).read_text(encoding="utf-8").strip()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  · {detail}" if detail and not ok else ""))
    return ok


def call(method: str, path: str, **kwargs) -> tuple[int, dict]:
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=180) as c:
        res = c.request(method, path, **kwargs)
    try:
        body = res.json()
    except json.JSONDecodeError:
        body = {}
    return res.status_code, body


def data(method: str, path: str, **kwargs):
    status, body = call(method, path, **kwargs)
    if status >= 400:
        raise RuntimeError(f"{method} {path} -> {status} {body}")
    return body.get("data", body)


def wait_status(profile_id: str, wanted: set[str], timeout: float = 40) -> str:
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        status = data("GET", f"/profiles/{profile_id}/status")["status"]
        if status in wanted:
            return status
        time.sleep(0.8)
    return status


def wait_job(job_id: str, timeout: float = 90) -> dict:
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = data("GET", f"/jobs/{job_id}")
        if job["state"] == "finished":
            return job
        time.sleep(0.7)
    return job


def preview_of(profile: dict) -> dict:
    return profile.get("fingerprint_preview") or {}


def main() -> int:
    stamp = int(time.time())
    ids: list[str] = []
    ext_ids: list[str] = []
    schedule_ids: list[str] = []

    # ---------------------------------------------------------------- 基础
    status, body = call("GET", "/health")
    check("健康检查", status == 200 and body.get("status") == "ok", str(body))
    stats = data("GET", "/stats")
    need = {"profiles_total", "profiles_running", "today_opened", "proxies_ok", "profile_limit"}
    check("统计字段齐全", need <= set(stats), f"缺少 {need - set(stats)}")

    # ---------------------------------------------------------------- 创建
    name_a = f"e2e-A-{stamp}"
    name_b = f"e2e-B-{stamp}"
    prof_a = data("POST", "/profiles", json={"name": name_a, "headless": True, "tags": ["e2e"]})
    prof_b = data("POST", "/profiles", json={"name": name_b, "headless": True})
    ids += [prof_a["id"], prof_b["id"]]
    check("创建环境", prof_a["status"] == "ready" and bool(prof_a["id"]))

    code, _ = call("POST", "/profiles", json={"name": name_a, "headless": True})
    check("重名被拒绝", code == 409, f"实际 {code}")

    found = data("GET", "/profiles", params={"q": name_a, "field": "name", "op": "equals"})
    check("精确搜索", found["total"] == 1 and found["items"][0]["id"] == prof_a["id"])

    # ---------------------------------------------------------------- 指纹
    p1 = preview_of(data("GET", f"/profiles/{prof_a['id']}"))
    check(
        "指纹预览字段",
        all(p1.get(k) for k in ("user_agent", "platform", "screen", "webgl_renderer")),
        str(p1)[:120],
    )

    # ---------------------------------------------------------------- 启动
    data("POST", f"/profiles/{prof_a['id']}/launch")
    state = wait_status(prof_a["id"], {"running", "crashed"})
    check("启动成功", state == "running", state)

    code, body = call("POST", f"/profiles/{prof_a['id']}/launch")
    check("重复启动被拒绝", code == 409, f"实际 {code} {body}")

    code, body = call("PUT", f"/profiles/{prof_a['id']}/cookies", json={"cookies": [], "mode": "replace"})
    check("运行中禁止写 Cookie", code == 409, f"实际 {code} {body}")

    data("POST", f"/profiles/{prof_a['id']}/stop")
    state = wait_status(prof_a["id"], {"ready", "crashed"}, timeout=45)
    check("关闭成功", state == "ready", state)

    after = data("GET", f"/profiles/{prof_a['id']}")
    p2 = preview_of(after)
    check("指纹跨启动一致", p1 == p2, f"{p1.get('user_agent')} vs {p2.get('user_agent')}")
    check("记录打开次数", (after.get("open_count") or 0) >= 1, str(after.get("open_count")))
    check("记录最近打开时间", bool(after.get("last_opened_at")))

    # ---------------------------------------------------------------- Cookie
    netscape = (
        "# Netscape HTTP Cookie File\n"
        ".example.com\tTRUE\t/\tFALSE\t1893456000\tsid\tabc123\n"
        ".example.com\tTRUE\t/\tTRUE\t0\ttheme\tdark\n"
    )
    r = data("PUT", f"/profiles/{prof_a['id']}/cookies", json={"text": netscape, "mode": "replace"})
    check("Cookie 导入 Netscape", r["applied"] == 2, str(r))

    r = data("GET", f"/profiles/{prof_a['id']}/cookies")
    check("Cookie 读回", r["total"] == 2 and {c["name"] for c in r["cookies"]} == {"sid", "theme"})

    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60) as c:
        export = c.get(f"/profiles/{prof_a['id']}/cookies/export", params={"format": "json"})
    parsed = export.json()["cookies"]
    check("Cookie 导出 JSON", export.status_code == 200 and len(parsed) == 2)

    r = data(
        "PUT",
        f"/profiles/{prof_a['id']}/cookies",
        json={"cookies": [{"name": "uid", "value": "42", "domain": ".example.com", "path": "/"}], "mode": "merge"},
    )
    total = data("GET", f"/profiles/{prof_a['id']}/cookies")["total"]
    check("Cookie 合并", r["applied"] == 1 and total == 3, f"total={total}")

    # ---------------------------------------------------------------- 扩展
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"manifest_version": 2, "name": "E2E 扩展", "version": "1.0.0",
                        "content_scripts": [{"matches": ["<all_urls>"], "js": ["m.js"]}]}),
        )
        zf.writestr("m.js", "")
    ext = data(
        "POST",
        "/extensions",
        json={"filename": "e2e.zip", "content": __import__("base64").b64encode(buf.getvalue()).decode()},
    )
    ext_ids.append(ext["id"])
    check("上传扩展", ext["name"] == "E2E 扩展", str(ext)[:120])

    data("PUT", f"/profiles/{prof_a['id']}/extensions", json={"ids": [ext["id"]]})
    check("分配扩展", data("GET", f"/profiles/{prof_a['id']}/extensions")["ids"] == [ext["id"]])

    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, "w") as zf:
        zf.writestr("readme.txt", "no manifest here")
    code, _ = call(
        "POST",
        "/extensions",
        json={"filename": "bad.zip", "content": __import__("base64").b64encode(buf2.getvalue()).decode()},
    )
    check("非法扩展被拒绝", code == 422, f"实际 {code}")

    data("POST", f"/profiles/{prof_a['id']}/launch")
    state = wait_status(prof_a["id"], {"running", "crashed"})
    check("带扩展重新启动", state == "running", state)
    plan = None
    run_dir = Path(os.environ.get("MOLTSKIN_DATA_DIR", ROOT / "data")) / "run"
    plans = sorted(run_dir.glob("*.plan.json"), key=lambda p: p.stat().st_mtime)
    if plans:
        plan = json.loads(plans[-1].read_text())
    check(
        "启动计划包含扩展",
        bool(plan) and ext["id"] in " ".join(plan.get("addons") or []),
        str(plan.get("addons") if plan else None),
    )
    p3 = preview_of(data("GET", f"/profiles/{prof_a['id']}"))
    check("再次启动指纹仍一致", p1 == p3)
    data("POST", f"/profiles/{prof_a['id']}/stop")
    wait_status(prof_a["id"], {"ready", "crashed"}, timeout=45)

    # ---------------------------------------------------------------- 同步
    r = data("POST", f"/profiles/{prof_b['id']}/sync-from/{prof_a['id']}", params={"parts": "cookies"})
    copied = data("GET", f"/profiles/{prof_b['id']}/cookies")["total"]
    check("环境同步 Cookie", copied == 3, f"applied={r.get('cookies')} total={copied}")

    code, _ = call("POST", f"/profiles/{prof_a['id']}/sync-from/{prof_a['id']}", params={"parts": "cookies"})
    check("同步到自己被拒绝", code == 422, f"实际 {code}")

    # ---------------------------------------------------------------- 批量任务
    job = data("POST", "/profiles/batch-async", json={"action": "launch", "ids": [prof_a["id"], prof_b["id"]]})
    done = wait_job(job["id"])
    check("批量打开进度", done.get("ok") == 2 and done.get("failed") == 0, json.dumps(done)[:160])

    job = data("POST", "/profiles/batch-async", json={"action": "stop", "ids": [prof_a["id"], prof_b["id"]]})
    done = wait_job(job["id"])
    check("批量关闭进度", done.get("ok") == 2 and done.get("failed") == 0, json.dumps(done)[:160])
    wait_status(prof_a["id"], {"ready"}, timeout=45)
    wait_status(prof_b["id"], {"ready"}, timeout=45)

    # ---------------------------------------------------------------- 定时任务
    sch = data(
        "POST",
        "/schedules",
        json={"name": f"e2e-stop-{stamp}", "action": "stop", "mode": "daily",
              "time_of_day": "23:59", "weekdays": [1, 2, 3, 4, 5, 6, 7],
              "target_type": "ids", "target_ids": [prof_a["id"]]},
    )
    schedule_ids.append(sch["id"])
    check("创建定时任务", sch["action"] == "stop" and bool(sch.get("next_run_at")))
    result = data("POST", f"/schedules/{sch['id']}/run")
    check("立即执行定时任务", result["skipped"] == 1 or result["ok"] == 0, json.dumps(result))

    code, _ = call("POST", "/schedules", json={"name": "bad", "action": "launch", "time_of_day": "25:99"})
    check("非法时间被拒绝", code == 422, f"实际 {code}")

    interval = data(
        "POST",
        "/schedules",
        json={"name": f"e2e-int-{stamp}", "action": "stop", "mode": "interval",
              "interval_minutes": 30, "target_type": "all"},
    )
    schedule_ids.append(interval["id"])
    check("间隔任务", interval["mode"] == "interval" and interval["interval_minutes"] == 30)

    # ---------------------------------------------------------------- 备份
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=180) as c:
        backup = c.get("/backup", params={"include_profiles": "false"})
    ok_zip = backup.status_code == 200 and backup.content[:2] == b"PK"
    check("导出备份", ok_zip, f"{backup.status_code} {len(backup.content)} bytes")
    if ok_zip:
        restored = data("POST", "/backup/restore", files={"file": ("backup.zip", backup.content, "application/zip")})
        check("恢复备份", restored.get("manifest", {}).get("app") == "moltskin", str(restored)[:120])

    # ---------------------------------------------------------------- 清理
    for sid in schedule_ids:
        call("DELETE", f"/schedules/{sid}")
    for eid in ext_ids:
        call("DELETE", f"/extensions/{eid}")
    for pid in ids:
        call("DELETE", f"/profiles/{pid}", params={"delete_data": "true"})
    left = data("GET", "/profiles", params={"q": "e2e-", "field": "name", "op": "contains"})
    check("清理测试数据", left["total"] == 0, str(left["total"]))

    # ---------------------------------------------------------------- 汇总
    failed = [r for r in RESULTS if not r[1]]
    print("\n" + "=" * 60)
    print(f"共 {len(RESULTS)} 项：通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    for name, _, detail in failed:
        print(f"  ✗ {name}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
