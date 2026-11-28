"""Profile settings must survive create, duplicate, export and import."""

import pytest
from fastapi.testclient import TestClient

from moltskin.services.core import config, db, main
from moltskin.services.core.security import get_token


@pytest.fixture
def api(data_dir):
    db.init_db()
    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"},
                    raise_server_exceptions=True) as client:
        yield client


def settings() -> dict:
    return {
        "engine": "chromium",
        "local_fonts": "restricted",
        "startup_page": "custom",
        "startup_urls": ["https://example.com/store", "https://example.org/orders"],
        "block_images": True,
        "remark": "contract",
        "tags": ["work", "test"],
        "headless": True,
        "preview_platform": "Windows",
        "preview_platform_version": "10.0.0",
        "preview_architecture": "x86_64",
        "media_capture": "blocked",
        "geolocation": "custom",
        "geolocation_latitude": 34.0522,
        "geolocation_longitude": -118.2437,
        "geolocation_accuracy": 22.5,
        "webrtc_policy": "proxy",
        "hardware_reporting": "limited",
        "canvas_readback": "blocked",
        "webgl_debug_info": "blocked",
        "chromium_version": "",
        "chromium_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Moltskin/1.0",
        "chromium_js_heap_mb": 256,
        "chromium_mute_audio": True,
        "chromium_renderer_limit": 4,
        "chromium_hardware_concurrency": 8,
        "chromium_device_memory": 4,
        "browser_language": "en-US",
        "browser_timezone": "UTC",
        "browser_window_size": "1440x900",
        "chromium_screen": "1920x1080x24@1.25",
        "chromium_platform": "windows:10.0.0:x86_64",
        "chromium_webgl": config.CHROMIUM_WEBGL_PRESETS[0],
        "chromium_canvas_noise": True,
        "chromium_audio_noise": True,
    }


def assert_settings(profile: dict, expected: dict) -> None:
    for key, value in expected.items():
        assert profile[key] == value, (key, profile.get(key), value)
    assert profile["os"] == "native"


def test_profile_model_and_export_field_sets_match(api):
    profile_fields = set(main.ProfileCreate.model_fields)
    update_fields = set(main.ProfileUpdate.model_fields)
    assert update_fields == profile_fields

    created = api.post("/api/v1/profiles", json={"name": "field inventory", "engine": "chromium"})
    assert created.status_code == 201
    profile = created.json()["data"]
    # Relationships are represented by group/proxy objects in the export.
    exportable = profile_fields - {"group_id", "proxy_id"}
    assert (exportable - {"fingerprint"}) <= set(profile)

    exported = api.get("/api/v1/export/profiles").raise_for_status().json()["profiles"][0]
    assert exportable <= set(exported)


def test_all_settings_survive_duplicate_export_and_import(api):
    expected = settings()
    created = api.post("/api/v1/profiles", json={"name": "contract source", **expected})
    assert created.status_code == 201, created.text
    profile = created.json()["data"]
    assert_settings(profile, expected)

    duplicated = api.post(f"/api/v1/profiles/{profile['id']}/duplicate")
    assert duplicated.status_code == 200, duplicated.text
    assert_settings(duplicated.json()["data"], expected)

    exported = api.get("/api/v1/export/profiles").raise_for_status().json()
    assert exported["version"] == 17
    item = next(item for item in exported["profiles"] if item["name"] == "contract source")
    for key, value in expected.items():
        assert item[key] == value, (key, item.get(key), value)

    restored_item = {**item, "name": "contract restored"}
    imported = api.post("/api/v1/import/profiles", json={"version": 17, "profiles": [restored_item]})
    assert imported.status_code == 200, imported.text
    assert imported.json()["data"]["created"] == 1
    profiles = api.get("/api/v1/profiles").raise_for_status().json()["data"]["items"]
    restored = next(profile for profile in profiles if profile["name"] == "contract restored")
    assert_settings(restored, expected)


def test_profile_migration_adds_current_columns_and_upgrades_checks(api):
    with db.session() as conn:
        for column in ("chromium_screen", "chromium_platform", "chromium_webgl",
                       "chromium_canvas_noise", "chromium_audio_noise",
                       "chromium_hardware_concurrency", "chromium_device_memory",
                       "geolocation_latitude", "geolocation_longitude", "geolocation_accuracy",
                       "webrtc_policy", "startup_urls"):
            conn.execute(f"ALTER TABLE profiles DROP COLUMN {column}")
    db.init_db()
    with db.session() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(profiles)")}
        proxy_columns = {row["name"] for row in conn.execute("PRAGMA table_info(proxies)")}
    assert {"chromium_screen", "chromium_platform", "chromium_webgl",
            "chromium_canvas_noise", "chromium_audio_noise",
            "chromium_hardware_concurrency", "chromium_device_memory",
            "geolocation_latitude", "geolocation_longitude", "geolocation_accuracy",
            "webrtc_policy", "startup_urls"} <= columns
    assert {"latitude", "longitude"} <= proxy_columns

    created = api.post("/api/v1/profiles", json={
        "name": "migrated check modes",
        "engine": "chromium",
        "geolocation": "auto",
        "webrtc_policy": "proxy",
        "startup_page": "restore",
    })
    assert created.status_code == 201, created.text
