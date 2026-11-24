"""Selected exports must follow the original environment even after a rename."""

import pytest
from fastapi.testclient import TestClient

from moltskin.services.core import db, main
from moltskin.services.core.security import get_token


@pytest.fixture
def client(data_dir, monkeypatch):
    db.init_db()
    monkeypatch.setattr(main, "get_random_preset", lambda **_: {"navigator": {"platform": "Win32"}})
    with TestClient(main.app, headers={"Authorization": "Bearer " + get_token()}) as api:
        yield api


def test_selected_export_follows_ids_after_name_reuse(client):
    root = "/api/v1"
    profiles = root + "/profiles"
    proxy = client.post(root + "/proxies", json={
        "name": "local test", "host": "127.0.0.1", "port": 8443, "username": "team", "password": "excluded",
    }).raise_for_status().json()["data"]
    group = client.post(root + "/groups", json={"name": "Design"}).raise_for_status().json()["data"]
    fox = client.post(profiles, json={
        "name": "A", "proxy_id": proxy["id"], "group_id": group["id"], "tags": ["test"],
    }).raise_for_status().json()["data"]
    chrome = client.post(profiles, json={
        "name": "B", "engine": "chromium", "local_fonts": "restricted",
    }).raise_for_status().json()["data"]
    client.patch(profiles + "/" + fox["id"], json={"name": "renamed"}).raise_for_status()
    client.post(profiles, json={"name": "A", "engine": "chromium"}).raise_for_status()

    url = root + "/export/profiles"
    response = client.post(url, json={"ids": [fox["id"], chrome["id"], fox["id"]]})
    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    selected = response.json()["profiles"]
    all_rows = client.get(url).raise_for_status().json()["profiles"]
    assert [row["name"] for row in selected] == ["renamed", "B"]
    assert selected == [row for row in all_rows if row["name"] != "A"]
    assert len(all_rows) == 3
    assert selected[0]["group"] == "Design" and selected[0]["tags"] == ["test"]
    assert selected[0]["proxy"] == {"name": "local test", "protocol": "http", "host": "127.0.0.1",
                                     "port": 8443, "username": "team"}
    assert selected[1]["local_fonts"] == "restricted" and selected[1]["engine"] == "chromium"


def test_missing_or_empty_selection_never_downloads_partial_or_full_export(client):
    profile = client.post("/api/v1/profiles", json={"name": "present", "engine": "chromium"}).raise_for_status().json()["data"]
    url = "/api/v1/export/profiles"
    response = client.post(url, json={"ids": [profile["id"], "pf_deleted"]})
    assert response.status_code == 409
    assert response.json()["error"]["missing_ids"] == ["pf_deleted"]
    assert response.json()["error"]["code"] == "PROFILE_SELECTION_CHANGED"
    assert "profiles" not in response.json() and "Content-Disposition" not in response.headers
    assert client.post(url, json={"ids": []}).status_code == 422
    assert client.post(url, json={}).status_code == 422
