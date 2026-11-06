"""Fingerprint self-test: open CreepJS in the profile and save a screenshot."""

from __future__ import annotations

import json
import time
from camoufox.sync_api import Camoufox

from . import config, db

TEST_URL = "https://abrahamjuliot.github.io/creepjs/"


def run(profile: dict, proxy: dict | None = None) -> dict:
    config.ensure_dirs()
    config.SELFTEST_DIR.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{profile['id']}-{stamp}.png"
    image_path = config.SELFTEST_DIR / name

    fingerprint = json.loads(profile["fingerprint"] or "{}")
    kwargs = {
        "headless": True,
        "persistent_context": True,
        "user_data_dir": profile["user_data_dir"],
        "i_know_what_im_doing": True,
    }
    if fingerprint.get("preset"):
        kwargs["fingerprint_preset"] = fingerprint["preset"]
    if profile.get("os"):
        kwargs["os"] = profile["os"]
    if proxy:
        kwargs["proxy"] = {
            "server": f"{proxy['protocol']}://{proxy['host']}:{proxy['port']}",
            "username": proxy.get("username") or "",
            "password": proxy.get("password") or "",
        }
        kwargs["geoip"] = True

    with Camoufox(**kwargs) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(TEST_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(12000)
        # 只截可视区域，整页截图太长，放到弹窗里看不清
        page.screenshot(path=str(image_path), full_page=False)
        try:
            text = page.inner_text("body")[:4000]
        except Exception:  # noqa: BLE001
            text = ""

    meta = {
        "profile_id": profile["id"],
        "image": name,
        "url": TEST_URL,
        "created_at": db.now(),
        "text": text,
    }
    (config.SELFTEST_DIR / f"{name}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta
