"""边界与回归测试：找出"主流程能跑、边角会炸"的问题。

    python tests/test_edge.py
"""

from __future__ import annotations

import base64
import concurrent.futures
import io
import json
import os
import time
import zipfile
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
        body = res.json()
    except json.JSONDecodeError:
        body = {"raw": res.text[:200]}
    return res.status_code, body


def data(method: str, path: str, **kwargs):
    status, body = call(method, path, **kwargs)
    if status >= 400:
        raise RuntimeError(f"{method} {path} -> {status} {body}")
    return body.get("data", body)


def wait_status(pid: str, wanted: set[str], timeout: float = 40) -> str:
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        status = data("GET", f"/profiles/{pid}/status")["status"]
        if status in wanted:
            return status
        time.sleep(0.7)
    return status


def main() -> int:
    stamp = int(time.time())
    created: list[str] = []
    # 先恢复到安全值，避免上一次异常退出残留的限制挡住本次测试
    data("PUT", "/settings", json={"daily_open_limit": 20000, "profile_limit": 200,
                                    "require_proxy_ok": False, "max_concurrent": 10})
    settings_backup = data("GET", "/settings")
    for stale in data("GET", "/profiles", params={"q": "e2e-", "field": "name", "op": "contains"})["items"]:
        if stale["status"] == "running":
            call("POST", f"/profiles/{stale['id']}/stop")
            wait_status(stale["id"], {"ready", "crashed"}, timeout=30)
        call("DELETE", f"/profiles/{stale['id']}", params={"delete_data": "true"})

    # ---------------------------------------------------------- 输入边界
    code, _ = call("POST", "/profiles", json={"name": "x" * 200})
    check("名称超长被拒绝", code == 422, f"实际 {code}")

    code, _ = call("POST", "/profiles", json={"name": ""})
    check("空名称被拒绝", code == 422, f"实际 {code}")

    code, _ = call("POST", "/profiles", json={"name": f"e2e-edge-{stamp}", "os": "beos"})
    check("非法系统被拒绝或归一", code in (201, 422), f"实际 {code}")
    if code == 201:
        created.append(data("GET", "/profiles", params={"q": f"e2e-edge-{stamp}"})["items"][0]["id"])

    code, body = call("GET", "/profiles", params={"sort": "../../etc/passwd", "page_size": 5})
    check("非法排序参数被忽略", code == 200, f"实际 {code} {body}")

    code, _ = call("GET", "/profiles", params={"page": -5, "page_size": -1})
    check("负数分页不报错", code == 200, f"实际 {code}")

    code, _ = call("POST", "/profiles", content=b"{not json}", headers={"Content-Type": "application/json"})
    check("非法 JSON 返回 422", code == 422, f"实际 {code}")

    # ---------------------------------------------------------- 指纹导入导出闭环
    prof = data("POST", "/profiles", json={"name": f"e2e-roundtrip-{stamp}", "headless": True})
    created.append(prof["id"])
    before = data("GET", f"/profiles/{prof['id']}")
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60) as c:
        exported = c.get("/export/profiles").json()
    entry = next(p for p in exported["profiles"] if p["name"] == f"e2e-roundtrip-{stamp}")
    check("导出包含指纹预设", bool(entry.get("fingerprint", {}).get("preset")))

    call("DELETE", f"/profiles/{prof['id']}", params={"delete_data": "true"})
    created.remove(prof["id"])
    imported = data("POST", "/import/profiles", json={"profiles": [entry]})
    check("重新导入", imported["created"] == 1, str(imported))
    restored = data("GET", "/profiles", params={"q": f"e2e-roundtrip-{stamp}", "op": "equals", "field": "name"})["items"][0]
    created.append(restored["id"])
    check(
        "导入后指纹预设不变",
        preview_of(restored) == preview_of(before),
        f"{preview_of(restored)} vs {preview_of(before)}",
    )

    # ---------------------------------------------------------- 删除保护
    data("POST", f"/profiles/{restored['id']}/launch")
    state = wait_status(restored["id"], {"running", "crashed"})
    check("启动用于删除保护测试", state == "running", state)
    code, _ = call("DELETE", f"/profiles/{restored['id']}", params={"delete_data": "true"})
    check("运行中禁止删除", code == 409, f"实际 {code}")
    code, _ = call("POST", f"/profiles/{restored['id']}/reset-fingerprint")
    check("运行中禁止重置指纹", code == 409, f"实际 {code}")
    data("POST", f"/profiles/{restored['id']}/stop")
    wait_status(restored["id"], {"ready", "crashed"}, timeout=45)

    # ---------------------------------------------------------- 并发上限
    data("PUT", "/settings", json={"max_concurrent": 1})
    p1 = data("POST", "/profiles", json={"name": f"e2e-conc-1-{stamp}", "headless": True})
    p2 = data("POST", "/profiles", json={"name": f"e2e-conc-2-{stamp}", "headless": True})
    created += [p1["id"], p2["id"]]
    data("POST", f"/profiles/{p1['id']}/launch")
    wait_status(p1["id"], {"running", "crashed"})
    code, body = call("POST", f"/profiles/{p2['id']}/launch")
    check("并发上限生效", code in (429, 409), f"实际 {code} {body}")
    data("POST", f"/profiles/{p1['id']}/stop")
    wait_status(p1["id"], {"ready", "crashed"}, timeout=45)
    data("PUT", "/settings", json={"max_concurrent": settings_backup["max_concurrent"]})

    # ---------------------------------------------------------- 同时启动同一环境
    data("POST", f"/profiles/{p2['id']}/stop") if False else None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(call, "POST", f"/profiles/{p2['id']}/launch") for _ in range(2)]
        codes = sorted(f.result()[0] for f in futures)
    check("同一环境并发启动只有一个成功", codes.count(200) == 1, f"codes={codes}")
    data("POST", f"/profiles/{p2['id']}/stop")
    wait_status(p2["id"], {"ready", "crashed"}, timeout=45)

    # ---------------------------------------------------------- Cookie 边角
    weird = [
        {"name": "unicode", "value": "中文值🎉", "domain": ".例子.com", "path": "/"},
        {"name": "expired", "value": "old", "domain": ".example.com", "path": "/", "expires": 1},
        {"name": "session", "value": "s", "domain": "example.com", "path": "/app"},
        {"name": "secure_only", "value": "x", "domain": ".example.com", "path": "/", "secure": True, "httpOnly": True},
    ]
    r = data("PUT", f"/profiles/{p1['id']}/cookies", json={"cookies": weird, "mode": "replace"})
    got = data("GET", f"/profiles/{p1['id']}/cookies")["cookies"]
    by_name = {c["name"]: c for c in got}
    check("Cookie 数量正确", len(got) == 4, str(len(got)))
    check("Unicode Cookie 保真", by_name.get("unicode", {}).get("value") == "中文值🎉")
    check("过期 Cookie 保留时间戳", by_name.get("expired", {}).get("expires", 0) > 0)
    check("会话 Cookie 为 -1", by_name.get("session", {}).get("expires") == -1)
    check("安全标记保留", by_name.get("secure_only", {}).get("httpOnly") is True)

    # ---------------------------------------------------------- zip 路径穿越
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"manifest_version": 2, "name": "evil", "version": "1"}))
        zf.writestr("../../../../tmp/moltskin_evil.txt", "pwned")
    code, body = call(
        "POST",
        "/extensions",
        json={"filename": "evil.zip", "content": base64.b64encode(evil.getvalue()).decode()},
    )
    escaped = Path("/tmp/moltskin_evil.txt").exists()
    check("扩展不允许路径穿越", code == 422 and not escaped, f"code={code} escaped={escaped}")
    if Path("/tmp/moltskin_evil.txt").exists():
        Path("/tmp/moltskin_evil.txt").unlink()

    # ---------------------------------------------------------- 备份异常
    code, _ = call(
        "POST", "/backup/restore", files={"file": ("bad.zip", b"not a zip", "application/zip")}
    )
    check("非法备份被拒绝", code == 422, f"实际 {code}")

    # ---------------------------------------------------------- 代理失败路径
    px = data(
        "POST",
        "/proxies",
        json={"name": f"e2e-dead-{stamp}", "protocol": "http", "host": "127.0.0.1", "port": 9},
    )
    checked = data("POST", f"/proxies/{px['id']}/check")
    check("坏代理检测为失败", checked["status"] == "failed", str(checked)[:120])
    call("DELETE", f"/proxies/{px['id']}")

    # ---------------------------------------------------------- 会话一致性
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60) as c:
        sessions = c.get("/sessions", params={"page_size": 50}).json()["data"]
    stuck = [s for s in sessions if s["state"] in ("starting", "running", "stopping")]
    check("没有残留的活跃会话", not stuck, f"{len(stuck)} 个残留")

    # ---------------------------------------------------------- 解绑 / 换系统
    px2 = data("POST", "/proxies", json={"name": f"e2e-px-{stamp}", "protocol": "http",
                                         "host": "127.0.0.1", "port": 9})
    probe = data("POST", "/profiles", json={"name": f"e2e-patch-{stamp}", "headless": True,
                                             "proxy_id": px2["id"]})
    created.append(probe["id"])
    patched = data("PATCH", f"/profiles/{probe['id']}", json={"proxy_id": None})
    check("PATCH null 解绑代理", patched["proxy"] is None)

    group = data("POST", "/groups", json={"name": f"e2e-g-{stamp}"})
    data("PATCH", f"/profiles/{probe['id']}", json={"group_id": group["id"]})
    patched = data("PATCH", f"/profiles/{probe['id']}", json={"group_id": None})
    check("PATCH null 移出分组", patched["group"] is None)
    call("DELETE", f"/groups/{group['id']}")

    before_ua = patched["fingerprint_preview"]["user_agent"]
    patched = data("PATCH", f"/profiles/{probe['id']}", json={"os": "macos"})
    after_ua = patched["fingerprint_preview"]["user_agent"]
    check("换系统会重建指纹", "Macintosh" in after_ua and after_ua != before_ua, after_ua)

    # ---------------------------------------------------------- 上限与严格代理
    data("PATCH", f"/profiles/{probe['id']}", json={"proxy_id": px2["id"]})
    data("PUT", "/settings", json={"require_proxy_ok": True})
    code, body = call("POST", f"/profiles/{probe['id']}/launch")
    check("严格模式拦截未检测代理", code == 422 and "PROXY_UNCHECKED" in json.dumps(body), f"{code} {body}")
    data("PUT", "/settings", json={"require_proxy_ok": False})
    data("PATCH", f"/profiles/{probe['id']}", json={"proxy_id": None})
    call("DELETE", f"/proxies/{px2['id']}")

    opened_today = data("GET", "/stats")["today_opened"]
    data("PUT", "/settings", json={"daily_open_limit": opened_today})
    code, body = call("POST", f"/profiles/{probe['id']}/launch")
    check("每日打开上限生效", code == 429 and "DAILY_LIMIT" in json.dumps(body), f"{code} {body}")

    data("PUT", "/settings", json={"daily_open_limit": opened_today + 1})
    code, body = call("POST", f"/profiles/{probe['id']}/launch")
    check("上限之下可以打开", code == 200, f"{code} {body}")
    if code == 200:
        wait_status(probe["id"], {"running"}, timeout=30)
        data("POST", f"/profiles/{probe['id']}/stop")
        wait_status(probe["id"], {"ready", "crashed"}, timeout=45)
    data("PUT", "/settings", json={"daily_open_limit": settings_backup["daily_open_limit"]})

    data("PUT", "/settings", json={"profile_limit": 1})
    code, body = call("POST", "/profiles", json={"name": f"e2e-over-{stamp}"})
    check("环境总量上限生效", code == 409 and "PROFILE_LIMIT" in json.dumps(body), f"{code} {body}")
    data("PUT", "/settings", json={"profile_limit": settings_backup["profile_limit"]})

    # ---------------------------------------------------------- 清理
    data("PUT", "/settings", json={
        "daily_open_limit": settings_backup["daily_open_limit"],
        "profile_limit": settings_backup["profile_limit"],
        "require_proxy_ok": settings_backup["require_proxy_ok"],
        "max_concurrent": settings_backup["max_concurrent"],
    })
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


def preview_of(profile: dict) -> dict:
    return profile.get("fingerprint_preview") or {}


if __name__ == "__main__":
    raise SystemExit(main())
