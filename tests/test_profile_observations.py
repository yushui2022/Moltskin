"""End-to-end verification responses for synthetic, local browser readings."""

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moltskin.services.core import config, db, main
from moltskin.services.core.security import get_token


PRESET = {
    "navigator": {"userAgent": "Mozilla/5.0 (Windows NT 10.0)", "platform": "Win32", "hardwareConcurrency": 4},
    "screen": {"width": 1920, "height": 1080, "colorDepth": 24},
    "webgl": {"unmaskedVendor": "Example vendor", "unmaskedRenderer": "Example renderer"},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("DB_PATH", "PROFILES_DIR", "RUN_DIR", "LOG_DIR", "SELFTEST_DIR", "TOKEN_PATH"):
        monkeypatch.setattr(config, name, tmp_path / getattr(config, name).name)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FORCE_HEADLESS", False)
    db.init_db()
    monkeypatch.setattr(main, "installed_verstr", lambda: "158.0")
    monkeypatch.setattr(main.proxy_checker, "check_and_save", lambda proxy: {"status": "ok"})
    # The app's scheduler and process housekeeping are unnecessary for these API calls.
    api = TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"})
    yield api
    api.close()


def create_profile(client, name, proxy_id=None):
    response = client.post("/api/v1/profiles", json={
        "name": name, "fingerprint": {"preset": PRESET}, "proxy_id": proxy_id,
    })
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def verify(client, profile_id):
    response = client.post(f"/api/v1/profiles/{profile_id}/verify")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    return data, {check["name"]: check for check in data["checks"] if check["source"] == "runtime"}


def record_sample(profile_id, session_id, values, proxy_id=None):
    with db.session() as conn:
        conn.execute("INSERT INTO sessions(id, profile_id, state, started_at) VALUES (?, ?, 'stopped', ?)",
                     (session_id, profile_id, db.now()))
        conn.execute("UPDATE profiles SET last_session_id=? WHERE id=?", (session_id, profile_id))
    main.observation.save({
        "profile_id": profile_id,
        "session_id": session_id,
        "status_file": str(config.RUN_DIR / f"{session_id}.json"),
        "os": "windows",
        "fingerprint": {"preset": PRESET},
        "headless": False,
        "proxy": {"id": proxy_id} if proxy_id else None,
    }, values)


def test_preset_alone_cannot_pass_runtime_checks(client):
    data, checks = verify(client, create_profile(client, "unopened"))
    assert data["measured_at"] is None
    assert checks["CPU 核数"]["expected"] == 4
    assert checks["屏幕尺寸"]["expected"] == "1920x1080"
    assert checks["WebGL 渲染器"]["expected"] == "Example renderer"
    assert all(check["status"] == "unverified" and check["observed"] is None for check in checks.values())


def test_reports_observed_cpu_drift_and_missing_webgl(client):
    profile_id = create_profile(client, "partial")
    record_sample(profile_id, "ss_123456abcdef", {"hardware_concurrency": 18, "screen": "1920x1080"})
    data, checks = verify(client, profile_id)
    assert datetime.fromisoformat(data["measured_at"]).tzinfo is not None
    assert data["ok"] is False
    cpu = checks["CPU 核数"]
    assert (cpu["expected"], cpu["observed"], cpu["status"]) == (4, 18, "mismatch")
    assert checks["屏幕尺寸"]["status"] == "match"
    renderer = checks["WebGL 渲染器"]
    assert (renderer["expected"], renderer["observed"], renderer["status"]) == (
        "Example renderer", None, "unverified",
    )


@pytest.mark.parametrize(("cores", "ok"), [(4, True), (18, False)])
def test_complete_sample_controls_overall_verdict(client, cores, ok):
    proxy = client.post("/api/v1/proxies", json={"name": "loopback", "host": "127.0.0.1", "port": 8080})
    assert proxy.status_code == 201, proxy.text
    proxy_id = proxy.json()["data"]["id"]
    profile_id = create_profile(client, "complete", proxy_id)
    record_sample(profile_id, "ss_123456abcdef", {
        "user_agent": PRESET["navigator"]["userAgent"], "platform": "Win32", "hardware_concurrency": cores,
        "screen": "1920x1080", "color_depth": 24, "webgl_vendor": "Example vendor", "webgl_renderer": "Example renderer",
    }, proxy_id)
    data, checks = verify(client, profile_id)
    assert all(row["status"] == "match" for name, row in checks.items() if name != "CPU 核数")
    assert data["ok"] is ok


def test_samples_belong_to_one_profile_session_and_configuration(client):
    first = create_profile(client, "first")
    second = create_profile(client, "second")
    session_id = "ss_fe9876543210"
    record_sample(first, session_id, {"hardware_concurrency": 4})
    assert verify(client, first)[1]["CPU 核数"]["status"] == "match"

    # A stale/incorrect last-session reference must never expose another profile's sample.
    with db.session() as conn:
        conn.execute("UPDATE profiles SET last_session_id=? WHERE id=?", (session_id, second))
    assert verify(client, second)[0]["measured_at"] is None

    response = client.patch(f"/api/v1/profiles/{first}", json={"remark": "new display note"})
    assert response.status_code == 200
    assert verify(client, first)[1]["CPU 核数"]["status"] == "match"
    response = client.patch(f"/api/v1/profiles/{first}", json={"headless": True})
    assert response.status_code == 200
    data, checks = verify(client, first)
    assert data["measured_at"] is None
    assert checks["CPU 核数"]["status"] == "unverified"
