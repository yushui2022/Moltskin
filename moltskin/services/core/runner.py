"""Runner：每个环境一个进程，负责拉起 Camoufox 并保持存活。

由 launcher 通过子进程启动，参数是启动计划 JSON 的路径。
状态通过 status 文件上报，launcher / API 读取。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import sys
import traceback
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import Error as PWError

from camoufox.sync_api import Camoufox

from . import observation
from .cookies import apply_pending_session_cookies

STOP = False


def _on_signal(signum, frame):  # noqa: ARG001
    global STOP
    STOP = True


def write_status(path: Path, **fields) -> None:
    payload = json.dumps(fields, ensure_ascii=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def acquire_lock(profile_dir: Path):
    """同一 profile 目录只允许一个进程。"""
    profile_dir.mkdir(parents=True, exist_ok=True)
    lock_path = profile_dir / ".moltskin.lock"
    handle = open(lock_path, "w", encoding="utf-8")  # noqa: SIM115 - 需要保持打开
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, help="启动计划 JSON 路径")
    args = parser.parse_args()

    plan_path = Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    status_path = Path(plan["status_file"])
    profile_dir = Path(plan["user_data_dir"])

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    lock = acquire_lock(profile_dir)
    if lock is None:
        write_status(
            status_path,
            state="failed",
            error_code="PROFILE_LOCKED",
            error_message="该环境已在运行（profile 目录被占用）",
        )
        return 2

    write_status(status_path, state="starting", pid=os.getpid())

    kwargs = {
        "headless": bool(plan.get("headless", False)),
        "block_images": bool(plan.get("block_images", False)),
        "persistent_context": True,
        "user_data_dir": str(profile_dir),
        "humanize": True,
        # 明确接受 canvas 噪声等告警（这是伪装的一部分）
        "i_know_what_im_doing": True,
    }

    proxy = plan.get("proxy")
    if proxy and plan.get("manager_url"):
        # 管理页走本机地址，不能被代理转发
        kwargs["firefox_user_prefs"] = {
            "network.proxy.no_proxies_on": "localhost, 127.0.0.1"
        }
    if proxy:
        kwargs["proxy"] = {
            "server": f"{proxy['protocol']}://{proxy['host']}:{proxy['port']}",
            "username": proxy.get("username") or "",
            "password": proxy.get("password") or "",
        }
        kwargs["geoip"] = True

    if plan.get("os"):
        kwargs["os"] = plan["os"]

    if plan.get("fingerprint_preset"):
        kwargs["fingerprint_preset"] = plan["fingerprint_preset"]

    addons = plan.get("addons") or []
    if addons:
        kwargs["addons"] = addons

    fingerprint = plan.get("fingerprint") or {}
    if fingerprint.get("locale"):
        kwargs["locale"] = fingerprint["locale"]
    if fingerprint.get("overrides"):
        kwargs["config"] = fingerprint["overrides"]

    context = None
    exit_code = 0
    try:
        with Camoufox(**kwargs) as context:
            apply_pending_session_cookies(profile_dir, context)
            version = context.browser.version if context.browser else None
            write_status(status_path, state="running", pid=os.getpid(),
                         browser_version=version if isinstance(version, str) else None)
            try:
                observation.capture(context, plan)
            except Exception as exc:
                # Diagnostic failures must leave the user's browser running.
                print(f"启动读数不可用 ({type(exc).__name__})", flush=True)

            startup_mode = plan.get("startup_page", "default")
            custom_urls = plan.get("startup_urls") or []
            if startup_mode == "custom" and custom_urls:
                for idx, target_url in enumerate(custom_urls):
                    try:
                        page = (context.pages[0] if idx == 0 and context.pages else context.new_page())
                        page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                        if idx == 0:
                            page.bring_to_front()
                    except Exception as exc:  # noqa: BLE001
                        print(f"startup url failed ({target_url}): {exc}", flush=True)
            elif startup_mode != "restore":
                start_url = plan.get("manager_url")
                if not start_url and startup_mode != "default":
                    start_url = "about:blank"
                if start_url:
                    try:
                        page = context.pages[0] if context.pages else context.new_page()
                        page.goto(start_url, wait_until="domcontentloaded", timeout=15000)
                        page.bring_to_front()
                        print(f"startup page: {page.title()}", flush=True)
                    except Exception as exc:  # noqa: BLE001 - 管理页打不开不应影响环境本身
                        print(f"startup page failed: {exc}", flush=True)

            while not STOP:
                try:
                    context.wait_for_event("close", timeout=1000)
                    break
                except PWTimeoutError:
                    continue
                except PWError:
                    break

            if STOP:
                try:
                    context.close()
                except Exception:  # noqa: BLE001 - 关闭阶段的异常不影响最终状态
                    pass
    except Exception as exc:  # noqa: BLE001 - 需要把启动失败原因写回状态文件
        exit_code = 1
        write_status(
            status_path,
            state="failed",
            error_code="LAUNCH_FAILED",
            error_message=str(exc),
            traceback=traceback.format_exc(limit=8),
        )
    else:
        write_status(status_path, state="stopped")
    finally:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
        except Exception:  # noqa: BLE001
            pass

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
