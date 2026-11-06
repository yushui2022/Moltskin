"""Minimal Python SDK for the Moltskin local API."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]


class MoltskinError(RuntimeError):
    """An error returned by the Moltskin API."""

    def __init__(self, code: str, message: str, status: int = 0):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


def _default_token() -> str:
    token = os.environ.get("MOLTSKIN_TOKEN")
    if token:
        return token
    data_dir = Path(os.environ.get("MOLTSKIN_DATA_DIR", ROOT / "data"))
    token_file = data_dir / "api.token"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    raise MoltskinError("TOKEN_NOT_FOUND", f"API token not found at {token_file}")


class MoltskinClient:
    """Synchronous client for a running Moltskin service."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 180.0,
    ):
        self.base_url = (base_url or os.environ.get("MOLTSKIN_API", "http://127.0.0.1:8765")).rstrip("/")
        self._client = httpx.Client(
            base_url=f"{self.base_url}/api/v1",
            headers={"Authorization": f"Bearer {token or _default_token()}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MoltskinClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                error = response.json().get("error") or {}
            except Exception:  # noqa: BLE001
                error = {}
            raise MoltskinError(
                error.get("code", "HTTP_ERROR"),
                error.get("message", response.text),
                response.status_code,
            )
        body = response.json()
        return body.get("data", body)

    # Profiles -----------------------------------------------------------------

    def list_profiles(self, status: str = "", q: str = "", page_size: int = 200) -> list[dict[str, Any]]:
        data = self._request("GET", "/profiles", params={"page_size": page_size, "status": status, "q": q})
        return data["items"]

    def get_profile(self, profile_id: str) -> dict[str, Any]:
        return self._request("GET", f"/profiles/{profile_id}")

    def create_profile(
        self,
        name: str,
        os: str = "windows",
        proxy_id: str | None = None,
        tags: list[str] | None = None,
        remark: str = "",
        headless: bool = False,
    ) -> dict[str, Any]:
        return self._request(
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

    def delete_profile(self, profile_id: str, delete_data: bool = True) -> dict[str, Any]:
        return self._request("DELETE", f"/profiles/{profile_id}", params={"delete_data": delete_data})

    def duplicate_profile(self, profile_id: str) -> dict[str, Any]:
        return self._request("POST", f"/profiles/{profile_id}/duplicate")

    def launch(self, profile_id: str) -> dict[str, Any]:
        return self._request("POST", f"/profiles/{profile_id}/launch")

    def stop(self, profile_id: str) -> dict[str, Any]:
        return self._request("POST", f"/profiles/{profile_id}/stop")

    def status(self, profile_id: str) -> dict[str, Any]:
        return self._request("GET", f"/profiles/{profile_id}/status")

    def verify(self, profile_id: str) -> dict[str, Any]:
        return self._request("POST", f"/profiles/{profile_id}/verify")

    def self_test(self, profile_id: str) -> dict[str, Any]:
        return self._request("POST", f"/profiles/{profile_id}/self-test")

    def batch(
        self,
        action: str,
        ids: list[str],
        group_id: str | None = None,
        tags: list[str] | None = None,
        delete_data: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/profiles/batch",
            json={
                "action": action,
                "ids": ids,
                "group_id": group_id,
                "tags": tags,
                "delete_data": delete_data,
            },
        )

    # Proxies ------------------------------------------------------------------

    def list_proxies(self) -> list[dict[str, Any]]:
        return self._request("GET", "/proxies")

    def create_proxy(
        self,
        name: str,
        host: str,
        port: int,
        protocol: str = "http",
        username: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
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

    def check_proxy(self, proxy_id: str) -> dict[str, Any]:
        return self._request("POST", f"/proxies/{proxy_id}/check")

    # Groups / misc ------------------------------------------------------------

    def list_groups(self) -> list[dict[str, Any]]:
        return self._request("GET", "/groups")

    def create_group(self, name: str) -> dict[str, Any]:
        return self._request("POST", "/groups", json={"name": name})

    def engine_info(self) -> dict[str, Any]:
        return self._request("GET", "/engine")

    def export_profiles(self) -> dict[str, Any]:
        response = self._client.get("/export/profiles")
        response.raise_for_status()
        return response.json()

    def import_profiles(self, profiles: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/import/profiles", json={"profiles": profiles})
