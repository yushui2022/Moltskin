"""Proxy checks fill blank GeoIP fields without overriding explicit choices."""

from fastapi.testclient import TestClient

from moltskin.services.core import db, main, proxy_checker
from moltskin.services.core.security import get_token


def fake_check(*args, **kwargs):
    return {
        "status": "ok", "exit_ip": "203.0.113.9", "country": "US",
        "timezone": "America/New_York", "locale": "en-US",
        "latitude": 40.7, "longitude": -74.0, "latency_ms": 40, "error": None,
    }


def test_proxy_check_aligns_blank_fields_and_preserves_explicit(data_dir, monkeypatch):
    db.init_db()
    monkeypatch.setattr(proxy_checker, "check_proxy", fake_check)
    headers = {"Authorization": f"Bearer {get_token()}"}
    with TestClient(main.app, headers=headers) as client:
        proxy = client.post("/api/v1/proxies", json={
            "name": "US", "host": "proxy.test", "port": 8080,
        }).raise_for_status().json()["data"]["id"]
        blank = client.post("/api/v1/profiles", json={
            "name": "blank", "engine": "chromium", "proxy_id": proxy,
        }).raise_for_status().json()["data"]
        explicit = client.post("/api/v1/profiles", json={
            "name": "explicit", "engine": "chromium", "proxy_id": proxy,
            "browser_timezone": "Asia/Tokyo", "browser_language": "ja-JP",
        }).raise_for_status().json()["data"]
        client.post(f"/api/v1/proxies/{proxy}/check").raise_for_status()

    with db.session() as conn:
        row = conn.execute("SELECT browser_timezone,browser_language FROM profiles WHERE id=?",
                           (blank["id"],)).fetchone()
        kept = conn.execute("SELECT browser_timezone,browser_language FROM profiles WHERE id=?",
                            (explicit["id"],)).fetchone()
    assert tuple(row) == ("America/New_York", "en-US")
    assert tuple(kept) == ("Asia/Tokyo", "ja-JP")


def test_align_endpoint_force_updates_profile(data_dir, monkeypatch):
    db.init_db()
    monkeypatch.setattr(proxy_checker, "check_proxy", fake_check)
    headers = {"Authorization": f"Bearer {get_token()}"}
    with TestClient(main.app, headers=headers) as client:
        proxy = client.post("/api/v1/proxies", json={
            "name": "US", "host": "proxy.test", "port": 8080,
        }).raise_for_status().json()["data"]["id"]
        profile = client.post("/api/v1/profiles", json={
            "name": "align", "engine": "chromium", "proxy_id": proxy,
            "browser_timezone": "Asia/Tokyo", "browser_language": "ja-JP",
        }).raise_for_status().json()["data"]
        result = client.post(f"/api/v1/profiles/{profile['id']}/align-proxy").raise_for_status().json()["data"]
        assert result["country"] == "US"
        assert result["browser_timezone"] == "America/New_York"
        assert result["browser_language"] == "en-US"
        updated = client.get(f"/api/v1/profiles/{profile['id']}").raise_for_status().json()["data"]
        assert updated["browser_timezone"] == "America/New_York"
        assert updated["browser_language"] == "en-US"
