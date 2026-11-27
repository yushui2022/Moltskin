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


def test_chromium_proxy_round_trip_and_rejected_imports(api):
    endpoint = {"protocol": "socks5", "host": "office.invalid", "port": 1080}
    row = {"name": "Assigned network", "engine": "chromium", "proxy": endpoint}
    result = import_rows(api, [row, {**row, "proxy": {**endpoint, "password": "test-only"}},
                               {**row, "proxy": None, "proxy_id": "px_old"}])
    assert result["created"] == 2 and [r["row"] for r in result["skipped"]] == [3]
    assert "test-only" not in str(result)
    exported = api.get("/api/v1/export/profiles").raise_for_status().json()
    assert exported["version"] == 8
    assert endpoint.items() <= exported["profiles"][0]["proxy"].items()
    assert "password" not in exported["profiles"][0]["proxy"]
    assert api.post("/api/v1/import/profiles", json=exported).raise_for_status().json()["data"]["created"] == 2
    profiles = api.get("/api/v1/profiles").raise_for_status().json()["data"]["items"]
    duplicate = api.post("/api/v1/profiles/" + profiles[0]["id"] + "/duplicate").raise_for_status().json()["data"]
    assert {profile["proxy"]["id"] for profile in profiles} == {duplicate["proxy"]["id"]}


def test_import_keeps_unversioned_and_current_files_compatible(api):
    rows = [{"name": "portable", "engine": "chromium"}]
    for payload in (rows, {"profiles": rows}, {"version": 1, "profiles": rows},
                    {"data": {"profiles": rows}}, {"version": 1, "data": {"version": 1, "profiles": rows}}):
        response = api.post("/api/v1/import/profiles", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["created"] == 1
    exported = api.get("/api/v1/export/profiles").raise_for_status().json()
    assert exported["version"] == 1
    assert api.post("/api/v1/import/profiles", json=exported).raise_for_status().json()["data"]["created"] == 5


def test_export_version_preserves_required_capture_restriction(api):
    profiles = []
    for mode in ("standard", "blocked"):
        created = api.post("/api/v1/profiles", json={"name": mode, "engine": "chromium", "media_capture": mode})
        profiles.append(created.raise_for_status().json()["data"]["id"])
    export = "/api/v1/export/profiles"
    assert api.post(export, json={"ids": profiles[:1]}).raise_for_status().json()["version"] == 1
    assert api.post(export, json={"ids": profiles[1:]}).raise_for_status().json()["version"] == 2
    mixed = api.get(export).raise_for_status().json()
    assert mixed["version"] == 2
    assert api.post("/api/v1/import/profiles", json=mixed).raise_for_status().json()["data"]["created"] == 2
    listed = api.get("/api/v1/profiles").raise_for_status().json()["data"]["items"]
    assert {p["media_capture"] for p in listed if p["name"].startswith("blocked")} == {"blocked"}


def test_required_geolocation_restriction_uses_version_three(api):
    rows = [
        {"name": "media", "engine": "chromium", "media_capture": "blocked"},
        {"name": "location", "engine": "chromium", "geolocation": "blocked"},
    ]
    ids = [api.post("/api/v1/profiles", json=row).raise_for_status().json()["data"]["id"] for row in rows]
    export = "/api/v1/export/profiles"
    assert api.post(export, json={"ids": ids[:1]}).raise_for_status().json()["version"] == 2
    assert api.post(export, json={"ids": ids[1:]}).raise_for_status().json()["version"] == 3
    mixed = api.get(export).raise_for_status().json()
    assert mixed["version"] == 3
    assert api.post("/api/v1/import/profiles", json=mixed).raise_for_status().json()["data"]["created"] == 2
    listed = api.get("/api/v1/profiles").raise_for_status().json()["data"]["items"]
    assert {p["name"]: p["geolocation"] for p in listed} == {
        "location": "blocked", "location (2)": "blocked", "media": "standard", "media (2)": "standard",
    }


@pytest.mark.parametrize("policy", ["canvas_readback", "webgl_debug_info"])
def test_graphics_policy_needs_version_nine_and_roundtrips(api, policy):
    rows = [
        {"name": "restricted", "engine": "chromium", policy: "blocked"},
        {"name": "legacy", "engine": "chromium"},
        {"name": "unsupported", "engine": "camoufox", policy: "blocked"},
    ]
    result = import_rows(api, rows)
    assert result["created"] == 2 and [r["row"] for r in result["skipped"]] == [3]
    exported = api.get("/api/v1/export/profiles").raise_for_status().json()
    assert exported["version"] == 9
    assert {p["name"]: p[policy] for p in exported["profiles"]} == {"restricted": "blocked", "legacy": "standard"}
    assert api.post("/api/v1/import/profiles", json=exported).raise_for_status().json()["data"]["created"] == 2
    imported = api.get("/api/v1/profiles").raise_for_status().json()["data"]["items"]
    assert {p["name"]: p[policy] for p in imported if p["name"].endswith("(2)")} == {
        "restricted (2)": "blocked", "legacy (2)": "standard",
    }


def test_hardware_limit_roundtrips_and_legacy_exports_remain_compatible(api):
    old = {"version": 9, "profiles": [{"name": "old", "engine": "chromium"}]}
    assert api.post("/api/v1/import/profiles", json=old).raise_for_status().json()["data"]["created"] == 1
    exported = api.get("/api/v1/export/profiles").raise_for_status().json()
    assert exported["version"] == 1 and exported["profiles"][0]["hardware_reporting"] == "standard"
    item = {"name": "limited", "engine": "chromium", "hardware_reporting": "limited"}
    result = import_rows(api, [item, {**item, "engine": "camoufox"}, {**item, "hardware_reporting": 4}])
    assert result["created"] == 1 and [r["row"] for r in result["skipped"]] == [2, 3]
    exported = api.get("/api/v1/export/profiles").raise_for_status().json()
    assert exported["version"] == 10
    assert api.post("/api/v1/import/profiles", json=exported).raise_for_status().json()["data"]["created"] == 2
    copied = api.get("/api/v1/profiles").raise_for_status().json()["data"]["items"]
    assert {p["name"]: p["hardware_reporting"] for p in copied if p["name"].endswith("(2)")} == {
        "limited (2)": "limited", "old (2)": "standard",
    }


def test_unsupported_or_invalid_file_version_rejects_whole_import(api):
    rows = [{"name": "untouched", "group": "team", "proxy": {"host": "proxy.invalid", "port": 80}}]
    unsupported = main.PROFILE_FORMAT_VERSION + 1
    for payload in (
        {"version": unsupported, "profiles": rows}, {"version": unsupported, "data": {"version": 1, "profiles": rows}},
        {"data": {"version": unsupported, "profiles": rows}}, {"version": 0, "profiles": rows},
    ):
        response = api.post("/api/v1/import/profiles", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "UNSUPPORTED_PROFILE_FORMAT_VERSION"
    for invalid in (True, None, "1", 1.5):
        response = api.post("/api/v1/import/profiles", json={"version": invalid, "profiles": rows})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_PROFILE_FORMAT_VERSION"
    with db.session() as conn:
        for table in ("profiles", "groups", "proxies"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM logs WHERE action='profiles.import'").fetchone()[0] == 0
    assert list(config.PROFILES_DIR.iterdir()) == []


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
