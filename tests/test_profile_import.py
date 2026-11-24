"""Portable environment configuration import, using an isolated local database."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moltskin.services.core import config, db, main
from moltskin.services.core.security import get_token


@pytest.fixture
def api(data_dir, monkeypatch):
    db.init_db()
    monkeypatch.setattr(main, "get_random_preset", lambda os: {"navigator": {"platform": os}})
    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}, raise_server_exceptions=True) as client:
        yield client


def import_rows(client, rows):
    result = client.post("/api/v1/import/profiles", json={"profiles": rows})
    assert result.status_code == 200, result.text
    return result.json()["data"]


def test_portable_import_skips_invalid_rows_without_losing_siblings(api):
    result = import_rows(api, [
        {"name": "work", "headless": "false"},
        "not an object",
        {"name": "failed", "group": "unused", "proxy": {"host": "localhost", "port": "bad"}},
        {"name": "bad preset", "fingerprint": {"preset": {"webgl": []}}},
        {"name": "work", "engine": "chromium", "local_fonts": "restricted", "group": "valid", "headless": True},
    ])
    assert result["created"] == 2
    assert result["renamed"] == [{"from": "work", "to": "work (2)"}]
    assert [row["row"] for row in result["skipped"]] == [2, 3, 4]
    assert "proxy.port" in result["skipped"][1]["reason"]
    with db.session() as conn:
        rows = conn.execute("SELECT name, engine, local_fonts, os, headless, user_data_dir FROM profiles ORDER BY name").fetchall()
        assert [tuple(row)[:5] for row in rows] == [
            ("work", "camoufox", "standard", "windows", 0),
            ("work (2)", "chromium", "restricted", "native", 1),
        ]
        assert [row["name"] for row in conn.execute("SELECT name FROM groups")] == ["valid"]
        assert not conn.execute("SELECT id FROM proxies").fetchall()
    assert set(config.PROFILES_DIR.iterdir()) == {Path(row["user_data_dir"]) for row in rows}


def test_round_trip_keeps_settings_and_distinguishes_proxy_protocols(api):
    proxy = {"host": "proxy.invalid", "port": 9000, "username": "example"}
    preset = {"preset": {"navigator": {"platform": "Linux"}, "screen": {"width": 1000, "height": 800}}}
    source = [
        {"name": "Firefox", "group": "group", "os": "linux", "remark": "工作", "tags": ["demo"],
         "headless": False, "fingerprint": preset, "proxy": {**proxy, "protocol": "http", "password": "ignored"}},
        {"name": "Firefox SOCKS", "proxy": {**proxy, "protocol": "socks5"}},
        {"name": "Chromium", "os": "native", "engine": "chromium", "local_fonts": "restricted", "headless": True},
    ]
    assert import_rows(api, source)["created"] == 3
    exported = api.get("/api/v1/export/profiles").json()["profiles"]
    entries = {entry["name"]: entry for entry in exported}
    assert entries["Firefox"]["fingerprint"] == preset
    assert entries["Firefox"]["proxy"]["protocol"] == "http"
    assert entries["Firefox SOCKS"]["proxy"]["protocol"] == "socks5"
    assert "password" not in entries["Firefox"]["proxy"]
    assert import_rows(api, [{**row, "name": row["name"] + " restored"} for row in exported])["created"] == 3
    second = {entry["name"]: entry for entry in api.get("/api/v1/export/profiles").json()["profiles"]}
    for name, entry in entries.items():
        assert second[name + " restored"] == {**entry, "name": name + " restored"}
    with db.session() as conn:
        assert conn.execute("SELECT count(*) FROM proxies").fetchone()[0] == 2
        assert not any(row[0] for row in conn.execute("SELECT password FROM proxies"))


def test_rejected_directory_rolls_back_only_its_row(api, monkeypatch):
    collision = config.PROFILES_DIR / "pf_collision"
    collision.mkdir()
    (collision / "keep").touch()
    ids = iter(["pf_collision", "pf_new"])
    real_id = main.new_id
    monkeypatch.setattr(main, "new_id", lambda prefix: next(ids) if prefix == "pf" else real_id(prefix))
    result = import_rows(api, [
        {"name": "collision", "group": "unused", "proxy": {"host": "proxy.invalid", "port": 80}},
        {"name": "valid"},
    ])
    assert result["created"] == 1 and [row["row"] for row in result["skipped"]] == [1]
    assert (collision / "keep").exists()
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM proxies").fetchone()[0] == 0


def test_concurrent_imports_allocate_distinct_names_and_share_references(api):
    row = {"name": "project", "group": "team", "proxy": {"host": "proxy.invalid", "port": 80}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: import_rows(api, [row]), range(2)))
    assert all(r["created"] == 1 and not r["skipped"] for r in results)
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM proxies").fetchone()[0] == 1
        rows = conn.execute("SELECT name, user_data_dir FROM profiles ORDER BY name").fetchall()
    assert [row["name"] for row in rows] == ["project", "project (2)"]
    assert len({row["user_data_dir"] for row in rows}) == 2
