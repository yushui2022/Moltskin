"""The single-window creation form can preview its assigned preset before saving."""

from itertools import count
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from moltskin.services.core import config, db, main
from moltskin.services.core.security import get_token

PROFILES = "/api/v1/profiles"
PREVIEW = "/api/v1/profile-preset-preview"


@pytest.fixture
def client(data_dir, monkeypatch):
    db.init_db()
    serial = count(1)

    def sample(os):
        number = next(serial)
        return {
            "navigator": {"platform": os, "userAgent": f"test-{number}"},
            "screen": {"width": 1280, "height": 720},
            "sample_id": number,
        }

    monkeypatch.setattr(main, "get_random_preset", sample)
    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as api:
        yield api


def preview(client, draft_id, os="windows"):
    result = client.post(PREVIEW, json={"draft_id": draft_id, "os": os})
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert set(data) == {"draft_id", "preview"}
    assert data["draft_id"] == draft_id
    assert not ({"preset", "navigator", "sample_id"} & data["preview"].keys())
    return data["preview"]


def test_preview_saves_the_same_full_preset_and_survives_a_failed_save(client):
    original = client.post(PROFILES, json={"name": "Taken"}).json()["data"]
    assert original["fingerprint_preview"]["user_agent"] == "test-1"
    draft = str(uuid4())
    first = preview(client, draft)
    assert first["user_agent"] == "test-2"
    assert preview(client, draft) == first
    assert client.post(PROFILES, json={"name": "Taken", "preset_draft_id": draft}).status_code == 409

    res = client.post(PROFILES, json={"name": "New window", "preset_draft_id": draft})
    assert res.status_code == 201, res.text
    assert res.json()["data"]["fingerprint_preview"] == first
    exported = client.get("/api/v1/export/profiles").json()["profiles"]
    stored = next(p for p in exported if p["name"] == "New window")
    assert stored["fingerprint"]["preset"]["sample_id"] == 2
    assert client.post(PROFILES, json={"name": "Replay", "preset_draft_id": draft}).status_code == 409


def test_abandoned_previews_leave_profiles_and_their_directories_untouched(client):
    before = client.post(PROFILES, json={"name": "Keep"}).json()["data"]
    draft_a, draft_b = str(uuid4()), str(uuid4())
    win = preview(client, draft_a)
    other = preview(client, draft_b)
    assert win != other
    assert preview(client, draft_a, "linux")["os"] == "linux"
    assert preview(client, draft_a) == win
    assert preview(client, draft_b) == other
    assert client.get(PROFILES).json()["data"]["items"] == [before]
    assert set(config.PROFILES_DIR.iterdir()) == {Path(before["user_data_dir"])}


def test_rejects_unknown_wrong_engine_and_wrong_os_without_creating_a_profile(client):
    draft = str(uuid4())
    preview(client, draft)
    for extra in ({"preset_draft_id": str(uuid4())}, {"os": "linux"}, {"engine": "chromium"}):
        result = client.post(PROFILES, json={"name": "Rejected", "preset_draft_id": draft, **extra})
        assert 400 <= result.status_code < 500, result.text
    assert client.post(PREVIEW, json={"draft_id": draft, "os": "invalid"}).status_code == 422
    assert client.get(PROFILES).json()["data"]["total"] == 0
    assert not list(config.PROFILES_DIR.iterdir())
