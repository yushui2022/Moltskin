"""Smoke tests that do not need the Camoufox browser binary."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Make `import moltskin` work when running from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MOLTSKIN_DATA_DIR", tempfile.mkdtemp(prefix="moltskin-test-"))

from fastapi.testclient import TestClient  # noqa: E402

from moltskin.services.core import main  # noqa: E402
from moltskin.services.core.main import _ua_os, fingerprint_preview  # noqa: E402
from moltskin.services.core.security import get_token  # noqa: E402


def _client() -> TestClient:
    return TestClient(main.app)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_token()}"}


def test_health_is_public():
    with _client() as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_requires_token():
    with _client() as client:
        response = client.get("/api/v1/profiles")
    assert response.status_code == 401


def test_ua_os_detection():
    assert _ua_os("Mozilla/5.0 (Windows NT 10.0; Win64; x64)") == "windows"
    assert _ua_os("Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15)") == "macos"
    assert _ua_os("Mozilla/5.0 (X11; Linux x86_64)") == "linux"
    assert _ua_os("curl/8.0") is None


def test_fingerprint_preview():
    preset = {
        "navigator": {"userAgent": "Mozilla/5.0 (Windows NT 10.0; rv:140.0) Gecko/20100101 Firefox/140.0",
                      "platform": "Win32", "hardwareConcurrency": 8},
        "screen": {"width": 1920, "height": 1080, "colorDepth": 24},
        "webgl": {"unmaskedVendor": "Google Inc. (Intel)", "unmaskedRenderer": "ANGLE (Intel)"},
    }
    preview = fingerprint_preview({"preset": preset}, "windows")
    assert preview is not None
    assert preview["platform"] == "Win32"
    assert preview["screen"] == "1920x1080"
    assert preview["webgl_renderer"] == "ANGLE (Intel)"


def test_create_profile_generates_preview():
    with _client() as client:
        response = client.post("/api/v1/profiles", headers=_auth_headers(), json={"name": "smoke-test", "os": "windows"})
        assert response.status_code == 201
        profile = response.json()["data"]
        assert profile["fingerprint_preview"] is not None
        assert profile["fingerprint_preview"]["platform"] == "Win32"

        deleted = client.delete(f"/api/v1/profiles/{profile['id']}?delete_data=true", headers=_auth_headers())
        assert deleted.status_code == 200
