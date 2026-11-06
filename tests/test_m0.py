"""M0 集成测试：启动服务 → 创建 → 打开 → 关闭 → 再次打开 → 删除。

运行：
    .venv/bin/python moltskin/tests/test_m0.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"


def wait_health(timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/api/v1/health", timeout=1)
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise RuntimeError("服务启动超时")


def main() -> int:
    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "moltskin.services.core.main:app",
         "--host", "127.0.0.1", "--port", "8765"],
        cwd=ROOT,
        stdout=open(ROOT / "data" / "test-server.log", "w"),
        stderr=subprocess.STDOUT,
    )
    try:
        wait_health()
        token = (ROOT / "data" / "api.token").read_text().strip()
        client = httpx.Client(
            base_url=BASE,
            headers={"Authorization": f"Bearer {token}"},
            timeout=90,
        )

        print("[1] health:", client.get("/api/v1/health").json())

        name = f"test-{int(time.time())}"
        headless = "--headful" not in sys.argv
        r = client.post("/api/v1/profiles", json={"name": name, "os": "windows", "headless": headless})
        r.raise_for_status()
        profile = r.json()["data"]
        print("[2] created:", profile["id"], profile["name"])
        assert profile["status"] == "ready"

        r = client.post(f"/api/v1/profiles/{profile['id']}/launch")
        r.raise_for_status()
        session = r.json()["data"]
        print("[3] launched:", session)
        assert session["state"] == "running"

        r = client.get(f"/api/v1/profiles/{profile['id']}/status")
        status = r.json()["data"]
        print("[4] status:", status)
        assert status["status"] == "running"

        profile_dir = Path(profile["user_data_dir"])
        assert profile_dir.exists(), "profile 目录不存在"
        time.sleep(2)
        files = list(profile_dir.rglob("*"))
        print("[5] profile dir files:", len(files))
        assert len(files) > 3, "浏览器没有写入 profile 数据"

        r = client.post(f"/api/v1/profiles/{profile['id']}/stop")
        r.raise_for_status()
        print("[6] stop requested")

        for _ in range(50):
            status = client.get(f"/api/v1/profiles/{profile['id']}/status").json()["data"]
            if status["status"] == "ready":
                break
            time.sleep(0.3)
        print("[7] status after stop:", status)
        assert status["status"] == "ready", f"关闭后状态不正确: {status}"

        r = client.post(f"/api/v1/profiles/{profile['id']}/launch")
        r.raise_for_status()
        print("[8] relaunched with same profile")
        time.sleep(2)
        client.post(f"/api/v1/profiles/{profile['id']}/stop")
        time.sleep(1.5)

        r = client.delete(f"/api/v1/profiles/{profile['id']}?delete_data=true")
        r.raise_for_status()
        print("[9] deleted")

        print("\nM0 PASS")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    sys.exit(main())
