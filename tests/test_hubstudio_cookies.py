"""Import one Hubstudio export per profile through the local API.

Include the real Camoufox restart test with an installed browser binary:
    MOLTSKIN_TEST_NATIVE_BROWSERS=1 python -m pytest -q tests/test_hubstudio_cookies.py
"""

import json
import os
import queue
import sqlite3
import threading
import time
from contextlib import ExitStack, closing
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from moltskin.services.core import config, cookies
from moltskin.services.core.main import app
from moltskin.services.core.security import get_token


@pytest.fixture
def api(data_dir):
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {get_token()}"
        yield client


def cookie_endpoint(api, name):
    profile = api.post("/api/v1/profiles", json={"name": name}).raise_for_status()
    return f"/api/v1/profiles/{profile.json()['data']['id']}/cookies"


def hub_cookie(value):
    return {
        "Name": "sid", "Domain": ".example.test", "Path": "/dashboard", "Value": value,
        "Secure": True, "HttpOnly": True, "Persistent": "1", "HasExpires": "1",
        "Expires": "2030-09-14T15:18:07.389+08:00", "Samesite": "2",
        "Creation": "2024-09-14T15:18:07.389+08:00", "LastAccess": "2025-09-14T15:18:07.389+08:00",
        "Priority": "1", "SourceScheme": "2", "Firstpartyonly": "", "schemeMap": False, "isSelf": False,
    }


def test_two_hubstudio_exports_stay_in_their_selected_profiles(api):
    a = cookie_endpoint(api, "Hubstudio A")
    b = cookie_endpoint(api, "Hubstudio B")
    account_a = "fixture-A+=%"
    account_b = "fixture-B+=%"
    text_a = json.dumps([hub_cookie(account_a)])
    text_b = json.dumps({"code": 0, "msg": "Success", "data": json.dumps([hub_cookie(account_b)])})
    for path, text in [(a, text_a), (b, text_b)]:
        result = api.put(path, json={"text": text, "mode": "replace"}).raise_for_status()
        assert result.json()["data"] == {"parsed": 1, "applied": 1, "source": "hubstudio", "mode": "replace"}

    expiry = datetime.fromisoformat("2030-09-14T15:18:07.389+08:00").timestamp()
    for path, expected in [(a, account_a), (b, account_b)]:
        data = api.get(path).raise_for_status().json()["data"]
        assert data["cookies"] == [{
            "name": "sid", "domain": ".example.test", "path": "/dashboard", "value": expected,
            "secure": True, "httpOnly": True, "sameSite": "strict", "expires": expiry,
        }]


def test_bad_hubstudio_replace_preserves_existing_cookies(api):
    path = cookie_endpoint(api, "Hubstudio invalid import")
    previous = {"name": "sid", "value": "original", "domain": ".example.test"}
    api.put(path, json={"cookies": [previous, hub_cookie("existing-persistent")]}).raise_for_status()
    original = api.get(path).raise_for_status().json()["data"]

    invalid_exports = [
        {"code": 1, "msg": "Error", "data": json.dumps([hub_cookie("unexpected")])},
        [hub_cookie("partial"), {"Name": "missing-domain", "Value": "incomplete"}],
        [{"Name": "only-name"}],
        [hub_cookie("partial"), None],
        [{**hub_cookie("partial"), "Secure": "invalid"}],
        [{**hub_cookie("partial"), "Samesite": "3"}],
        [{**hub_cookie("partial"), "Path": []}],
        [{**hub_cookie("partial"), "Domain": "https://example.test/login"}],
        [{**hub_cookie("partial"), "Expires": "not a timestamp"}],
        [{**hub_cookie("partial"), "Firstpartyonly": "1"}],
        [{**hub_cookie("partial"), "partitionKey": {"topLevelSite": "https://example.test"}}],
        [{**hub_cookie("partial"), "partitionKey": {}}],
        {"other": "unrecognized"},
        "# unrelated text",
        "example.test\tFALSE\t/\tTRUE\t1800000000\tok\tpartial\ninvalid",
        "[{",
    ]
    for payload in invalid_exports:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        result = api.put(path, json={"text": text, "mode": "replace"})
        assert result.status_code == 422
        assert result.json()["error"]["code"] == "INVALID_COOKIES"
        assert "partial" not in result.text
        assert api.get(path).raise_for_status().json()["data"] == original
    for empty in ([], {"cookies": []}, {"code": 0, "data": "[]"}, "# Netscape HTTP Cookie File\n"):
        result = api.put(path, json={"cookies": empty, "mode": "replace"})
        assert result.status_code == 422
        assert result.json()["error"]["code"] == "NO_COOKIES"
        assert api.get(path).raise_for_status().json()["data"] == original
    api.delete(path).raise_for_status()
    assert api.get(path).raise_for_status().json()["data"]["total"] == 0


def test_cookie_mutations_hold_launch_reservation_until_write_finishes(api, monkeypatch):
    source = cookie_endpoint(api, "source")
    target = cookie_endpoint(api, "target")
    row = {"name": "sid", "value": "test", "domain": "example.test"}
    api.put(source, json={"cookies": [row]}).raise_for_status()
    write = cookies.write_cookies
    reservations = []

    def check_reservation(*args, **kwargs):
        with closing(sqlite3.connect(config.DB_PATH, timeout=0)) as competing_launch:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competing_launch.execute("BEGIN IMMEDIATE")
        reservations.append(True)
        return write(*args, **kwargs)

    monkeypatch.setattr(cookies, "write_cookies", check_reservation)
    api.put(target, json={"cookies": [row]}).raise_for_status()
    source_id = source.split("/")[-2]
    api.post(f"{target}/copy-from/{source_id}").raise_for_status()
    api.delete(target).raise_for_status()
    assert len(reservations) == 3


def test_storage_state_and_netscape_round_trip(api):
    path = cookie_endpoint(api, "standard formats")
    row = {"name": "flags", "value": "", "domain": ".example.test", "path": "/",
           "expires": 1893456000, "sameSite": "Lax", "httpOnly": "true", "secure": "false"}
    res = api.put(path, json={"cookies": {"cookies": [row], "origins": []}}).raise_for_status()
    assert res.json()["data"]["source"] == "storage_state"
    text = api.get(path + "/export?format=netscape").raise_for_status().text
    assert "#HttpOnly_.example.test\tTRUE\t/\tFALSE\t1893456000\tflags\t\n" in text
    result = api.put(path, json={"text": text, "mode": "replace"}).raise_for_status()
    assert result.json()["data"]["source"] == "netscape"
    assert api.get(path).raise_for_status().json()["data"]["cookies"] == [
        {**row, "sameSite": "unspecified", "secure": False, "httpOnly": True}
    ]


@pytest.mark.parametrize("scope, partitioned", [
    ("^userContextId=7", False), ("", True), ("^partitionKey=%28https%2Cexample.test%29", True),
])
def test_scoped_native_cookie_is_preserved_in_json_and_blocks_lossy_transfers(api, scope, partitioned):
    source = cookie_endpoint(api, "scope source")
    target = cookie_endpoint(api, "scope destination")
    target_profile = target.removesuffix("/cookies")
    source_id = source.split("/")[-2]
    api.put(source, json={"cookies": [hub_cookie("ordinary")]}).raise_for_status()
    api.put(target, json={"cookies": [hub_cookie("existing")]}).raise_for_status()
    original = api.get(target).raise_for_status().json()["data"]
    source_db = cookies.cookies_path(config.profile_directory(source_id))
    with closing(sqlite3.connect(source_db)) as conn, conn:
        conn.execute(
            "INSERT INTO moz_cookies(name,host,path,value,originAttributes,isPartitionedAttributeSet) "
            "VALUES ('scoped','.example.test','/','scoped-fixture',?,?)",
            (scope, int(partitioned)),
        )
    rows = api.get(source).raise_for_status().json()["data"]["cookies"]
    ordinary, scoped = sorted(rows, key=lambda row: row["value"])
    assert "originAttributes" not in ordinary and "isPartitionedAttributeSet" not in ordinary
    assert scoped.get("originAttributes", "") == scope
    assert scoped.get("isPartitionedAttributeSet", False) is partitioned
    exported = api.get(source + "/export?format=json").raise_for_status().json()
    assert exported["cookies"] == rows

    text_export = api.get(source + "/export?format=netscape")
    assert text_export.status_code == 422
    assert text_export.json()["error"]["code"] == "UNSUPPORTED_COOKIE_SCOPE"
    assert "content-disposition" not in text_export.headers
    assert "scoped-fixture" not in text_export.text
    for result in (
        api.put(target, json={"cookies": exported, "mode": "replace"}),
        api.post(target + "/copy-from/" + source_id, params={"mode": "replace"}),
        api.post(target_profile + "/sync-from/" + source_id, params={"parts": "cookies,storage"}),
    ):
        assert result.status_code == 422
        assert result.json()["error"]["code"] == "INVALID_COOKIES"
        assert "scoped-fixture" not in result.text
    assert api.get(target).raise_for_status().json()["data"] == original


def test_profile_backup_includes_pending_session_import(api):
    path = cookie_endpoint(api, "pending session backup")
    row = {"name": "session", "value": "backup-fixture", "domain": "example.test"}
    api.put(path, json={"cookies": [row]}).raise_for_status()
    original = api.get(path).raise_for_status().json()["data"]
    archive = api.get("/api/v1/backup?include_profiles=true").raise_for_status().content
    api.delete(path).raise_for_status()
    api.post("/api/v1/backup/restore", files={"file": ("backup.zip", archive)}).raise_for_status()
    assert api.get(path).raise_for_status().json()["data"] == original


def test_import_upgrades_old_moltskin_database_and_rolls_back_sql_errors(tmp_path):
    with closing(sqlite3.connect(cookies.cookies_path(tmp_path))) as db:
        db.executescript(cookies.SCHEMA)
        db.executemany(
            "INSERT INTO moz_cookies(name,value,host,path,expiry,sameSite) VALUES (?,?,'example.test','/',?,?)",
            [("old", "persistent", 1893456000, 3), ("visit", "session", 0, 0)],
        )
        db.commit()
        cookies.write_cookies(tmp_path, [hub_cookie("new")])
        original = cookies.read_cookies(tmp_path)
        rows = {row["name"]: row for row in original}
        assert rows["visit"]["expires"] == -1
        assert (rows["old"]["expires"], rows["old"]["sameSite"]) == (1893456000, "no_restriction")
        assert db.execute("SELECT expiry FROM moz_cookies WHERE name='old'").fetchone() == (1893456000000,)
        db.execute(
            "CREATE TRIGGER fail_insert BEFORE INSERT ON moz_cookies BEGIN "
            "SELECT RAISE(ABORT, 'simulated partial write failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            cookies.write_cookies(tmp_path, [hub_cookie("replacement")], mode="replace")
        assert cookies.read_cookies(tmp_path) == original


def test_import_matches_camoufox_v17_cookie_storage(tmp_path):
    # An API round trip alone can hide a unit mismatch if the reader and writer
    # both assume seconds. Schema 17 stores expiry in milliseconds.
    with closing(sqlite3.connect(cookies.cookies_path(tmp_path))) as db:
        db.executescript(cookies.SCHEMA)
        db.execute("PRAGMA user_version=17")
        record = {**hub_cookie("test-only"), "Samesite": "0"}
        db.execute(
            "INSERT INTO moz_cookies (name,host,path,originAttributes,value) VALUES (?,?,?,?,?)",
            (record["Name"], record["Domain"], record["Path"], "^partitionKey=example.test", "partition fixture"),
        )
        db.commit()
        items, _ = cookies.parse_payload([record])
        cookies.write_cookies(tmp_path, items)
        expiry, same_site = db.execute("SELECT expiry, sameSite FROM moz_cookies WHERE originAttributes=''").fetchone()
        expected_seconds = datetime.fromisoformat(record["Expires"]).timestamp()
        assert expiry == round(expected_seconds * 1000)
        assert same_site == 0  # Native HTTP Set-Cookie with explicit SameSite=None.
        assert db.execute("SELECT value FROM moz_cookies WHERE originAttributes!=''").fetchall() == [("partition fixture",)]


def test_session_only_import_delivered_once_to_selected_profile(tmp_path):
    a, b = tmp_path / "profile-a", tmp_path / "profile-b"
    record = {**hub_cookie("session-only"), "HasExpires": "0", "Persistent": "0"}
    items, _ = cookies.parse_payload([record])
    cookies.write_cookies(a, items)
    assert cookies.read_cookies(a)[0]["expires"] == -1

    browser = Mock()
    cookies.apply_pending_session_cookies(b, browser)
    browser.add_cookies.assert_not_called()
    browser.add_cookies.side_effect = RuntimeError("mock launch failure")
    with pytest.raises(RuntimeError):
        cookies.apply_pending_session_cookies(a, browser)
    assert cookies.read_cookies(a)[0]["value"] == "session-only"
    browser.reset_mock(side_effect=True)

    cookies.apply_pending_session_cookies(a, browser)
    browser.add_cookies.assert_called_once_with([{
        "name": "sid", "value": "session-only", "domain": ".example.test", "path": "/dashboard",
        "secure": True, "httpOnly": True, "sameSite": "Strict",
    }])
    cookies.apply_pending_session_cookies(a, browser)
    assert browser.add_cookies.call_count == 1


@pytest.mark.skipif(os.environ.get("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="requires installed Camoufox")
def test_two_real_browsers_send_only_their_account_cookies_after_restart(api, monkeypatch):
    requests = queue.Queue()

    class AccountSite(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/account/A", "/account/B"):
                received = SimpleCookie(self.headers.get("Cookie", ""))
                requests.put((self.path, {k: item.value for k, item in received.items()}))
            self.send_response(200)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Account session check")

        def log_message(self, *args):
            pass

    def stop_profile(path):
        api.post(path + "/stop").raise_for_status()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if api.get(path + "/status").json()["data"]["status"] == "ready":
                return
            time.sleep(0.1)
        pytest.fail("Timed out closing test browser")

    with ThreadingHTTPServer(("127.0.0.1", 0), AccountSite) as site, ExitStack() as cleanup:
        thread = threading.Thread(target=site.serve_forever, daemon=True)
        thread.start()
        cleanup.callback(thread.join)
        cleanup.callback(site.shutdown)
        monkeypatch.setattr(config, "OPEN_MANAGER", True)
        api.put("/api/v1/settings", json={"manager_button": False}).raise_for_status()
        profiles = {}
        for account in ("A", "B"):
            url = f"http://127.0.0.1:{site.server_port}/account/{account}"
            response = api.post("/api/v1/profiles", json={
                "name": f"Browser account {account}", "headless": True,
            }).raise_for_status()
            path = "/api/v1/profiles/" + response.json()["data"]["id"]
            cleanup.callback(api.delete, path, params={"delete_data": "true"})
            if account == "B":
                # Exercise both a fresh directory and a database created by the native browser.
                monkeypatch.setattr(config, "MANAGER_URL", f"http://127.0.0.1:{site.server_port}/bootstrap")
                api.post(path + "/launch").raise_for_status()
                stop_profile(path)
            persistent = {**hub_cookie(account), "Domain": "127.0.0.1", "Path": "/", "Secure": False}
            session = {**persistent, "Name": "visit", "Persistent": "0", "HasExpires": "0"}
            api.put(path + "/cookies", json={"text": json.dumps([persistent, session])}).raise_for_status()
            profiles[path] = url

        for first_launch in (True, False):
            with ExitStack() as running:
                for path, url in profiles.items():
                    monkeypatch.setattr(config, "MANAGER_URL", url)
                    api.post(path + "/launch").raise_for_status()
                    running.callback(stop_profile, path)
                observed = dict(requests.get(timeout=30) for _ in profiles)
                for account in ("A", "B"):
                    received = observed[f"/account/{account}"]
                    assert received["sid"] == account
                    if first_launch:
                        assert received["visit"] == account
                    elif "visit" in received:  # Firefox can restore a previous browser session.
                        assert received["visit"] == account
