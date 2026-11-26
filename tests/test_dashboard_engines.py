"""Dashboard engine integration with real runner processes and a fake Chromium binary."""

import json
import re
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROFILES = "/api/v1/profiles"
RESTRICT_FONTS = "--moltskin-disable-local-font-access"
BLOCK_MEDIA = "--moltskin-block-media-capture"
BLOCK_LOCATION = "--moltskin-block-geolocation"
BLOCK_WEBGL = "--moltskin-block-webgl-debug-info"
BLOCK_CANVAS = "--moltskin-block-canvas-readback"
LIMIT_HARDWARE = "--moltskin-limit-hardware-reporting"


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
    if not os.environ.get("TEST_LEGACY_CHROMIUM"):
        print("moltskin-block-media-capture")
        if not os.environ.get("TEST_MEDIA_ONLY_CHROMIUM"):
            print("moltskin-block-geolocation")
            print("moltskin-limit-hardware-reporting")
            for capability in ("moltskin-block-webgl-debug-info", "moltskin-block-canvas-readback"):
                if capability != os.environ.get("TEST_MISSING_GRAPHICS_CAPABILITY"):
                    print(capability)
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


@pytest.mark.parametrize("protocol", ["http", "https", "socks5"])
def test_chromium_fixed_proxy_persists_and_requires_explicit_unbinding(client, protocol):
    proxies = "/api/v1/proxies"
    proxy = client.post(proxies, json={
        "name": "Company", "protocol": protocol, "host": "::1", "port": 3128,
    }).raise_for_status().json()["data"]["id"]
    endpoint = f"{proxies}/{proxy}"
    profile = create_chromium(client, "Network test", proxy_id=proxy)
    url = f"{PROFILES}/{profile['id']}"
    assert client.get(proxies).json()["data"][0]["chromium_compatible"]
    assert client.delete(endpoint).status_code == 409
    assert client.patch(endpoint, json={"username": "user"}).status_code == 422
    client.post(url + "/launch").raise_for_status()
    try:
        args = json.loads((Path(profile["user_data_dir"]) / "test-args.json").read_text())
        assert [arg for arg in args if arg.startswith("--proxy-server=")] == [f"--proxy-server={protocol}://[::1]:3128"]
        assert client.patch(url, json={"proxy_id": None}).status_code == 409
        assert client.patch(endpoint, json={"port": 4321}).status_code == 409
    finally:
        client.post(url + "/stop").raise_for_status()
    client.post(url + "/trash").raise_for_status()
    assert client.delete(endpoint).status_code == 409
    restored = client.post(f"/api/v1/trash/profiles/{profile['id']}/restore").raise_for_status().json()["data"]
    assert restored["proxy"]["id"] == proxy
    assert client.patch(url, json={"proxy_id": None}).raise_for_status().json()["data"]["proxy"] is None
    client.delete(endpoint).raise_for_status()


def test_chromium_refuses_incompatible_proxy_before_saving_or_launching(client):
    from moltskin.services.core import db

    proxies = "/api/v1/proxies"
    profile = create_chromium(client, "Keep network setting")
    url = f"{PROFILES}/{profile['id']}"
    assert client.patch(url, json={"proxy_id": "px_missing"}).status_code == 422
    for opts in ({"username": "user"}, {"password": "test-only"}, {"host": "example.invalid,direct://"}):
        proxy = client.post(proxies, json={
            "name": "Legacy proxy", "host": "example.invalid", "port": 443, **opts,
        }).raise_for_status().json()["data"]["id"]
        assert client.post(PROFILES, json={"name": "Reject", "engine": "chromium", "proxy_id": proxy}).status_code == 422
        assert client.patch(url, json={"proxy_id": proxy}).status_code == 422
    assert all(not row["chromium_compatible"] for row in client.get(proxies).json()["data"])
    with db.session() as conn:
        conn.execute("UPDATE profiles SET proxy_id=? WHERE id=?", (proxy, profile["id"]))
    failure = client.post(url + "/launch")
    assert failure.status_code == 422 and failure.json()["error"]["code"] == "INVALID_CHROMIUM_PROXY"


def test_directory_mismatch_refuses_launch_and_single_or_batch_data_deletion(client, monkeypatch):
    from moltskin.services.core import chromium, db

    first = create_chromium(client, "first")
    other = create_chromium(client, "other")
    disposable = create_chromium(client, "disposable")
    for profile in (first, other):
        (Path(profile["user_data_dir"]) / "marker").write_text(profile["id"])
    with db.session() as conn:
        conn.execute("UPDATE profiles SET user_data_dir=? WHERE id=?", (other["user_data_dir"], first["id"]))

    monkeypatch.setattr(chromium, "prepare_launch", lambda *a, **kw: pytest.fail("unexpected launch preparation"))
    url = f"{PROFILES}/{first['id']}"
    for response in (client.post(url + "/launch"), client.delete(url, params={"delete_data": True})):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_PROFILE_DIRECTORY"
    result = client.post(PROFILES + "/batch", json={
        "action": "delete", "delete_data": True, "ids": [first["id"], disposable["id"]],
    }).raise_for_status().json()["data"]
    assert result["results"][0]["error"]["code"] == "INVALID_PROFILE_DIRECTORY"
    assert (result["ok"], result["failed"]) == (1, 1)
    assert not Path(disposable["user_data_dir"]).exists()
    for profile in (first, other):
        assert client.get(f"{PROFILES}/{profile['id']}").is_success
        assert (Path(profile["user_data_dir"]) / "marker").read_text() == profile["id"]
    assert client.delete(url).is_success  # Metadata-only removal never touches either directory.
    assert Path(first["user_data_dir"]).exists() and Path(other["user_data_dir"]).exists()


def test_launch_reservation_rechecks_changed_directory(client, monkeypatch):
    from moltskin.services.core import chromium, db

    first = create_chromium(client, "reserving")
    other = create_chromium(client, "other")
    prepare = chromium.prepare_launch

    def change_record(*args, **kwargs):
        command, version = prepare(*args, **kwargs)
        with db.session() as conn:
            conn.execute("UPDATE profiles SET user_data_dir=? WHERE id=?", (other["user_data_dir"], first["id"]))
        return command, version

    monkeypatch.setattr(chromium, "prepare_launch", change_record)
    response = client.post(f"{PROFILES}/{first['id']}/launch")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_PROFILE_DIRECTORY"
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert not any((Path(p["user_data_dir"]) / "test-args.json").exists() for p in (first, other))


def test_chromium_accepts_linked_parent_directory(client, data_dir, monkeypatch):
    from moltskin.services.core import config

    alias = data_dir / "profiles-alias"
    alias.symlink_to(config.PROFILES_DIR, target_is_directory=True)
    monkeypatch.setattr(config, "PROFILES_DIR", alias)
    profile = create_chromium(client, "linked storage")
    path = Path(profile["user_data_dir"])
    assert path.parent == alias
    url = f"{PROFILES}/{profile['id']}"
    try:
        client.post(url + "/launch").raise_for_status()
        args = json.loads((path / "test-args.json").read_text())
        assert f"--user-data-dir={path.resolve()}" in args
    finally:
        client.post(url + "/stop").raise_for_status()
    client.delete(url, params={"delete_data": True}).raise_for_status()
    assert not path.exists() and alias.is_symlink()


def test_owned_directory_in_use_refuses_data_deletion(client, monkeypatch):
    from moltskin.services.core import recycle

    p = create_chromium(client, "directory still open")
    path = Path(p["user_data_dir"])
    monkeypatch.setattr(recycle, "chromium_owns_directory", lambda directory: directory == path.resolve())
    url = f"{PROFILES}/{p['id']}"
    result = client.delete(url, params={"delete_data": True})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "PROFILE_RUNNING"
    assert path.is_dir() and client.get(url).is_success


def test_legacy_camoufox_custom_directory_remains_supported(client, data_dir):
    from moltskin.services.core import db

    profile = client.post(PROFILES, json={"name": "old-profile", "engine": "camoufox"}).raise_for_status().json()["data"]
    custom = data_dir / "legacy-storage" / "not-the-profile-id"
    custom.parent.mkdir()
    Path(profile["user_data_dir"]).rename(custom)
    with db.session() as conn:
        conn.execute("UPDATE profiles SET user_data_dir=? WHERE id=?", (str(custom), profile["id"]))
    client.delete(f"{PROFILES}/{profile['id']}", params={"delete_data": True}).raise_for_status()
    assert not custom.exists()


def test_migrate_existing_camoufox_profile(data_dir):
    from moltskin.services.core import db

    db.init_db()
    with db.session() as conn:
        conn.execute("ALTER TABLE profiles DROP COLUMN local_fonts")
        conn.execute("ALTER TABLE profiles DROP COLUMN media_capture")
        conn.execute("ALTER TABLE profiles DROP COLUMN geolocation")
        conn.execute("ALTER TABLE profiles DROP COLUMN hardware_reporting")
        conn.execute("ALTER TABLE profiles DROP COLUMN canvas_readback")
        conn.execute("ALTER TABLE profiles DROP COLUMN webgl_debug_info")
        conn.execute("ALTER TABLE profiles DROP COLUMN chromium_js_heap_mb")
        conn.execute("ALTER TABLE profiles DROP COLUMN chromium_mute_audio")
        conn.execute("ALTER TABLE profiles DROP COLUMN chromium_renderer_limit")
        conn.execute("ALTER TABLE profiles DROP COLUMN browser_language")
        conn.execute("ALTER TABLE profiles DROP COLUMN browser_timezone")
        conn.execute("ALTER TABLE profiles DROP COLUMN browser_window_size")
        conn.execute("ALTER TABLE profiles DROP COLUMN chromium_version")
        conn.execute("ALTER TABLE profiles DROP COLUMN chromium_user_agent")
        conn.execute("ALTER TABLE profiles DROP COLUMN preview_platform")
        conn.execute("ALTER TABLE profiles DROP COLUMN preview_platform_version")
        conn.execute("ALTER TABLE profiles DROP COLUMN preview_architecture")
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
    assert row["chromium_version"] == ""
    assert row["chromium_user_agent"] == ""
    assert row["preview_platform"] == row["preview_platform_version"] == row["preview_architecture"] == ""
    assert row["media_capture"] == row["geolocation"] == "standard"
    assert row["hardware_reporting"] == "standard"
    assert row["canvas_readback"] == row["webgl_debug_info"] == "standard"
    assert row["browser_language"] == row["browser_timezone"] == row["browser_window_size"] == ""
    assert row["user_data_dir"] == str(data_dir / "old-profile")
    assert (row["chromium_mute_audio"], row["chromium_renderer_limit"], row["chromium_js_heap_mb"]) == (0, 0, 0)
    assert row["open_count"] == 12
    assert row["browser_version"] is None
    with db.session() as conn:
        assert "browser_version" in {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert (row["startup_page"], row["block_images"]) == ("default", 0)


def test_engine_contract_and_portable_settings(client):
    original = create_chromium(client, "test", local_fonts="restricted")
    assert original["media_capture"] == original["geolocation"] == "standard"
    url = f"{PROFILES}/{original['id']}"
    immutable = client.patch(url, json={"engine": "camoufox"})
    assert immutable.status_code == 409 and immutable.json()["error"]["code"] == "ENGINE_IMMUTABLE"
    assert client.patch(url, json={"local_fonts": "typo"}).is_client_error
    assert client.patch(url, json={"media_capture": "typo"}).status_code == 422
    assert client.patch(url, json={"media_capture": None}).status_code == 422
    assert client.post(PROFILES, json={"name": "unsupported", "engine": "camoufox", "media_capture": "blocked"}).status_code == 422
    assert client.patch(url, json={"geolocation": "typo"}).status_code == 422
    assert client.patch(url, json={"geolocation": None}).status_code == 422
    assert client.post(PROFILES, json={"name": "unsupported", "engine": "camoufox", "geolocation": "blocked"}).status_code == 422
    assert client.patch(url, json={"fingerprint": {"locale": "fr-FR"}}).is_client_error
    assert (original["startup_page"], original["block_images"]) == ("default", False)
    assert client.patch(url, json={"startup_page": "https://external.invalid"}).status_code == 422
    assert client.patch(url, json={"startup_page": "dashboard", "block_images": True, "media_capture": "blocked"}).is_success
    assert client.patch(url, json={"geolocation": "blocked"}).is_success
    assert client.patch(url, json={"remark": "edited"}).is_success
    duplicate = client.post(url + "/duplicate").json()["data"]
    assert (duplicate["startup_page"], duplicate["block_images"]) == ("dashboard", True)
    assert (duplicate["engine"], duplicate["local_fonts"]) == ("chromium", "restricted")
    assert duplicate["media_capture"] == duplicate["geolocation"] == "blocked"
    assert duplicate["user_data_dir"] != original["user_data_dir"]
    exported = client.get("/api/v1/export/profiles").json()
    item = next(p for p in exported["profiles"] if p["name"] == "test")
    item["name"] = "restored"
    imported = client.post("/api/v1/import/profiles", json={"profiles": [item]})
    assert imported.is_success, imported.text
    restored = next(p for p in client.get(PROFILES).json()["data"]["items"] if p["name"] == "restored")
    assert (restored["engine"], restored["local_fonts"]) == ("chromium", "restricted")
    assert restored["media_capture"] == restored["geolocation"] == "blocked"
    assert restored["user_data_dir"] != original["user_data_dir"]
    assert (restored["startup_page"], restored["block_images"]) == ("dashboard", True)


def test_hardware_policy_follows_profile_and_requires_capable_build(client, monkeypatch):
    old_flag = "TEST_MEDIA_ONLY_CHROMIUM"
    engine = client.get("/api/v1/engine").raise_for_status().json()["data"]["chromium"]
    assert engine["supports_hardware_reporting_policy"]
    profile = create_chromium(client, "Hardware", hardware_reporting="limited")
    url = f"{PROFILES}/{profile['id']}"
    assert client.patch(url, json={"remark": "note"}).raise_for_status().json()["data"]["hardware_reporting"] == "limited"
    assert client.post(url + "/duplicate").raise_for_status().json()["data"]["hardware_reporting"] == "limited"
    client.post(url + "/launch").raise_for_status()
    args_file = Path(profile["user_data_dir"]) / "test-args.json"
    try:
        assert LIMIT_HARDWARE in json.loads(args_file.read_text())
        assert client.patch(url, json={"hardware_reporting": "standard"}).status_code == 409
    finally:
        client.post(url + "/stop").raise_for_status()

    monkeypatch.setenv(old_flag, "1")
    old_engine = client.get("/api/v1/engine").raise_for_status().json()["data"]["chromium"]
    assert not old_engine["supports_hardware_reporting_policy"]
    assert not old_engine["installed_versions"][0]["supports_hardware_reporting_policy"]
    assert client.patch(url, json={"chromium_version": old_engine["version"]}).status_code == 422
    refusal = client.post(url + "/launch")
    assert refusal.status_code == 503 and LIMIT_HARDWARE[2:] in refusal.json()["error"]["message"]
    client.patch(url, json={"hardware_reporting": "standard"}).raise_for_status()
    client.post(url + "/launch").raise_for_status()
    try:
        assert LIMIT_HARDWARE not in json.loads(args_file.read_text())
    finally:
        client.post(url + "/stop").raise_for_status()


@pytest.mark.parametrize(("policy", "other"), [
    ("canvas_readback", "webgl_debug_info"),
    ("webgl_debug_info", "canvas_readback"),
])
def test_graphics_policies_are_independent_and_portable(client, policy, other):
    original = create_chromium(client, "Graphics policy", **{policy: "blocked"})
    url = f"{PROFILES}/{original['id']}"
    expected = {policy: "blocked", other: "standard"}
    assert expected.items() <= original.items()
    assert expected.items() <= client.patch(url, json={"remark": "edited"}).raise_for_status().json()["data"].items()
    assert expected.items() <= client.post(url + "/duplicate").raise_for_status().json()["data"].items()
    client.post(url + "/trash").raise_for_status()
    recovered = client.post(f"/api/v1/trash/profiles/{original['id']}/restore").raise_for_status().json()["data"]
    assert expected.items() <= recovered.items()
    for invalid in (None, "randomized"):
        assert client.patch(url, json={policy: invalid}).status_code == 422
    assert client.post(PROFILES, json={"name": "Firefox", "engine": "camoufox", policy: "blocked"}).status_code == 422

    switched = client.patch(url, json={policy: "standard", other: "blocked"}).raise_for_status().json()["data"]
    assert (switched[policy], switched[other]) == ("standard", "blocked")


@pytest.mark.parametrize(("unsupported", "supported", "missing_switch"), [
    ("canvas_readback", "webgl_debug_info", BLOCK_CANVAS),
    ("webgl_debug_info", "canvas_readback", BLOCK_WEBGL),
])
def test_graphics_support_follows_individual_build_capabilities(client, monkeypatch, unsupported, supported, missing_switch):
    monkeypatch.setenv("TEST_MISSING_GRAPHICS_CAPABILITY", missing_switch[2:])
    engine = client.get("/api/v1/engine").raise_for_status().json()["data"]["chromium"]
    pinned = next(item for item in engine["installed_versions"] if item["version"] == engine["version"])
    for info in (engine, pinned):
        assert info[f"supports_{supported}_policy"]
        assert not info[f"supports_{unsupported}_policy"]
    profile = create_chromium(client, "single capability", chromium_version=pinned["version"], **{supported: "blocked"})
    url = f"{PROFILES}/{profile['id']}"
    refused = client.patch(url, json={unsupported: "blocked"})
    assert refused.status_code == 422 and missing_switch[2:] in refused.json()["error"]["message"]
    exported = client.post("/api/v1/export/profiles", json={"ids": [profile["id"]]}).raise_for_status().json()
    exported["profiles"][0][unsupported] = "blocked"
    imported = client.post("/api/v1/import/profiles", json=exported).raise_for_status().json()["data"]
    assert imported["created"] == 0 and missing_switch[2:] in imported["skipped"][0]["reason"]


def test_graphics_policies_cannot_change_while_starting(client):
    from moltskin.services.core import db

    profile = create_chromium(client, "Starting")
    url = f"{PROFILES}/{profile['id']}"
    with db.session() as conn:
        conn.execute("INSERT INTO sessions(id,profile_id,state,started_at) VALUES ('ss_starting',?,'starting',?)",
                     (profile["id"], db.now()))
    for field in ("canvas_readback", "webgl_debug_info"):
        response = client.patch(url, json={field: "blocked"})
        assert response.status_code == 409 and response.json()["error"]["code"] == "PROFILE_RUNNING"
    saved = client.get(url).raise_for_status().json()["data"]
    assert saved["canvas_readback"] == saved["webgl_debug_info"] == "standard"


def test_chromium_window_settings_survive_partial_edits_duplication_and_import(client):
    settings = {"browser_language": "ja-JP", "browser_timezone": "Asia/Tokyo", "browser_window_size": "1280x800"}
    options = client.get("/api/v1/engine").raise_for_status().json()["data"]["chromium"]
    for key, value in settings.items():
        assert value in options[key + "s"]

    original = create_chromium(client, "regional", **settings)
    assert settings.items() <= original.items()
    url = f"{PROFILES}/{original['id']}"
    changed = {**settings, "browser_language": "de-DE"}
    edited = client.patch(url, json={"browser_language": "de-DE"}).raise_for_status().json()["data"]
    assert changed.items() <= edited.items()
    copy = client.post(url + "/duplicate").raise_for_status().json()["data"]
    assert changed.items() <= copy.items()

    exported = client.post("/api/v1/export/profiles", json={"ids": [original["id"]]}).raise_for_status().json()
    assert exported["version"] == 4
    assert changed.items() <= exported["profiles"][0].items()
    assert client.post("/api/v1/import/profiles", json=exported).raise_for_status().json()["data"]["created"] == 1
    items = client.get(PROFILES).raise_for_status().json()["data"]["items"]
    assert len(items) == 3 and all(changed.items() <= item.items() for item in items)

    defaults = dict.fromkeys(settings, "")
    reset = client.patch(url, json=defaults).raise_for_status().json()["data"]
    assert defaults.items() <= reset.items()
    assert client.post("/api/v1/export/profiles", json={"ids": [original["id"]]}).json()["version"] == 1


def test_platform_preview_roundtrip_and_independent_settings(client):
    options = client.get("/api/v1/engine").raise_for_status().json()["data"]["chromium"]
    assert options["preview_platforms"] == ["Windows", "macOS", "Linux"]
    assert options["preview_architectures"] == ["arm64", "x86_64"]
    settings = {"preview_platform": "macOS", "preview_platform_version": "15.4.1", "preview_architecture": "arm64"}
    defaults = dict.fromkeys(settings, "")
    source = create_chromium(client, "preview", **settings)
    assert source["os"] == "native" and settings.items() <= source.items()
    url = f"{PROFILES}/{source['id']}"
    copy = client.post(url + "/duplicate").raise_for_status().json()["data"]
    assert copy["id"] != source["id"] and settings.items() <= copy.items()
    selection = {"ids": [source["id"]]}
    export_url = "/api/v1/export/profiles"
    exported = client.post(export_url, json=selection).raise_for_status().json()
    assert exported["version"] == 7 and settings.items() <= exported["profiles"][0].items()
    imported = client.post("/api/v1/import/profiles", json=exported).raise_for_status().json()["data"]
    assert imported["created"] == 1 and not imported["skipped"]
    listed = client.get(PROFILES).raise_for_status().json()["data"]["items"]
    assert len(listed) == 3 and all(settings.items() <= p.items() for p in listed)

    for key, value in settings.items():
        reset = client.patch(url, json=defaults).raise_for_status().json()["data"]
        assert defaults.items() <= reset.items()
        assert client.post(export_url, json=selection).json()["version"] == 1
        edited = client.patch(url, json={key: value}).raise_for_status().json()["data"]
        assert {**defaults, key: value}.items() <= edited.items()
        assert client.post(export_url, json=selection).json()["version"] == 7
    assert settings.items() <= client.get(f"{PROFILES}/{copy['id']}").json()["data"].items()


def test_platform_preview_validation_and_legacy_import(client):
    chrome = create_chromium(client, "native")
    url = f"{PROFILES}/{chrome['id']}"
    for invalid in ({"preview_platform": "Darwin"}, {"preview_architecture": "x86"}):
        assert client.patch(url, json=invalid).status_code == 422
    for version in (None, 15, "１", "123456", "1.", ".2", "1.2.3.4", "1\n"):
        assert client.patch(url, json={"preview_platform_version": version}).status_code == 422
    fox = client.post(PROFILES, json={"name": "fox"}).raise_for_status().json()["data"]
    fox_url = f"{PROFILES}/{fox['id']}"
    incompatible = [{"name": key, key: value} for key, value in (
        ("preview_platform", "Linux"), ("preview_architecture", "x86_64"), ("preview_platform_version", "1"))]
    for row in incompatible:
        assert client.post(PROFILES, json=row).status_code == 422
        assert client.patch(fox_url, json=row).status_code == 422
    imported = client.post("/api/v1/import/profiles", json={"profiles": incompatible}).raise_for_status().json()["data"]
    assert imported["created"] == 0 and len(imported["skipped"]) == len(incompatible)
    legacy = {"version": 6, "profiles": [{"name": "legacy", "engine": "chromium", "chromium_user_agent": "Legacy/1"}]}
    assert client.post("/api/v1/import/profiles", json=legacy).raise_for_status().json()["data"]["created"] == 1
    listed = client.get(PROFILES).raise_for_status().json()["data"]["items"]
    old = next(p for p in listed if p["name"] == "legacy")
    assert old["preview_platform"] == old["preview_platform_version"] == old["preview_architecture"] == ""
    assert client.get("/api/v1/export/profiles").json()["version"] == 6


def test_platform_preview_presets_do_not_change_launch_arguments(client):
    settings = {"preview_platform": "Windows", "preview_platform_version": "10.0", "preview_architecture": "arm64"}
    profile = create_chromium(client, "preview launch", startup_page="blank")
    url = f"{PROFILES}/{profile['id']}"
    args_file = Path(profile["user_data_dir"]) / "test-args.json"
    launches = []
    for changes in ({}, settings):
        client.patch(url, json=changes).raise_for_status()
        client.post(url + "/launch").raise_for_status()
        try:
            launches.append(json.loads(args_file.read_text()))
            for key, value in settings.items():
                blocked = client.patch(url, json={key: "" if changes else value})
                assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "PROFILE_RUNNING"
        finally:
            client.post(url + "/stop").raise_for_status()
    assert launches[0] == launches[1]


def test_manual_user_agent_roundtrip_and_launch(client):
    agent = 'Moltskin-QA/1.0 (Window "A"; argv boundary)'
    profile = create_chromium(client, "UA original", chromium_user_agent=agent)
    assert profile["chromium_user_agent"] == agent
    url = f"{PROFILES}/{profile['id']}"
    updated = client.patch(url, json={"remark": "updated"}).raise_for_status().json()["data"]
    assert updated["chromium_user_agent"] == agent
    clone = client.post(url + "/duplicate").raise_for_status().json()["data"]
    assert clone["chromium_user_agent"] == agent
    selection = {"ids": [profile["id"]]}
    exported = client.post("/api/v1/export/profiles", json=selection).raise_for_status().json()
    assert exported["version"] == 6 and exported["profiles"][0]["chromium_user_agent"] == agent
    exported["profiles"][0]["name"] = "UA restored"
    restored = client.post("/api/v1/import/profiles", json=exported).raise_for_status().json()["data"]
    assert restored["created"] == 1
    rows = client.get(PROFILES).raise_for_status().json()["data"]["items"]
    assert next(p for p in rows if p["name"] == "UA restored")["chromium_user_agent"] == agent

    argv_file = Path(profile["user_data_dir"]) / "test-args.json"
    for expected in (agent, ""):
        if not expected:
            reset = client.patch(url, json={"chromium_user_agent": ""}).raise_for_status().json()["data"]
            assert reset["chromium_user_agent"] == ""
        client.post(url + "/launch").raise_for_status()
        try:
            args = json.loads(argv_file.read_text())
            switches = [arg for arg in args if arg.startswith("--user-agent=")]
            assert switches == (["--user-agent=" + expected] if expected else [])
            change = client.patch(url, json={"chromium_user_agent": "Change/2"})
            assert change.status_code == 409 and change.json()["error"]["code"] == "PROFILE_RUNNING"
        finally:
            client.post(url + "/stop").raise_for_status()
    assert client.post("/api/v1/export/profiles", json=selection).json()["version"] == 1


def test_manual_user_agent_validation_and_legacy_import(client):
    value = "A" * 512
    original = create_chromium(client, "UA boundary", chromium_user_agent=value)
    url = f"{PROFILES}/{original['id']}"
    invalid = ("A" * 513, " UA", "UA ", " ", "UA\r\nExtra: x", "UA\tX", "UA\x00", "UA\x7f", "版", None)
    for bad in invalid:
        assert client.patch(url, json={"chromium_user_agent": bad}).status_code == 422
    assert client.get(url).json()["data"]["chromium_user_agent"] == value
    other_engine = {"name": "Firefox UA", "engine": "camoufox", "chromium_user_agent": "UA/1.0"}
    assert client.post(PROFILES, json=other_engine).status_code == 422
    rows = [other_engine, {"name": "bad control", "engine": "chromium", "chromium_user_agent": "UA\nX"}]
    skipped = client.post("/api/v1/import/profiles", json={"profiles": rows}).raise_for_status().json()["data"]
    assert skipped["created"] == 0 and len(skipped["skipped"]) == 2
    legacy = {"version": 5, "profiles": [{"name": "Older profile", "engine": "chromium"}]}
    assert client.post("/api/v1/import/profiles", json=legacy).json()["data"]["created"] == 1
    items = client.get(PROFILES).json()["data"]["items"]
    assert next(p for p in items if p["name"] == "Older profile")["chromium_user_agent"] == ""


def test_chromium_startup_controls_roundtrip(client):
    custom = {"chromium_mute_audio": True, "chromium_renderer_limit": 4, "chromium_js_heap_mb": 256}
    original = create_chromium(client, "audio & processes", **custom)
    assert custom.items() <= original.items()
    url = f"{PROFILES}/{original['id']}"
    updated = client.patch(url, json={"chromium_renderer_limit": 2}).raise_for_status().json()["data"]
    changed = {**custom, "chromium_renderer_limit": 2}
    assert changed.items() <= updated.items()
    duplicate = client.post(url + "/duplicate").raise_for_status().json()["data"]
    assert changed.items() <= duplicate.items()
    exported = client.post("/api/v1/export/profiles", json={"ids": [original["id"]]}).raise_for_status().json()
    assert exported["version"] == 5 and changed.items() <= exported["profiles"][0].items()
    result = client.post("/api/v1/import/profiles", json=exported).raise_for_status().json()["data"]
    assert result["created"] == 1
    profiles = client.get(PROFILES).raise_for_status().json()["data"]["items"]
    assert len(profiles) == 3 and all(changed.items() <= row.items() for row in profiles)
    for invalid in ({"chromium_renderer_limit": True}, {"chromium_renderer_limit": 3},
                    {"chromium_renderer_limit": "4"}, {"chromium_js_heap_mb": -1},
                    {"chromium_js_heap_mb": None}, {"chromium_mute_audio": "on"}):
        assert client.patch(url, json=invalid).status_code == 422
    assert client.post(PROFILES, json={"name": "wrong engine", **custom}).status_code == 422
    defaults = {"chromium_mute_audio": False, "chromium_renderer_limit": 0, "chromium_js_heap_mb": 0}
    assert defaults.items() <= client.patch(url, json=defaults).raise_for_status().json()["data"].items()
    assert client.post("/api/v1/export/profiles", json={"ids": [original["id"]]}).json()["version"] == 1


def test_window_settings_reject_unsupported_inputs_and_other_engine(client):
    url = f"{PROFILES}/{create_chromium(client, 'original')['id']}"
    for fields in ({"browser_language": "--no-sandbox"}, {"browser_language": None},
                   {"browser_timezone": "../../etc/passwd"}, {"browser_window_size": "0x0"}):
        assert client.patch(url, json=fields).status_code == 422
        assert client.post(PROFILES, json={"name": "invalid", "engine": "chromium", **fields}).status_code == 422
    rows = [{"name": key, key: value} for key, value in (
        ("browser_language", "en-GB"), ("browser_timezone", "UTC"), ("browser_window_size", "1024x768"))]
    for row in rows:
        assert client.post(PROFILES, json=row).status_code == 422
    result = client.post("/api/v1/import/profiles", json={"profiles": rows}).raise_for_status().json()["data"]
    assert result["created"] == 0 and len(result["skipped"]) == len(rows)


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="Chromium preview targets macOS and Linux")
def test_missing_binary_never_leaves_a_running_session(client, data_dir, monkeypatch):
    engines = client.get("/api/v1/engine").json()["data"]
    assert {"camoufox", "playwright", "browsers"} <= engines.keys()
    assert engines["chromium"]["available"] is True
    assert engines["chromium"]["supports_media_capture_policy"] is True
    assert engines["chromium"]["supports_geolocation_policy"] is True
    assert engines["chromium"]["supports_webgl_debug_info_policy"] is True
    assert engines["chromium"]["supports_canvas_readback_policy"] is True
    assert engines["chromium"]["version"] == "158.0.0.1"
    profile = create_chromium(client, "missing-build")
    monkeypatch.setenv("MOLTSKIN_CHROMIUM_BINARY", str(data_dir / "missing"))
    missing = client.get("/api/v1/engine").json()["data"]["chromium"]
    assert missing["available"] is False
    assert missing["supports_media_capture_policy"] is False
    assert missing["supports_geolocation_policy"] is False
    assert missing["supports_webgl_debug_info_policy"] is False
    assert missing["supports_canvas_readback_policy"] is False
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
@pytest.mark.parametrize(("legacy_flag", "policy", "switch"), [
    ("TEST_LEGACY_CHROMIUM", "media_capture", BLOCK_MEDIA),
    ("TEST_MEDIA_ONLY_CHROMIUM", "geolocation", BLOCK_LOCATION),
    ("TEST_MEDIA_ONLY_CHROMIUM", "webgl_debug_info", BLOCK_WEBGL),
    ("TEST_MEDIA_ONLY_CHROMIUM", "canvas_readback", BLOCK_CANVAS),
])
def test_legacy_binary_refuses_unsupported_policy_before_start(client, monkeypatch, legacy_flag, policy, switch):
    from moltskin.services.core import db

    monkeypatch.setenv(legacy_flag, "1")
    engine = client.get("/api/v1/engine").json()["data"]["chromium"]
    assert engine["available"] is True
    assert engine["supports_media_capture_policy"] == (legacy_flag == "TEST_MEDIA_ONLY_CHROMIUM")
    assert engine["supports_geolocation_policy"] is False
    assert engine["supports_webgl_debug_info_policy"] is False
    assert engine["supports_canvas_readback_policy"] is False
    profile = create_chromium(client, "old-browser", **{policy: "blocked"})
    url = f"{PROFILES}/{profile['id']}"
    failure = client.post(url + "/launch")
    assert failure.status_code == 503
    assert failure.json()["error"]["code"] == "CHROMIUM_UNAVAILABLE"
    assert switch[2:] in failure.json()["error"]["message"]
    assert not (Path(profile["user_data_dir"]) / "test-args.json").exists()
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE profile_id=?", (profile["id"],)).fetchone()[0] == 0
    assert client.patch(url, json={policy: "standard"}).is_success
    try:
        response = client.post(url + "/launch")
        assert response.is_success, response.text
        args = json.loads((Path(profile["user_data_dir"]) / "test-args.json").read_text())
        assert switch not in args
    finally:
        client.post(url + "/stop")


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="Chromium preview targets macOS and Linux")
def test_two_native_sessions_and_restart(client, monkeypatch):
    from moltskin.services.core import config
    from moltskin.services.core.security import workbench_signature

    monkeypatch.setattr(config, "OPEN_MANAGER", True)
    monkeypatch.setattr(config, "PORT", 18765)

    a = create_chromium(client, "a", local_fonts="restricted", media_capture="blocked",
                        chromium_mute_audio=True, chromium_renderer_limit=4, chromium_js_heap_mb=512)
    b = create_chromium(client, "b", geolocation="blocked")
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
        assert client.patch(urls[0], json={"media_capture": "standard"}).status_code == 409
        assert client.patch(urls[0], json={"chromium_mute_audio": False}).status_code == 409
        assert client.patch(urls[0], json={"chromium_renderer_limit": 0}).status_code == 409
        assert client.patch(urls[0], json={"chromium_js_heap_mb": 0}).status_code == 409
        assert client.patch(urls[1], json={"geolocation": "standard"}).status_code == 409
        for field, value in (("browser_language", "en-US"), ("browser_timezone", "UTC"), ("browser_window_size", "1440x900")):
            assert client.patch(urls[0], json={field: value}).status_code == 409
        for profile, restricted, session_id in ((a, True, first_sessions[0]), (b, False, first_sessions[1])):
            args = json.loads((Path(profile["user_data_dir"]) / "test-args.json").read_text())
            assert f"--user-data-dir={profile['user_data_dir']}" in args
            assert (RESTRICT_FONTS in args) == restricted
            assert (BLOCK_MEDIA in args) == restricted
            assert ("--mute-audio" in args) == (profile == a)
            assert ("--js-flags=--max-old-space-size=512" in args) == (profile == a)
            limits = [arg for arg in args if arg.startswith("--renderer-process-limit=")]
            assert limits == (["--renderer-process-limit=4"] if profile == a else [])
            assert (BLOCK_LOCATION in args) == (profile == b)
            assert "--use-mock-keychain" not in args
            assert "--moltskin-panel-port=18765" in args
            assert args[-1] == f"http://127.0.0.1:18765/workbench/{session_id}/{workbench_signature(session_id)}"
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
        assert client.patch(urls[0], json={"local_fonts": "standard", "media_capture": "standard", "geolocation": "blocked",
                                          "startup_page": "dashboard", "block_images": True}).is_success
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
        assert BLOCK_MEDIA not in args
        assert BLOCK_LOCATION in args
        assert marker.read_text() == "profile A"
    finally:
        for url in urls:
            client.post(url + "/stop")


def test_workbench_reads_only_the_current_session(client, monkeypatch):
    from moltskin.services.core import config, main
    from moltskin.services.core.security import get_token, workbench_signature

    monkeypatch.setattr(config, "OPEN_MANAGER", True)
    monkeypatch.setattr(config, "FORCE_HEADLESS", False)
    group = client.post("/api/v1/groups", json={"name": "研发组"}).raise_for_status().json()["data"]
    ids = iter(["pf_000000000001", "pf_000000000002"])
    monkeypatch.setattr(main, "new_id", lambda prefix: next(ids))
    first = create_chromium(client, "Alpha", group_id=group["id"], remark="</script><b>QA & 验收</b>")
    second = create_chromium(client, "Beta", remark="独立环境")
    paths = []
    try:
        for profile, version in ((first, "158.0.0.1"), (second, "158.0.0.2")):
            monkeypatch.setenv("TEST_CHROMIUM_VERSION", version)
            launched = client.post(f"{PROFILES}/{profile['id']}/launch").raise_for_status().json()["data"]
            session_id = launched["session_id"]
            session = client.get(f"/api/v1/sessions/{session_id}").raise_for_status().json()["data"]
            args = json.loads((Path(profile["user_data_dir"]) / "test-args.json").read_text())
            path = f"/workbench/{session_id}/{workbench_signature(session_id)}"
            assert args[-1] == f"http://127.0.0.1:{config.PORT}{path}"
            paths.append(path)
            response = client.get(path, headers={"Authorization": ""}).raise_for_status()
            assert response.headers["referrer-policy"] == "no-referrer"
            assert response.headers["cache-control"] == "no-store"
            assert "connect-src 'self' https://ipinfo.io https://api64.ipify.org;" in response.headers["content-security-policy"]
            assert get_token() not in response.text and get_token() not in args[-1]
            assert "</script><b>QA" not in response.text
            embedded = re.search(r'<script\b[^>]*\bid="workbenchContext"[^>]*>(.*?)</script>', response.text, re.S)
            context = json.loads(embedded.group(1))
            assert context == {
                "id": profile["id"], "name": profile["name"], "group_name": "研发组" if profile == first else "",
                "remark": profile["remark"], "engine": "chromium", "started_at": session["started_at"],
                "browser_version": version, "session_id": session_id, "proxy_label": "系统网络",
            }
        crossed = paths[0].rsplit("/", 1)[0] + "/" + paths[1].rsplit("/", 1)[1]
        unknown_id = "ss_000000000000"
        for invalid in (crossed, paths[0] + "a", f"/workbench/{unknown_id}/{workbench_signature(unknown_id)}"):
            response = client.get(invalid)
            assert response.status_code == 404 and first["id"] not in response.text and second["id"] not in response.text
        client.post(f"{PROFILES}/{first['id']}/stop").raise_for_status()
        assert client.get(paths[0]).status_code == 404
        assert client.get(paths[1]).is_success
        client.delete(f"{PROFILES}/{first['id']}").raise_for_status()
        assert client.get(paths[0]).status_code == 404
    finally:
        for profile in (first, second):
            client.post(f"{PROFILES}/{profile['id']}/stop")


@pytest.mark.parametrize(("startup", "host_allows", "global_manager", "headless", "target"), [
    ("default", True, False, False, "about:blank"),
    ("default", False, True, False, "about:blank"),
    ("default", True, True, True, "about:blank"),
    ("blank", True, True, False, "about:blank"),
    ("dashboard", True, False, True, "http://127.0.0.1:18765/#/home"),
    ("dashboard", False, True, False, "about:blank"),
])
def test_chromium_respects_startup_preferences(client, monkeypatch, startup, host_allows, global_manager, headless, target):
    from moltskin.services.core import config, db

    monkeypatch.setattr(config, "OPEN_MANAGER", host_allows)
    monkeypatch.setattr(config, "PORT", 18765)
    db.set_setting("open_manager_on_launch", global_manager)
    profile = create_chromium(client, "startup preference", startup_page=startup, headless=headless)
    url = f"{PROFILES}/{profile['id']}"
    client.post(url + "/launch").raise_for_status()
    try:
        args = json.loads((Path(profile["user_data_dir"]) / "test-args.json").read_text())
        assert args[-1] == target
    finally:
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


def test_choose_installed_build_and_protect_profile_data(client, data_dir, monkeypatch):
    builds = data_dir / "registered"
    monkeypatch.setenv("MOLTSKIN_CHROMIUM_BUILDS_DIR", str(builds))
    executable = Path("Chromium.app/Contents/MacOS/Chromium") if sys.platform == "darwin" else Path("chrome")
    old_binary = builds / "older" / executable
    old_binary.parent.mkdir(parents=True)
    source = (data_dir / "test-chromium").read_text().replace(
        'os.environ.get("TEST_CHROMIUM_VERSION", "158.0.0.1")', '"147.0.0.1"',
    ).replace('if not os.environ.get("TEST_LEGACY_CHROMIUM"):', 'if False:')
    old_binary.write_text(source)
    old_binary.chmod(0o700)
    duplicate = builds / "second-copy" / executable
    duplicate.parent.mkdir(parents=True)
    duplicate.symlink_to(old_binary)
    versions = client.get("/api/v1/engine").json()["data"]["chromium"]
    assert versions["version"] == "158.0.0.1"
    assert [v["version"] for v in versions["installed_versions"]] == ["158.0.0.1", "147.0.0.1"]
    assert not versions["installed_versions"][1]["supports_media_capture_policy"]
    assert [v["supports_webgl_debug_info_policy"] for v in versions["installed_versions"]] == [True, False]
    assert [v["supports_canvas_readback_policy"] for v in versions["installed_versions"]] == [True, False]

    with monkeypatch.context() as patch:
        patch.setenv("MOLTSKIN_CHROMIUM_BINARY", str(data_dir / "uninstalled"))
        alternate = client.get("/api/v1/engine").json()["data"]["chromium"]
        assert alternate["available"] and alternate["version"] is None
        assert [v["version"] for v in alternate["installed_versions"]] == ["147.0.0.1"]

    for field in ("webgl_debug_info", "canvas_readback"):
        policy = {field: "blocked"}
        unsupported = {"name": "bad pin", "engine": "chromium", "chromium_version": "147.0.0.1", **policy}
        assert client.post(PROFILES, json=unsupported).status_code == 422
        pinned = create_chromium(client, field, chromium_version="158.0.0.1", **policy)
        pinned_url = f"{PROFILES}/{pinned['id']}"
        assert client.patch(pinned_url, json={"chromium_version": "147.0.0.1"}).status_code == 422
        assert client.get(pinned_url).json()["data"]["chromium_version"] == "158.0.0.1"
    original = create_chromium(client, "pinned", chromium_version="147.0.0.1")
    url = f"{PROFILES}/{original['id']}"
    for invalid in ("147.0.0.2", str(old_binary)):
        assert client.patch(url, json={"chromium_version": invalid}).status_code == 422
    assert client.patch(url, json={"media_capture": "blocked"}).status_code == 422
    assert client.patch(url, json={"webgl_debug_info": "blocked"}).status_code == 422
    assert client.patch(url, json={"canvas_readback": "blocked"}).status_code == 422
    assert client.post(PROFILES, json={"name": "other-engine", "chromium_version": "147.0.0.1"}).status_code == 422
    assert client.post(url + "/duplicate").json()["data"]["chromium_version"] == "147.0.0.1"
    export = client.post("/api/v1/export/profiles", json={"ids": [original["id"]]}).json()
    assert export["version"] == 5 and export["profiles"][0]["chromium_version"] == "147.0.0.1"
    assert client.post("/api/v1/import/profiles", json=export).json()["data"]["created"] == 1

    def launch(version):
        try:
            started = client.post(url + "/launch").raise_for_status().json()["data"]
            assert started["browser_version"] == version
            assert client.patch(url, json={"chromium_version": "158.0.0.1" if version == "147.0.0.1" else "147.0.0.1"}).status_code == 409
        finally:
            client.post(url + "/stop")

    launch("147.0.0.1")
    client.patch(url, json={"chromium_version": ""}).raise_for_status()
    launch("158.0.0.1")
    client.patch(url, json={"chromium_version": "147.0.0.1"}).raise_for_status()
    rejected = client.post(url + "/launch")
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "CHROMIUM_PROFILE_VERSION"
    old_binary.unlink()
    rejected = client.post(url + "/launch")
    assert rejected.status_code == 503 and "147.0.0.1" in rejected.json()["error"]["message"]
    client.patch(url, json={"chromium_version": ""}).raise_for_status()

    marker = Path(original["user_data_dir"]) / "Last Version"
    marker.write_text("159.0.0.1")
    assert client.post(url + "/launch").status_code == 409
    assert marker.read_text() == "159.0.0.1"
    monkeypatch.setenv("TEST_CHROMIUM_VERSION", "159.0.0.2")
    client.post(url + "/launch").raise_for_status()
    client.post(url + "/stop").raise_for_status()
