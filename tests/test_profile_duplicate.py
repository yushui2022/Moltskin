"""Duplicate naming and filesystem/transaction consistency in a temporary workspace."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moltskin.services.core import config, db, main
from moltskin.services.core.security import get_token


@pytest.fixture
def client(data_dir):
    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as api:
        yield api


def create(client, name):
    result = client.post("/api/v1/profiles", json={"name": name, "engine": "chromium"})
    result.raise_for_status()
    return result.json()["data"]


def test_concurrent_copies_allocate_names_and_keep_empty_independent_directories(client):
    source = create(client, "验收")
    original = Path(source["user_data_dir"])
    (original / "marker").touch()
    url = f"/api/v1/profiles/{source['id']}/duplicate"

    def duplicate():
        return client.post(url).raise_for_status().json()["data"]

    first = duplicate()
    assert first["name"] == "验收 副本"
    with ThreadPoolExecutor(max_workers=4) as workers:
        futures = [workers.submit(duplicate) for _ in range(4)]
        copies = [future.result() for future in futures]
    assert {p["name"] for p in copies} == {f"验收 副本 ({i})" for i in range(2, 6)}
    directories = {Path(p["user_data_dir"]) for p in [first, *copies]}
    assert len(directories) == 5
    assert all(list(path.iterdir()) == [] for path in directories)
    assert set(config.PROFILES_DIR.iterdir()) == directories | {original}
    assert (original / "marker").is_file()


def test_long_names_keep_the_entire_copy_suffix(client):
    name = "窗" * 120
    source = create(client, name)
    create(client, name[:117] + " 副本")
    copy = client.post(f"/api/v1/profiles/{source['id']}/duplicate").raise_for_status().json()["data"]
    assert copy["name"] == name[:113] + " 副本 (2)"


def test_failed_commit_cleans_up_only_the_new_directory(client):
    source = create(client, "恢复")
    original = Path(source["user_data_dir"])
    marker = original / "marker"
    marker.touch()
    with db.session() as conn:
        conn.executescript("""
            CREATE TABLE commit_guard (
                profile_id TEXT REFERENCES profiles(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER reject_copy AFTER INSERT ON profiles BEGIN
                INSERT INTO commit_guard VALUES ('missing');
            END;
        """)
    with pytest.raises(sqlite3.IntegrityError):
        client.post(f"/api/v1/profiles/{source['id']}/duplicate")
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 1
    assert set(config.PROFILES_DIR.iterdir()) == {original}
    assert marker.is_file()
