import os

import pytest
from fastapi.testclient import TestClient

from chromium.native import default_binary, probe
from moltskin.services.core import main, platform_preview
from moltskin.services.core.security import get_token


ROUTE = "/api/v1/chromium/platform-preview"


def test_preview_honors_auth_input_validation_and_capacity(data_dir, monkeypatch):
    with TestClient(main.app) as client:
        assert client.post(ROUTE, json={}).status_code == 401
        client.headers["Authorization"] = "Bearer " + get_token()
        for payload in ({"url": "https://example.invalid"}, {"preview_architecture": "arbitrary"},
                        {"chromium_version": "/some/binary"}, {"preview_platform_version": "11 beta"}):
            assert client.post(ROUTE, json=payload).status_code == 422
        def busy(**kwargs):
            raise platform_preview.PreviewBusy
        monkeypatch.setattr(platform_preview, "measure", busy)
        response = client.post(ROUTE, json={})
        assert response.status_code == 429 and response.json()["error"]["code"] == "PLATFORM_PREVIEW_BUSY"


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium")
def test_isolated_platform_readings_and_return_to_native(data_dir):
    _, build = probe(default_binary())
    with TestClient(main.app, headers={"Authorization": "Bearer " + get_token()}) as client:
        def read(**values):
            response = client.post(ROUTE, json={"chromium_version": build, **values})
            return response.raise_for_status().json()["data"]

        before = read()
        assert before["platform"]
        platform = "Windows" if before["platform"] != "Windows" else "Linux"
        arch, name = ("x86_64", "x86") if before["architecture"] != "x86" else ("arm64", "arm")
        assert read(preview_platform=platform, preview_platform_version="15.0.0", preview_architecture=arch) == {
            "platform": platform, "platform_version": "15.0.0", "architecture": name, "bitness": "64",
        }
        assert read(preview_platform=platform) == {**before, "platform": platform, "platform_version": ""}
        assert read(preview_architecture=arch) == {**before, "architecture": name, "bitness": "64"}
        assert read() == before
