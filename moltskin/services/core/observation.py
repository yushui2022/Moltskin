"""Read-only diagnostics from the blank document of a Camoufox launch."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from . import config, db


_READ = """() => {
    if (location.href !== 'about:blank') return null;
    const values = {
        user_agent: navigator.userAgent,
        platform: navigator.platform,
        hardware_concurrency: navigator.hardwareConcurrency,
        screen: screen.width + 'x' + screen.height,
        color_depth: screen.colorDepth,
    };
    try {
        const gl = document.createElement('canvas').getContext('webgl');
        const info = gl && gl.getExtension('WEBGL_debug_renderer_info');
        if (info) {
            values.webgl_vendor = gl.getParameter(info.UNMASKED_VENDOR_WEBGL);
            values.webgl_renderer = gl.getParameter(info.UNMASKED_RENDERER_WEBGL);
        }
        if (gl) gl.getExtension('WEBGL_lose_context')?.loseContext();
    } catch (_) {
        // Graphics diagnostics can be unavailable while the other values still work.
    }
    return values;
}"""


def _config_key(os_name, fingerprint, headless, proxy_id) -> str:
    settings = [os_name, fingerprint, bool(headless), proxy_id]
    encoded = json.dumps(settings, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _values(raw) -> dict:
    result = {}
    if not isinstance(raw, dict):
        return result
    for field in ("user_agent", "platform", "screen", "webgl_vendor", "webgl_renderer"):
        value = raw.get(field)
        if isinstance(value, str) and value.strip() and len(value) <= 2048:
            result[field] = value
    for field in ("hardware_concurrency", "color_depth"):
        value = raw.get(field)
        if type(value) is int and value > 0:
            result[field] = value
    return result


def save(plan: dict, values: dict | None) -> None:
    """Publish one sample atomically, without storing the raw launch options."""
    values = _values(values)
    if not values:
        return
    path = Path(plan["status_file"]).with_suffix(".observation.json")
    key = _config_key(plan["os"], plan["fingerprint"], plan["headless"], (plan.get("proxy") or {}).get("id"))
    report = {"profile_id": plan["profile_id"], "session_id": plan["session_id"],
              "config_key": key, "measured_at": db.now(), "values": values}
    tmp = tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False)
    try:
        with tmp:
            json.dump(report, tmp, ensure_ascii=False)
        os.replace(tmp.name, path)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def capture(context, plan: dict) -> None:
    pages = context.pages
    reuse = len(pages) == 1 and pages[0].url == "about:blank"
    page = pages[0] if reuse else context.new_page()
    try:
        # Recheck the document inside the read in case the initial tab navigated.
        save(plan, page.locator("html").evaluate(_READ, timeout=2500))
    finally:
        if not reuse and len(context.pages) > 1:
            page.close()


def read_current(row) -> dict | None:
    session_id = row["last_session_id"]
    if not isinstance(session_id, str) or not re.fullmatch(r"ss_[a-f0-9]{12}", session_id):
        return None
    path = config.RUN_DIR / f"{session_id}.observation.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(report, dict) or report.get("session_id") != session_id
                or report.get("profile_id") != row["id"]):
            return None
        headless = config.FORCE_HEADLESS or bool(row["headless"]) or bool(db.get_setting("headless", False))
        key = _config_key(row["os"], json.loads(row["fingerprint"] or "{}"), headless, row["proxy_id"])
        if key != report.get("config_key"):
            return None
        measured_at = report["measured_at"]
        if datetime.fromisoformat(measured_at).tzinfo is None:
            return None
        values = _values(report.get("values"))
        return {"session_id": session_id, "measured_at": measured_at, "values": values} if values else None
    except (OSError, ValueError, KeyError, TypeError):
        return None
