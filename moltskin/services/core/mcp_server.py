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
        "Moltskin manages isolated browser profiles built on Camoufox. "
        "Use these tools to list, create, launch and stop profiles, manage proxies, "
        "run consistency checks and fingerprint self-tests. "
        "A profile must be stopped before deleting it or running a self-test."
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
    os: str = "windows",
    proxy_id: str | None = None,
    tags: list[str] | None = None,
    remark: str = "",
    headless: bool = False,
) -> dict[str, Any]:
    """Create an isolated profile. A stable fingerprint preset is generated automatically."""
    return _request(
        "POST",
        "/profiles",
        json={
            "name": name,
            "os": os,
            "proxy_id": proxy_id,
            "tags": tags or [],
            "remark": remark,
            "headless": headless,
        },
    )


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
    """Check fingerprint, OS/UA, proxy and timezone consistency before launch."""
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
    """Camoufox / Playwright versions and installed browser builds."""
    return _request("GET", "/engine")


if __name__ == "__main__":
    mcp.run(transport="stdio")
