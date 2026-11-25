"""Dashboard engine integration with real runner processes and a fake Chromium binary."""

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROFILES = "/api/v1/profiles"
RESTRICT_FONTS = "--moltskin-disable-local-font-access"


@pytest.fixture
def client(data_dir, monkeypatch):
    from moltskin.services.core import main
    from moltskin.services.core.security import get_token

    binary = data_dir / "test-chromium"
    binary.write_text(
        """#!/usr/bin/env python3
import json, os, signal, socket, sys
from pathlib import Path

if sys.argv[1:] == ["--version", "--moltskin-capabilities"]:
    print("Chromium " + os.environ.get("TEST_CHROMIUM_VERSION", "158.0.0.1"))
    print("moltskin-disable-local-font-access")
    sys.exit(0)
directory = Path(next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--user-data-dir=")))
lock = directory / "SingletonLock"
def close_browser(signum, _frame):
    (directory / "test-exit-signal").write_text(signal.Signals(signum).name)
    sys.exit(0)
signal.signal(signal.SIGTERM, close_browser)
signal.signal(signal.SIGINT, close_browser)
(directory / "test-args.json").write_text(json.dumps(sys.argv[1:]))
lock.symlink_to(f"{socket.gethostname()}-{os.getpid()}")
try:
    signal.alarm(45)
    signal.pause()
finally:
    lock.unlink(missing_ok=True)
""",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    monkeypatch.setenv("MOLTSKIN_CHROMIUM_BINARY", str(binary))
    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as client:
        yield client


def create_chromium(client, name, **options):
    response = client.post(PROFILES, json={"name": name, "engine": "chromium", **options})
    assert response.status_code == 201, response.text
    profile = response.json()["data"]
    assert profile["engine"] == "chromium"
    assert profile["browser_version"] is None
    assert profile["fingerprint_preview"] is None
    return profile


def test_migrate_existing_camoufox_profile(data_dir):
    from moltskin.services.core import db

    db.init_db()
    with db.session() as conn:
        conn.execute("ALTER TABLE profiles DROP COLUMN local_fonts")
        conn.execute("ALTER TABLE profiles DROP COLUMN engine")
        conn.execute("ALTER TABLE profiles DROP COLUMN startup_page")
        conn.execute("ALTER TABLE profiles DROP COLUMN block_images")
        conn.execute("ALTER TABLE sessions DROP COLUMN browser_version")
        conn.execute(
            "INSERT INTO profiles(id,name,user_data_dir,open_count,created_at,updated_at) "
            "VALUES ('old-id','legacy',?,12,?,?)", (str(data_dir / "old-profile"), db.now(), db.now()),
        )
    db.init_db()
    db.init_db()
    with db.session() as conn:
        row = conn.execute("SELECT * FROM profiles WHERE id='old-id'").fetchone()
    assert row["engine"] == "camoufox"
    assert row["user_data_dir"] == str(data_dir / "old-profile")
    assert row["open_count"] == 12
    assert row["browser_version"] is None
    with db.session() as conn:
        assert "browser_version" in {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert (row["startup_page"], row["block_images"]) == ("default", 0)


def test_engine_contract_and_portable_settings(client):
    original = create_chromium(client, "test", local_fonts="restricted")
    url = f"{PROFILES}/{original['id']}"
    immutable = client.patch(url, json={"engine": "camoufox"})
    assert immutable.status_code == 409 and immutable.json()["error"]["code"] == "ENGINE_IMMUTABLE"
    assert client.patch(url, json={"local_fonts": "typo"}).is_client_error
    assert client.patch(url, json={"fingerprint": {"locale": "fr-FR"}}).is_client_error
    assert (original["startup_page"], original["block_images"]) == ("default", False)
    assert client.patch(url, json={"startup_page": "https://external.invalid"}).status_code == 422
    assert client.patch(url, json={"startup_page": "dashboard", "block_images": True}).is_success
    assert client.patch(url, json={"remark": "edited"}).is_success
    duplicate = client.post(url + "/duplicate").json()["data"]
    assert (duplicate["startup_page"], duplicate["block_images"]) == ("dashboard", True)
    assert (duplicate["engine"], duplicate["local_fonts"]) == ("chromium", "restricted")
    assert duplicate["user_data_dir"] != original["user_data_dir"]
    exported = client.get("/api/v1/export/profiles").json()
    item = next(p for p in exported["profiles"] if p["name"] == "test")
    item["name"] = "restored"
    imported = client.post("/api/v1/import/profiles", json={"profiles": [item]})
    assert imported.is_success, imported.text
    restored = next(p for p in client.get(PROFILES).json()["data"]["items"] if p["name"] == "restored")
    assert (restored["engine"], restored["local_fonts"]) == ("chromium", "restricted")
    assert restored["user_data_dir"] != original["user_data_dir"]
    assert (restored["startup_page"], restored["block_images"]) == ("dashboard", True)


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="Chromium preview targets macOS and Linux")
def test_missing_binary_never_leaves_a_running_session(client, data_dir, monkeypatch):
    engines = client.get("/api/v1/engine").json()["data"]
    assert {"camoufox", "playwright", "browsers"} <= engines.keys()
    assert engines["chromium"]["available"] is True
    assert engines["chromium"]["version"] == "158.0.0.1"
    profile = create_chromium(client, "missing-build")
    monkeypatch.setenv("MOLTSKIN_CHROMIUM_BINARY", str(data_dir / "missing"))
    missing = client.get("/api/v1/engine").json()["data"]["chromium"]
    assert missing["available"] is False
    assert missing["version"] is None and missing["message"]
    url = f"{PROFILES}/{profile['id']}"
    failure = client.post(url + "/launch")
    assert failure.status_code == 503, failure.text
    assert failure.json()["error"]["code"] == "CHROMIUM_UNAVAILABLE"
    assert client.get(url).json()["data"]["browser_version"] is None
    status = client.get(url + "/status").json()["data"]
    assert status["status"] != "running"
    assert status["session_id"] is None


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="Chromium preview targets macOS and Linux")
def test_two_native_sessions_and_restart(client, monkeypatch):
    from moltskin.services.core import config

    monkeypatch.setattr(config, "OPEN_MANAGER", True)
    monkeypatch.setattr(config, "PORT", 18765)

    a = create_chromium(client, "a", local_fonts="restricted")
    b = create_chromium(client, "b")
    assert a["user_data_dir"] != b["user_data_dir"]
    urls = [f"{PROFILES}/{p['id']}" for p in (a, b)]
    first_sessions = []
    try:
        for url in urls:
            result = client.post(url + "/launch")
            assert result.is_success, result.text
            first_sessions.append(result.json()["data"]["session_id"])
            assert client.get(url).json()["data"]["browser_version"] == "158.0.0.1"
            assert client.get(url + "/status").json()["data"]["state"] == "running"
        assert client.post(urls[0] + "/launch").status_code == 409
        assert client.patch(urls[0], json={"local_fonts": "standard"}).status_code == 409
        for profile, restricted in ((a, True), (b, False)):
            args = json.loads((Path(profile["user_data_dir"]) / "test-args.json").read_text())
            assert f"--user-data-dir={profile['user_data_dir']}" in args
            assert (RESTRICT_FONTS in args) == restricted
            assert "--use-mock-keychain" not in args
            assert "--moltskin-panel-port=18765" in args
            assert args[-1] == "about:blank"
            assert config.MANAGER_URL not in args
        marker = Path(a["user_data_dir"]) / "test-marker"
        marker.write_text("profile A")
        assert not (Path(b["user_data_dir"]) / marker.name).exists()
        assert client.post(urls[0] + "/stop").is_success
        deadline = time.monotonic() + 8
        while client.get(urls[0] + "/status").json()["data"]["session_id"] is not None:
            assert time.monotonic() < deadline, "stopped session still active"
            time.sleep(0.1)
        assert (Path(a["user_data_dir"]) / "test-exit-signal").read_text() == "SIGINT"
        assert client.get(urls[1] + "/status").json()["data"]["state"] == "running"
        with monkeypatch.context() as unavailable:
            unavailable.setenv("MOLTSKIN_CHROMIUM_BINARY", str(Path(a["user_data_dir"]) / "missing-browser"))
            assert client.post(urls[0] + "/launch").status_code == 503
            assert client.get(urls[0]).json()["data"]["browser_version"] == "158.0.0.1"
        assert client.patch(urls[0], json={"local_fonts": "standard", "startup_page": "dashboard", "block_images": True}).is_success
        monkeypatch.setenv("TEST_CHROMIUM_VERSION", "158.0.0.2")
        assert client.post(urls[0] + "/launch").is_success
        assert client.get(urls[0]).json()["data"]["browser_version"] == "158.0.0.2"
        assert client.get(urls[1]).json()["data"]["browser_version"] == "158.0.0.1"
        for session_id in first_sessions:
            assert client.get(f"/api/v1/sessions/{session_id}").json()["data"]["browser_version"] == "158.0.0.1"
        args = json.loads((Path(a["user_data_dir"]) / "test-args.json").read_text())
        assert args[-1] == "http://127.0.0.1:18765/#/home"
        assert "--blink-settings=imagesEnabled=false" in args
        assert RESTRICT_FONTS not in args
        assert marker.read_text() == "profile A"
    finally:
        for url in urls:
            client.post(url + "/stop")


@pytest.mark.parametrize(("startup", "host_allows", "global_manager", "target"), [
    ("default", True, True, "http://configured.invalid"),
    ("default", True, False, None),
    ("blank", True, True, "about:blank"),
    ("dashboard", True, False, "http://127.0.0.1:18765/#/home"),
    ("dashboard", False, True, "about:blank"),
])
def test_camoufox_startup_tab(client, monkeypatch, data_dir, startup, host_allows, global_manager, target):
    from unittest.mock import MagicMock

    from moltskin.services.core import config, db, launcher, runner

    monkeypatch.setattr(config, "PORT", 18765)
    monkeypatch.setattr(config, "MANAGER_URL", "http://configured.invalid")
    monkeypatch.setattr(config, "OPEN_MANAGER", host_allows)
    db.set_setting("open_manager_on_launch", global_manager)
    assert client.get("/api/v1/engine").json()["data"]["dashboard_startup_available"] is host_allows
    saved = client.post(PROFILES, json={"name": "portable", "startup_page": startup, "block_images": True})
    assert saved.status_code == 201, saved.text
    with db.session() as conn:
        profile = conn.execute("SELECT * FROM profiles WHERE id=?", (saved.json()["data"]["id"],)).fetchone()
    cmd = launcher._camoufox_command(profile, None, "ss_test", True, data_dir / "status.json")
    context = MagicMock()
    page = context.pages[0]
    browser = MagicMock()
    browser.return_value.__enter__.return_value = context
    monkeypatch.setattr(runner, "Camoufox", browser)
    monkeypatch.setattr(runner.signal, "signal", lambda *_: None)
    monkeypatch.setattr(runner.observation, "capture", lambda *_: None)
    monkeypatch.setattr(sys, "argv", ["runner", *cmd[-2:]])
    assert runner.main() == 0
    assert browser.call_args.kwargs["block_images"] is True
    if target:
        page.goto.assert_called_once_with(target, wait_until="domcontentloaded", timeout=15000)
    else:
        page.goto.assert_not_called()
