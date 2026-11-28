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

    def list_profiles(
        self,
        status: str = "",
        q: str = "",
        page_size: int = 200,
        group_id: str = "",
        tag: str = "",
        field: str = "all",
        op: str = "contains",
        sort: str = "last_opened",
        order: str = "desc",
    ) -> list[dict[str, Any]]:
        """List profiles.

        status: ready, running, crashed, draft, opened, unopened, stopped.
        field:  all, name, remark, proxy, group; op: contains, not_contains, equals.
        sort:   last_opened, created_at, name, status, open_count.
        """
        data = self._request(
            "GET",
            "/profiles",
            params={
                "page_size": page_size,
                "status": status,
                "q": q,
                "group_id": group_id,
                "tag": tag,
                "field": field,
                "op": op,
                "sort": sort,
                "order": order,
            },
        )
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

    def trash_profile(self, profile_id: str) -> dict[str, Any]:
        """Move a stopped profile to the recycle bin, keeping its local data."""
        return self._request("POST", f"/profiles/{profile_id}/trash")

    def list_trashed_profiles(self) -> list[dict[str, Any]]:
        return self._request("GET", "/trash/profiles")

    def restore_profile(self, profile_id: str) -> dict[str, Any]:
        return self._request("POST", f"/trash/profiles/{profile_id}/restore")

    def purge_trashed_profile(self, profile_id: str) -> dict[str, Any]:
        """Permanently delete a recycled profile and its local browser data."""
        return self._request("DELETE", f"/trash/profiles/{profile_id}")

    def duplicate_profile(self, profile_id: str) -> dict[str, Any]:
        return self._request("POST", f"/profiles/{profile_id}/duplicate")

    def reset_fingerprint(self, profile_id: str) -> dict[str, Any]:
        """New fingerprint preset, cookies kept. The profile must be stopped."""
        return self._request("POST", f"/profiles/{profile_id}/reset-fingerprint")

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

    # Cookies -----------------------------------------------------------------

    def get_cookies(self, profile_id: str, domain: str = "") -> list[dict[str, Any]]:
        data = self._request("GET", f"/profiles/{profile_id}/cookies", params={"domain": domain})
        return data["cookies"]

    def set_cookies(self, profile_id: str, cookies: Any, mode: str = "merge") -> dict[str, Any]:
        """cookies 可以是列表、storage_state dict 或 Netscape cookies.txt 文本。"""
        payload = {"text": cookies, "mode": mode} if isinstance(cookies, str) else {"cookies": cookies, "mode": mode}
        return self._request("PUT", f"/profiles/{profile_id}/cookies", json=payload)

    def export_cookies(self, profile_id: str, format: str = "json") -> str:
        response = self._client.get(f"/profiles/{profile_id}/cookies/export", params={"format": format})
        response.raise_for_status()
        return response.text

    def copy_cookies(self, profile_id: str, source_id: str, mode: str = "merge") -> dict[str, Any]:
        return self._request(
            "POST", f"/profiles/{profile_id}/cookies/copy-from/{source_id}", params={"mode": mode}
        )

    def clear_cookies(self, profile_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/profiles/{profile_id}/cookies")

    # Extensions --------------------------------------------------------------

    def list_extensions(self) -> list[dict[str, Any]]:
        return self._request("GET", "/extensions")

    def upload_extension(self, filename: str, content: bytes) -> dict[str, Any]:
        import base64

        return self._request(
            "POST",
            "/extensions",
            json={"filename": filename, "content": base64.b64encode(content).decode()},
        )

    def delete_extension(self, ext_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/extensions/{ext_id}")

    def get_profile_extensions(self, profile_id: str) -> list[str]:
        return self._request("GET", f"/profiles/{profile_id}/extensions")["ids"]

    def set_profile_extensions(self, profile_id: str, ids: list[str]) -> list[str]:
        return self._request("PUT", f"/profiles/{profile_id}/extensions", json={"ids": ids})["ids"]

    # State sync / backup -----------------------------------------------------

    def state_size(self, profile_id: str) -> dict[str, Any]:
        return self._request("GET", f"/profiles/{profile_id}/state-size")

    def sync_state(
        self, profile_id: str, source_id: str, parts: str = "cookies,storage"
    ) -> dict[str, Any]:
        return self._request(
            "POST", f"/profiles/{profile_id}/sync-from/{source_id}", params={"parts": parts}
        )

    def download_backup(self, include_profiles: bool = False) -> bytes:
        response = self._client.get(
            "/backup", params={"include_profiles": "true" if include_profiles else "false"}
        )
        response.raise_for_status()
        return response.content

    def restore_backup(self, archive: bytes) -> dict[str, Any]:
        response = self._client.post(
            "/backup/restore", files={"file": ("backup.zip", archive, "application/zip")}
        )
        if response.status_code >= 400:
            error = response.json().get("error", {})
            raise MoltskinError(error.get("code", "HTTP_ERROR"), error.get("message", response.text))
        return response.json()["data"]

    # Schedules ---------------------------------------------------------------

    def list_schedules(self) -> list[dict[str, Any]]:
        return self._request("GET", "/schedules")

    def create_schedule(
        self,
        name: str,
        action: str = "launch",
        time_of_day: str = "09:00",
        weekdays: list[int] | None = None,
        target_type: str = "all",
        target_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
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

    def run_schedule(self, schedule_id: str) -> dict[str, Any]:
        return self._request("POST", f"/schedules/{schedule_id}/run")

    def delete_schedule(self, schedule_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/schedules/{schedule_id}")

    def batch_async(self, action: str, ids: list[str]) -> dict[str, Any]:
        return self._request("POST", "/profiles/batch-async", json={"action": action, "ids": ids})

    def job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/jobs/{job_id}")

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

    def check_all_proxies(self) -> list[dict[str, Any]]:
        ids = [proxy["id"] for proxy in self.list_proxies()]
        return self._request("POST", "/proxies/check-batch", json={"ids": ids})

    # Groups / misc ------------------------------------------------------------

    def list_groups(self) -> list[dict[str, Any]]:
        return self._request("GET", "/groups")

    def create_group(self, name: str) -> dict[str, Any]:
        return self._request("POST", "/groups", json={"name": name})

    def rename_group(self, group_id: str, name: str) -> dict[str, Any]:
        return self._request("PATCH", f"/groups/{group_id}", json={"name": name})

    def delete_group(self, group_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/groups/{group_id}")

    def list_tags(self) -> list[dict[str, Any]]:
        return self._request("GET", "/tags")

    def stats(self) -> dict[str, Any]:
        return self._request("GET", "/stats")

    def logs(self, page: int = 1, page_size: int = 50, action: str = "") -> list[dict[str, Any]]:
        return self._request(
            "GET", "/logs", params={"page": page, "page_size": page_size, "action": action}
        )

    def engine_info(self) -> dict[str, Any]:
        return self._request("GET", "/engine")

    def export_profiles(self) -> dict[str, Any]:
        response = self._client.get("/export/profiles")
        response.raise_for_status()
        return response.json()

    def import_profiles(self, profiles: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
        """Import a complete configuration export or a legacy list of profiles."""
        payload = {"profiles": profiles} if isinstance(profiles, list) else profiles
        return self._request("POST", "/import/profiles", json=payload)

    # Page-level control (Chromium) -------------------------------------------

    def list_pages(self, profile_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/profiles/{profile_id}/pages")["pages"]

    def new_page(self, profile_id: str, url: str = "about:blank") -> dict[str, Any]:
        return self._request("POST", f"/profiles/{profile_id}/pages", json={"url": url})

    def close_page(self, profile_id: str, target_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/profiles/{profile_id}/pages/{target_id}")

    def navigate(
        self,
        profile_id: str,
        url: str,
        *,
        target_id: str = "",
        wait_until: str = "load",
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/profiles/{profile_id}/pages/navigate",
            json={
                "url": url,
                "target_id": target_id,
                "wait_until": wait_until,
                "timeout": timeout,
            },
        )

    def snapshot(
        self,
        profile_id: str,
        *,
        target_id: str = "",
        max_elements: int = 120,
        include_text: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/profiles/{profile_id}/pages/snapshot",
            json={
                "target_id": target_id,
                "max_elements": max_elements,
                "include_text": include_text,
            },
        )

    def click(
        self,
        profile_id: str,
        selector: str | None = None,
        *,
        x: float | None = None,
        y: float | None = None,
        target_id: str = "",
        button: str = "left",
        click_count: int = 1,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/profiles/{profile_id}/pages/click",
            json={
                "selector": selector,
                "x": x,
                "y": y,
                "target_id": target_id,
                "button": button,
                "click_count": click_count,
            },
        )

    def fill(
        self,
        profile_id: str,
        selector: str,
        value: str,
        *,
        target_id: str = "",
        clear: bool = True,
        submit: bool = False,
    ) -> dict[str, Any]:
        return self._request(
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

    def press_key(self, profile_id: str, key: str, *, target_id: str = "") -> dict[str, Any]:
        return self._request(
            "POST",
            f"/profiles/{profile_id}/pages/press",
            json={"key": key, "target_id": target_id},
        )

    def evaluate(
        self,
        profile_id: str,
        expression: str,
        *,
        target_id: str = "",
        isolated: bool = False,
        await_promise: bool = True,
    ) -> Any:
        return self._request(
            "POST",
            f"/profiles/{profile_id}/pages/evaluate",
            json={
                "expression": expression,
                "target_id": target_id,
                "isolated": isolated,
                "await_promise": await_promise,
            },
        )

    def screenshot(
        self,
        profile_id: str,
        *,
        target_id: str = "",
        full_page: bool = False,
        format: str = "png",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/profiles/{profile_id}/pages/screenshot",
            json={"target_id": target_id, "full_page": full_page, "format": format},
        )
