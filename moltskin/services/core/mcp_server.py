"""Moltskin MCP server.

Exposes profile, proxy and self-test operations as MCP tools over stdio.
Start the Moltskin service first, then point your MCP client at:

    python /path/to/Moltskin/moltskin/services/core/mcp_server.py
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

ROOT = Path(__file__).resolve().parents[3]
API_URL = os.environ.get("MOLTSKIN_API", "http://127.0.0.1:8765").rstrip("/")


def _token() -> str:
    token = os.environ.get("MOLTSKIN_TOKEN")
    if token:
        return token
    path = Path(os.environ.get("MOLTSKIN_DATA_DIR", ROOT / "data")) / "api.token"
    return path.read_text(encoding="utf-8").strip()


def _request(method: str, path: str, **kwargs: Any) -> Any:
    headers = {"Authorization": f"Bearer {_token()}"}
    with httpx.Client(base_url=f"{API_URL}/api/v1", headers=headers, timeout=180) as client:
        response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        try:
            error = response.json().get("error") or {}
        except Exception:  # noqa: BLE001
            error = {}
        raise RuntimeError(f"{error.get('code', 'HTTP_ERROR')}: {error.get('message', response.text)}")
    body = response.json()
    return body.get("data", body)


mcp = MCPServer(
    "moltskin",
    instructions=(
        "Moltskin manages isolated anti-detect browser profiles built on patched Chromium and Camoufox. "
        "Use these tools to create/launch/stop profiles, manage proxies and extensions, "
        "and perform stealth page-level automation (navigate_page, snapshot_page, click_page, "
        "fill_page, press_key, evaluate_page, screenshot_page) on running Chromium profiles."
    ),
)


@mcp.tool()
def list_profiles(status: str = "", q: str = "", tag: str = "",
                  sort: str = "last_opened", order: str = "desc") -> list[dict[str, Any]]:
    """List browser profiles.

    status: ready, running, crashed, draft, opened, unopened, stopped.
    sort: last_opened, created_at, name, status, open_count.
    """
    data = _request(
        "GET",
        "/profiles",
        params={
            "page_size": 200,
            "status": status,
            "q": q,
            "tag": tag,
            "sort": sort,
            "order": order,
        },
    )
    return data["items"]


@mcp.tool()
def get_profile(profile_id: str) -> dict[str, Any]:
    """Get one profile, including its fingerprint preview and proxy binding."""
    return _request("GET", f"/profiles/{profile_id}")


@mcp.tool()
def create_profile(
    name: str,
    engine: str = "chromium",
    os: str = "windows",
    proxy_id: str | None = None,
    tags: list[str] | None = None,
    remark: str = "",
    headless: bool = False,
) -> dict[str, Any]:
    """Create an isolated browser profile (engine: 'chromium' or 'camoufox')."""
    body: dict[str, Any] = {
        "name": name,
        "engine": engine,
        "proxy_id": proxy_id,
        "tags": tags or [],
        "remark": remark,
        "headless": headless,
    }
    if engine == "camoufox":
        body["os"] = os
    return _request("POST", "/profiles", json=body)


@mcp.tool()
def launch_profile(profile_id: str) -> dict[str, Any]:
    """Launch a profile. Returns a session id."""
    return _request("POST", f"/profiles/{profile_id}/launch")


@mcp.tool()
def stop_profile(profile_id: str) -> dict[str, Any]:
    """Stop a running profile."""
    return _request("POST", f"/profiles/{profile_id}/stop")


@mcp.tool()
def profile_status(profile_id: str) -> dict[str, Any]:
    """Get the live status of a profile."""
    return _request("GET", f"/profiles/{profile_id}/status")


@mcp.tool()
def verify_profile(profile_id: str) -> dict[str, Any]:
    """Compare the last Camoufox launch readings with the preset and check the proxy configuration."""
    return _request("POST", f"/profiles/{profile_id}/verify")


@mcp.tool()
def run_self_test(profile_id: str) -> dict[str, Any]:
    """Open CreepJS in the profile and return a screenshot URL plus extracted report."""
    return _request("POST", f"/profiles/{profile_id}/self-test")


@mcp.tool()
def reset_fingerprint(profile_id: str) -> dict[str, Any]:
    """Generate a new fingerprint preset for a stopped profile, keeping cookies."""
    return _request("POST", f"/profiles/{profile_id}/reset-fingerprint")


@mcp.tool()
def get_cookies(profile_id: str, domain: str = "") -> dict[str, Any]:
    """Read a profile's cookies from its Firefox profile (works while stopped)."""
    return _request("GET", f"/profiles/{profile_id}/cookies", params={"domain": domain})


@mcp.tool()
def set_cookies(profile_id: str, cookies_json: str, mode: str = "merge") -> dict[str, Any]:
    """Import cookies from a JSON array, storage_state or Netscape cookies.txt text."""
    import json as _json

    try:
        cookies = _json.loads(cookies_json)
        payload: dict[str, Any] = {"cookies": cookies, "mode": mode}
    except _json.JSONDecodeError:
        payload = {"text": cookies_json, "mode": mode}
    return _request("PUT", f"/profiles/{profile_id}/cookies", json=payload)


@mcp.tool()
def copy_cookies(target_profile_id: str, source_profile_id: str) -> dict[str, Any]:
    """Copy cookies from one profile to another (merge)."""
    return _request("POST", f"/profiles/{target_profile_id}/cookies/copy-from/{source_profile_id}")


@mcp.tool()
def list_extensions() -> list[dict[str, Any]]:
    """List uploaded browser extensions."""
    return _request("GET", "/extensions")


@mcp.tool()
def set_profile_extensions(profile_id: str, extension_ids: list[str]) -> dict[str, Any]:
    """Choose which extensions a stopped profile loads on next launch."""
    return _request("PUT", f"/profiles/{profile_id}/extensions", json={"ids": extension_ids})


@mcp.tool()
def list_schedules() -> list[dict[str, Any]]:
    """List scheduled launch/stop tasks."""
    return _request("GET", "/schedules")


@mcp.tool()
def create_schedule(
    name: str,
    action: str = "launch",
    time_of_day: str = "09:00",
    weekdays: list[int] | None = None,
    target_type: str = "all",
    target_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Create a daily task. action: launch|stop; target_type: all|group|ids."""
    return _request(
        "POST",
        "/schedules",
        json={
            "name": name,
            "action": action,
            "time_of_day": time_of_day,
            "weekdays": weekdays or [1, 2, 3, 4, 5, 6, 7],
            "target_type": target_type,
            "target_ids": target_ids or [],
        },
    )


@mcp.tool()
def run_schedule(schedule_id: str) -> dict[str, Any]:
    """Run a scheduled task immediately."""
    return _request("POST", f"/schedules/{schedule_id}/run")


@mcp.tool()
def list_tags() -> list[dict[str, Any]]:
    """List every tag with the number of profiles using it."""
    return _request("GET", "/tags")


@mcp.tool()
def stats() -> dict[str, Any]:
    """Counts for profiles, running sessions, today's launches and proxy health."""
    return _request("GET", "/stats")


@mcp.tool()
def list_proxies() -> list[dict[str, Any]]:
    """List configured proxies and their last check result."""
    return _request("GET", "/proxies")


@mcp.tool()
def create_proxy(
    name: str,
    host: str,
    port: int,
    protocol: str = "http",
    username: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    """Add a proxy (http, https or socks5)."""
    return _request(
        "POST",
        "/proxies",
        json={
            "name": name,
            "protocol": protocol,
            "host": host,
            "port": port,
            "username": username,
            "password": password,
        },
    )


@mcp.tool()
def check_proxy(proxy_id: str) -> dict[str, Any]:
    """Check a proxy's exit IP, country, timezone and latency."""
    return _request("POST", f"/proxies/{proxy_id}/check")


@mcp.tool()
def list_groups() -> list[dict[str, Any]]:
    """List profile groups."""
    return _request("GET", "/groups")


@mcp.tool()
def create_group(name: str) -> dict[str, Any]:
    """Create a profile group."""
    return _request("POST", "/groups", json={"name": name})


@mcp.tool()
def batch_profiles(
    action: str,
    ids: list[str],
    group_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Batch action: launch, stop, delete, move or tag."""
    return _request(
        "POST",
        "/profiles/batch",
        json={"action": action, "ids": ids, "group_id": group_id, "tags": tags},
    )


@mcp.tool()
def engine_info() -> dict[str, Any]:
    """Chromium / Camoufox / Playwright versions and installed browser builds."""
    return _request("GET", "/engine")


@mcp.tool()
def list_pages(profile_id: str) -> list[dict[str, Any]]:
    """List open tabs in a running Chromium profile."""
    return _request("GET", f"/profiles/{profile_id}/pages")["pages"]


@mcp.tool()
def new_page(profile_id: str, url: str = "about:blank") -> dict[str, Any]:
    """Open a new tab in a running Chromium profile."""
    return _request("POST", f"/profiles/{profile_id}/pages", json={"url": url})


@mcp.tool()
def close_page(profile_id: str, target_id: str) -> dict[str, Any]:
    """Close a tab by target_id in a running Chromium profile."""
    return _request("DELETE", f"/profiles/{profile_id}/pages/{target_id}")


@mcp.tool()
def navigate_page(
    profile_id: str,
    url: str,
    target_id: str = "",
    wait_until: str = "load",
) -> dict[str, Any]:
    """Navigate a tab in a running Chromium profile (wait_until: none|domcontentloaded|load)."""
    return _request(
        "POST",
        f"/profiles/{profile_id}/pages/navigate",
        json={"url": url, "target_id": target_id, "wait_until": wait_until},
    )


@mcp.tool()
def snapshot_page(
    profile_id: str,
    target_id: str = "",
    max_elements: int = 120,
    include_text: bool = True,
) -> dict[str, Any]:
    """Inspect a running Chromium tab in an isolated world (returns visible text and interactive selectors)."""
    return _request(
        "POST",
        f"/profiles/{profile_id}/pages/snapshot",
        json={
            "target_id": target_id,
            "max_elements": max_elements,
            "include_text": include_text,
        },
    )


@mcp.tool()
def click_page(
    profile_id: str,
    selector: str = "",
    x: float | None = None,
    y: float | None = None,
    target_id: str = "",
    button: str = "left",
    click_count: int = 1,
) -> dict[str, Any]:
    """Click an element by CSS/text= selector or (x, y) coordinates using trusted input events."""
    return _request(
        "POST",
        f"/profiles/{profile_id}/pages/click",
        json={
            "selector": selector or None,
            "x": x,
            "y": y,
            "target_id": target_id,
            "button": button,
            "click_count": click_count,
        },
    )


@mcp.tool()
def fill_page(
    profile_id: str,
    selector: str,
    value: str,
    target_id: str = "",
    clear: bool = True,
    submit: bool = False,
) -> dict[str, Any]:
    """Focus and type text into an input/textarea/select in a running Chromium tab."""
    return _request(
        "POST",
        f"/profiles/{profile_id}/pages/fill",
        json={
            "selector": selector,
            "value": value,
            "target_id": target_id,
            "clear": clear,
            "submit": submit,
        },
    )


@mcp.tool()
def press_key(profile_id: str, key: str, target_id: str = "") -> dict[str, Any]:
    """Press a keyboard key (e.g. Enter, Tab, Escape, ArrowDown) in a running Chromium tab."""
    return _request(
        "POST",
        f"/profiles/{profile_id}/pages/press",
        json={"key": key, "target_id": target_id},
    )


@mcp.tool()
def evaluate_page(
    profile_id: str,
    expression: str,
    target_id: str = "",
    isolated: bool = False,
    await_promise: bool = True,
) -> dict[str, Any]:
    """Evaluate JavaScript in a running Chromium tab without enabling Runtime.enable."""
    return _request(
        "POST",
        f"/profiles/{profile_id}/pages/evaluate",
        json={
            "expression": expression,
            "target_id": target_id,
            "isolated": isolated,
            "await_promise": await_promise,
        },
    )


@mcp.tool()
def screenshot_page(
    profile_id: str,
    target_id: str = "",
    full_page: bool = False,
    format: str = "png",
) -> dict[str, Any]:
    """Capture a base64 PNG/JPEG screenshot of a running Chromium tab."""
    return _request(
        "POST",
        f"/profiles/{profile_id}/pages/screenshot",
        json={"target_id": target_id, "full_page": full_page, "format": format},
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
