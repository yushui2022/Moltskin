"""Profile recycle bin through the local API, with an isolated database and files."""

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from moltskin.services.core import db, launcher, main, scheduler
from moltskin.services.core.security import get_token


PROFILES = "/api/v1/profiles"
TRASH = "/api/v1/trash/profiles"


@pytest.fixture
def api(data_dir):
    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as client:
        yield client


def create(api, name, **options):
    fields = {"name": name, "engine": "chromium", **options}
    return api.post(PROFILES, json=fields).raise_for_status().json()["data"]


def test_archived_profile_is_hidden_until_restored_with_data_history_and_bindings(api):
    group = api.post("/api/v1/groups", json={"name": "team"}).raise_for_status().json()["data"]["id"]
    proxy = api.post("/api/v1/proxies", json={
        "name": "proxy", "host": "proxy.invalid", "port": 80,
    }).raise_for_status().json()["data"]["id"]
    profile = create(api, "work", engine="camoufox", group_id=group, proxy_id=proxy, tags=["team"],
                     remark="note", fingerprint={"preset": {"navigator": {"platform": "Linux"}}})
    pid = profile["id"]
    marker = Path(profile["user_data_dir"]) / "persistent-state"
    marker.write_bytes(b"saved before archive")
    with db.session() as conn:
        conn.execute("INSERT INTO extensions (id, name, created_at) VALUES ('ext', 'example', ?)", (db.now(),))
        conn.execute("INSERT INTO profile_extensions VALUES (?, 'ext')", (pid,))
        conn.execute(
            "INSERT INTO sessions (id, profile_id, state, started_at, ended_at, browser_version) "
            "VALUES ('old-session', ?, 'stopped', ?, ?, 'v1')", (pid, db.now(), db.now()),
        )
        conn.execute(
            "UPDATE profiles SET open_count=1, last_session_id='old-session', browser_version='v1' WHERE id=?",
            (pid,),
        )
    url = f"{PROFILES}/{pid}"
    before = api.get(url).raise_for_status().json()["data"]
    history = api.get("/api/v1/sessions/old-session").raise_for_status().json()["data"]
    schedules = [{"target_type": "ids", "target_ids": [pid]}, {"target_type": "group", "target_ids": [group]},
                 {"target_type": "all"}]

    api.post(f"{url}/trash").raise_for_status()
    entry, = api.get(TRASH).raise_for_status().json()["data"]
    assert (entry["id"], entry["name"], entry["engine"]) == (pid, "work", "camoufox")
    assert entry["trashed_at"] and not entry["purge_started"]
    assert api.get(PROFILES).json()["data"]["items"] == []
    assert api.get(url).status_code == 404
    assert api.post(f"{url}/duplicate").status_code == 404
    assert api.get("/api/v1/export/profiles").json()["profiles"] == []
    assert api.get("/api/v1/groups").json()["data"][0]["profile_count"] == 0
    assert api.get("/api/v1/proxies").json()["data"][0]["used_by"] == 0
    stats = api.get("/api/v1/stats").raise_for_status().json()["data"]
    assert stats["profiles_total"] == 0 and stats["today_opened"] == 1
    assert all(scheduler.resolve_profiles(schedule) == [] for schedule in schedules)

    restored = api.post(f"{TRASH}/{pid}/restore").raise_for_status().json()["data"]
    for key in before.keys() - {"updated_at"}:
        assert restored[key] == before[key], key
    assert marker.read_bytes() == b"saved before archive"
    assert api.get("/api/v1/sessions/old-session").json()["data"] == history
    assert api.get(f"{url}/extensions").json()["data"]["ids"] == ["ext"]
    assert all([p["id"] for p in scheduler.resolve_profiles(schedule)] == [pid] for schedule in schedules)
    assert api.get(TRASH).json()["data"] == []
    assert api.post(f"{TRASH}/{pid}/restore").status_code == 404
    assert api.get("/api/v1/stats").json()["data"]["today_opened"] == 1


def test_archiving_preserves_daily_launch_limits_and_restoring_respects_profile_limit(api, monkeypatch):
    pid = create(api, "old")["id"]
    with db.session() as conn:
        conn.execute("INSERT INTO sessions(id, profile_id, state, started_at, ended_at) "
                     "VALUES ('session', ?, 'stopped', ?, ?)", (pid, db.now(), db.now()))
    db.set_setting("daily_open_limit", 1)
    db.set_setting("profile_limit", 1)
    api.post(f"{PROFILES}/{pid}/trash").raise_for_status()
    replacement = create(api, "new")["id"]
    monkeypatch.setattr(launcher.chromium, "prepare_launch", lambda *a, **kw: (["unused"], "1"))
    denied_launch = api.post(f"{PROFILES}/{replacement}/launch")
    assert denied_launch.status_code == 429
    assert denied_launch.json()["error"]["code"] == "DAILY_LIMIT"
    denied_restore = api.post(f"{TRASH}/{pid}/restore")
    assert denied_restore.status_code == 409
    assert denied_restore.json()["error"]["code"] == "PROFILE_LIMIT"
    assert [row["id"] for row in api.get(TRASH).json()["data"]] == [pid]
    api.post(f"{PROFILES}/{replacement}/trash").raise_for_status()
    api.post(f"{TRASH}/{pid}/restore").raise_for_status()


def test_restore_after_name_reuse_and_removal_of_linked_resources(api):
    group = api.post("/api/v1/groups", json={"name": "old"}).json()["data"]["id"]
    proxy = api.post("/api/v1/proxies", json={"name": "old", "host": "example.invalid", "port": 80}).json()["data"]["id"]
    original = create(api, "shared", engine="camoufox", group_id=group, proxy_id=proxy,
                      fingerprint={"preset": {"navigator": {"platform": "Linux"}}})
    pid = original["id"]
    with db.session() as conn:
        conn.execute("INSERT INTO extensions (id, name, created_at) VALUES ('old', 'old', ?)", (db.now(),))
        conn.execute("INSERT INTO profile_extensions VALUES (?, 'old')", (pid,))
    api.post(f"{PROFILES}/{pid}/trash").raise_for_status()
    api.delete(f"/api/v1/groups/{group}").raise_for_status()
    api.delete(f"/api/v1/proxies/{proxy}").raise_for_status()
    api.delete("/api/v1/extensions/old").raise_for_status()
    replacement = create(api, "shared")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(api.post, f"{TRASH}/{pid}/restore") for _ in range(2)]
        responses = [f.result() for f in futures]
    assert sorted(r.status_code for r in responses) == [200, 404]
    restored = next(r for r in responses if r.is_success).json()["data"]
    assert (restored["id"], restored["name"]) == (pid, "shared (2)")
    assert restored["group"] is None and restored["proxy"] is None
    assert api.get(f"{PROFILES}/{pid}/extensions").json()["data"]["ids"] == []
    assert api.get(f"{PROFILES}/{replacement['id']}").json()["data"]["name"] == "shared"
    assert replacement["user_data_dir"] != restored["user_data_dir"]


@pytest.mark.parametrize("state", ["starting", "running", "stopping"])
def test_batch_skips_active_session_without_stranding_other_profiles(api, state):
    busy = create(api, "busy")["id"]
    idle = create(api, "idle")["id"]
    with db.session() as conn:
        conn.execute("INSERT INTO sessions (id, profile_id, state, started_at) VALUES ('active', ?, ?, ?)",
                     (busy, state, db.now()))
    assert api.post(f"{PROFILES}/{busy}/trash").status_code == 409
    batch = api.post(f"{PROFILES}/batch", json={"action": "trash", "ids": [busy, idle]}).raise_for_status().json()["data"]
    assert [batch["ok"], batch["failed"]] == [1, 1]
    assert [row["id"] for row in api.get(TRASH).json()["data"]] == [idle]
    assert api.get(f"{PROFILES}/{busy}").is_success


def test_selftest_reserves_only_its_profile_until_completion(api, monkeypatch):
    pid = create(api, "selftest", engine="camoufox", fingerprint={"preset": {"navigator": {"platform": "Linux"}}})["id"]
    other = create(api, "other")["id"]
    started, release = Event(), Event()

    def wait_for_release(profile, proxy):
        started.set()
        assert release.wait(timeout=10)
        return {"image": "local-test.png"}

    monkeypatch.setattr(main.selftest, "run", wait_for_release)
    with ThreadPoolExecutor(max_workers=1) as pool:
        test_run = pool.submit(api.post, f"{PROFILES}/{pid}/self-test")
        try:
            assert started.wait(timeout=5)
            denied = api.post(f"{PROFILES}/{pid}/trash")
            assert denied.status_code == 409 and denied.json()["error"]["code"] == "PROFILE_BUSY"
            api.post(f"{PROFILES}/{other}/trash").raise_for_status()
        finally:
            release.set()
        test_run.result(timeout=5).raise_for_status()
    api.post(f"{PROFILES}/{pid}/trash").raise_for_status()


def test_purge_requires_archived_profile_and_retry_after_filesystem_failure(api, monkeypatch):
    profile = create(api, "retry")
    pid = profile["id"]
    directory = Path(profile["user_data_dir"])
    (directory / "marker").touch()
    (directory / "second-file").touch()
    url = f"{TRASH}/{pid}"
    assert api.delete(url).status_code == 404
    assert (directory / "marker").is_file()
    api.post(f"{PROFILES}/{pid}/trash").raise_for_status()

    def interrupt(path, *args, **kwargs):
        (Path(path) / "marker").unlink()
        raise PermissionError("test directory is busy")

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "rmtree", interrupt)
        assert not api.delete(url).is_success
    entry, = api.get(TRASH).json()["data"]
    assert entry["id"] == pid and entry["purge_started"]
    assert not (directory / "marker").exists() and (directory / "second-file").is_file()
    assert api.post(f"{url}/restore").status_code == 409
    api.delete(url).raise_for_status()
    assert not directory.exists()
    assert api.get(TRASH).json()["data"] == []
    assert api.post(f"{url}/restore").status_code == 404


def test_symlink_cannot_redirect_purge_or_restore(api, data_dir):
    profile = create(api, "symlink")
    directory = Path(profile["user_data_dir"])
    pid = profile["id"]
    api.post(f"{PROFILES}/{pid}/trash").raise_for_status()
    outside = data_dir / "unrelated"
    outside.mkdir()
    marker = outside / "keep"
    marker.touch()
    directory.rmdir()
    directory.symlink_to(outside, target_is_directory=True)
    url = f"{TRASH}/{pid}"
    assert not api.delete(url).is_success
    assert not api.post(f"{url}/restore").is_success
    assert marker.is_file()
    assert [row["id"] for row in api.get(TRASH).json()["data"]] == [pid]
    directory.unlink()
    directory.mkdir()
    api.post(f"{url}/restore").raise_for_status()


def test_recycle_bin_requires_token(api):
    profile = create(api, "private")
    pid = profile["id"]
    denied = {"Authorization": "Bearer unrelated"}
    assert api.post(f"{PROFILES}/{pid}/trash", headers=denied).status_code == 401
    api.post(f"{PROFILES}/{pid}/trash").raise_for_status()
    assert api.get(TRASH, headers=denied).status_code == 401
    assert api.post(f"{TRASH}/{pid}/restore", headers=denied).status_code == 401
    assert api.delete(f"{TRASH}/{pid}", headers=denied).status_code == 401
    assert [row["id"] for row in api.get(TRASH).json()["data"]] == [pid]
