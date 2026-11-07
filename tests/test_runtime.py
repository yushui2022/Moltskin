"""运行时闭环测试：真实启动浏览器验证指纹身份稳定、进程回收和调度触发。

    python tests/test_runtime.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import httpx

API = os.environ.get("MOLTSKIN_API", "http://127.0.0.1:8765").rstrip("/") + "/api/v1"
ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("MOLTSKIN_DATA_DIR", ROOT / "data"))
TOKEN = os.environ.get("MOLTSKIN_TOKEN") or (DATA / "api.token").read_text().strip()

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  · {detail}" if detail and not ok else ""))
    return ok


def call(method: str, path: str, **kwargs) -> tuple[int, dict]:
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=120) as c:
        res = c.request(method, path, **kwargs)
    try:
        return res.status_code, res.json()
    except json.JSONDecodeError:
        return res.status_code, {}


def data(method: str, path: str, **kwargs):
    status, body = call(method, path, **kwargs)
    if status >= 400:
        raise RuntimeError(f"{method} {path} -> {status} {body}")
    return body.get("data", body)


def wait_status(pid: str, wanted: set[str], timeout: float = 45) -> str:
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        status = data("GET", f"/profiles/{pid}/status")["status"]
        if status in wanted:
            return status
        time.sleep(0.7)
    return status


def latest_plan() -> dict:
    plans = sorted((DATA / "run").glob("*.plan.json"), key=lambda p: p.stat().st_mtime)
    return json.loads(plans[-1].read_text()) if plans else {}


def probe_identity(preset: dict, os_name: str) -> dict:
    """用同一份预设起两次浏览器，读出真实运行时指纹。"""
    from camoufox.sync_api import Camoufox
    import tempfile

    def once() -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            with Camoufox(
                headless=True,
                persistent_context=True,
                user_data_dir=tmp,
                os=os_name,
                fingerprint_preset=preset,
                i_know_what_im_doing=True,
            ) as ctx:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto("about:blank")
                return page.evaluate(
                    """() => {
                      const gl = document.createElement('canvas').getContext('webgl');
                      const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
                      return {
                        ua: navigator.userAgent,
                        platform: navigator.platform,
                        cores: navigator.hardwareConcurrency,
                        lang: navigator.languages.join(','),
                        screen: [screen.width, screen.height, screen.colorDepth].join('x'),
                        dpr: window.devicePixelRatio,
                        tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
                        glVendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
                        glRenderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
                      };
                    }"""
                )

    first = once()
    second = once()
    return {"first": first, "second": second, "same": first == second}


def main() -> int:
    stamp = int(time.time())
    created: list[str] = []
    schedules: list[str] = []

    # ------------------------------------------------ 运行时指纹身份
    prof = data("POST", "/profiles", json={"name": f"e2e-id-{stamp}", "headless": True})
    created.append(prof["id"])
    data("POST", f"/profiles/{prof['id']}/launch")
    state = wait_status(prof["id"], {"running", "crashed"})
    check("启动环境", state == "running", state)
    plan1 = latest_plan()
    preset1 = (plan1.get("fingerprint_preset") or {})
    data("POST", f"/profiles/{prof['id']}/stop")
    wait_status(prof["id"], {"ready", "crashed"}, timeout=45)

    data("POST", f"/profiles/{prof['id']}/launch")
    state = wait_status(prof["id"], {"running", "crashed"})
    plan2 = latest_plan()
    preset2 = (plan2.get("fingerprint_preset") or {})
    data("POST", f"/profiles/{prof['id']}/stop")
    wait_status(prof["id"], {"ready", "crashed"}, timeout=45)
    check("两次启动使用同一预设", bool(preset1) and preset1 == preset2)

    if preset1:
        result = probe_identity(preset1, plan1.get("os", "windows"))
        same = result["same"]
        detail = ""
        if not same:
            diffs = {k: (result["first"][k], result["second"][k])
                     for k in result["first"] if result["first"][k] != result["second"][k]}
            detail = json.dumps(diffs, ensure_ascii=False)[:300]
        check("同一预设的真实指纹一致", same, detail)
        check(
            "指纹字段齐全",
            all(result["first"].get(k) not in (None, "", "0x0x0") for k in
                ("ua", "platform", "cores", "screen", "tz", "glRenderer")),
            json.dumps(result["first"], ensure_ascii=False)[:200],
        )

    # ------------------------------------------------ 进程僵死回收
    data("POST", f"/profiles/{prof['id']}/launch")
    state = wait_status(prof["id"], {"running", "crashed"})
    sessions = data("GET", "/sessions", params={"profile_id": prof["id"], "page_size": 5})
    items = sessions if isinstance(sessions, list) else sessions.get("items", [])
    pid = items[0]["pid"] if items else None
    check("拿到 runner 进程", bool(pid), str(items[:1]))
    if pid:
        os.kill(pid, signal.SIGKILL)
        deadline = time.time() + 30
        status = ""
        while time.time() < deadline:
            status = data("GET", f"/profiles/{prof['id']}/status")["status"]
            if status == "crashed":
                break
            time.sleep(2)
        check("进程被杀后自动标记异常", status == "crashed", status)

    # ------------------------------------------------ 定时任务真实触发
    target = data("POST", "/profiles", json={"name": f"e2e-cron-{stamp}", "headless": True})
    created.append(target["id"])
    sch = data(
        "POST",
        "/schedules",
        json={"name": f"e2e-cron-{stamp}", "action": "stop", "mode": "interval",
              "interval_minutes": 1, "target_type": "ids", "target_ids": [target["id"]]},
    )
    schedules.append(sch["id"])
    check("新建间隔任务", sch["mode"] == "interval")
    data("POST", f"/schedules/{sch['id']}/run")  # 写入 last_run_at
    time.sleep(3)
    first_run = next(s for s in data("GET", "/schedules") if s["id"] == sch["id"])["last_run_at"]
    print("  等待调度线程触发（最多 100 秒）…")
    fired = False
    deadline = time.time() + 100
    while time.time() < deadline:
        current = next(s for s in data("GET", "/schedules") if s["id"] == sch["id"])
        if current["last_run_at"] and current["last_run_at"] != first_run:
            fired = True
            break
        time.sleep(5)
    check("间隔任务自动触发", fired, "100 秒内未再次执行")

    # ------------------------------------------------ 并发建号
    started = time.time()
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=120) as c:
        for i in range(20):
            res = c.post("/profiles", json={"name": f"e2e-burst-{stamp}-{i}", "headless": True})
            if res.status_code == 201:
                created.append(res.json()["data"]["id"])
    burst = data("GET", "/profiles", params={"q": f"e2e-burst-{stamp}", "field": "name", "op": "contains"})
    check("连续创建 20 个环境", burst["total"] == 20, f"total={burst['total']}, {time.time()-started:.1f}s")

    # ------------------------------------------------ 清理
    for sid in schedules:
        call("DELETE", f"/schedules/{sid}")
    for pid in created:
        call("DELETE", f"/profiles/{pid}", params={"delete_data": "true"})
    left = data("GET", "/profiles", params={"q": "e2e-", "field": "name", "op": "contains"})
    check("清理测试数据", left["total"] == 0, str(left["total"]))

    failed = [r for r in RESULTS if not r[1]]
    print("\n" + "=" * 60)
    print(f"共 {len(RESULTS)} 项：通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    for name, _, detail in failed:
        print(f"  ✗ {name}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
