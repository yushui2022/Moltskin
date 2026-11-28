"""Restore configuration backups into a different data root."""

import io
import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from moltskin.services.core import backup, config, db


def fixture_archive(source: Path, ids, *, legacy=False):
    source.mkdir()
    database = source / "source.db"
    with closing(sqlite3.connect(database)) as c, c:
        c.executescript(db.SCHEMA)
        for i, ident in enumerate(ids):
            c.execute(
                "INSERT INTO profiles(id,name,engine,user_data_dir,local_fonts,status,last_session_id,created_at,updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)",
                (ident, f"profile {i}", "camoufox" if legacy or i != 1 else "chromium",
                 str(source / "profiles" / ident), "restricted" if not legacy and i == 1 else "standard",
                 f"current{i}", db.now(), db.now()),
            )
            c.execute(
                "INSERT INTO sessions(id,profile_id,pid,state,started_at) VALUES (?,?,100,'running',?)",
                (f"current{i}", ident, db.now()),
            )
        if ids:
            c.execute(
                "INSERT INTO sessions(id,profile_id,pid,state,started_at,ended_at) "
                "VALUES ('old-completed', ?, 456, 'stopped', 'yesterday', 'then')", (ids[0],),
            )
        if legacy:
            c.execute("ALTER TABLE profiles DROP COLUMN hardware_reporting")
            c.execute("ALTER TABLE profiles DROP COLUMN canvas_readback")
            c.execute("ALTER TABLE profiles DROP COLUMN webgl_debug_info")
            c.execute("ALTER TABLE profiles DROP COLUMN local_fonts")
            c.execute("ALTER TABLE profiles DROP COLUMN engine")
        else:
            c.execute("UPDATE profiles SET hardware_reporting='limited', canvas_readback='blocked', webgl_debug_info='blocked' WHERE engine='chromium'")
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as zf:
        zf.write(database, backup.DB_NAME)
    return result.getvalue()


@pytest.mark.parametrize("legacy, ids", [(True, ["old-id"]), (False, ["pf_fox", "pf_chrome"])])
def test_cross_root_restore_rebases_paths_and_disarms_stale_sessions(data_dir, tmp_path, legacy, ids):
    db.init_db()
    archive = fixture_archive(tmp_path / "source", ids, legacy=legacy)
    with closing(db.connect()) as live:
        # Hold this connection open so the newest committed row still lives in the WAL.
        live.execute("PRAGMA wal_autocheckpoint=0")
        live.execute("INSERT INTO groups (id,name,created_at,updated_at) VALUES ('previous','before restore',?,?)",
                     (db.now(), db.now()))
        live.commit()
        assert Path(str(config.DB_PATH) + "-wal").stat().st_size > 0
        result = backup.restore(archive)
        assert live.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == len(ids)
        assert not live.execute("SELECT 1 FROM groups WHERE id='previous'").fetchone()
    with closing(sqlite3.connect(result["backup_path"])) as before:
        assert before.execute("SELECT name FROM groups WHERE id='previous'").fetchone() == ("before restore",)
    db.init_db()
    with db.session() as c:
        rows = c.execute("SELECT * FROM profiles ORDER BY name").fetchall()
        assert [Path(row["user_data_dir"]) for row in rows] == [config.PROFILES_DIR / ident for ident in ids]
        assert all(row["status"] == "ready" and row["last_session_id"] is None for row in rows)
        assert rows[0]["hardware_reporting"] == "standard"
        assert rows[0]["canvas_readback"] == rows[0]["webgl_debug_info"] == "standard"
        if not legacy:
            assert rows[1]["hardware_reporting"] == "limited"
            assert rows[1]["canvas_readback"] == rows[1]["webgl_debug_info"] == "blocked"
            assert (rows[1]["engine"], rows[1]["local_fonts"]) == ("chromium", "restricted")
        sessions = c.execute("SELECT * FROM sessions WHERE id LIKE 'current%'").fetchall()
        assert len(sessions) == len(ids)
        assert all(row["pid"] is None and row["state"] == "stopped" and row["ended_at"] for row in sessions)
        assert tuple(c.execute("SELECT pid, state, ended_at FROM sessions WHERE id='old-completed'").fetchone()) == (
            456, "stopped", "then",
        )


@pytest.mark.parametrize("unsafe_id", ["..", "../other", r"sub\other", "C:escape"])
def test_invalid_id_cannot_replace_live_database(data_dir, tmp_path, unsafe_id):
    db.init_db()
    archive = fixture_archive(tmp_path / "source", ["ordinary", unsafe_id])
    with pytest.raises(ValueError, match="环境 ID"):
        backup.restore(archive)
    with db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
    assert not config.DB_PATH.with_suffix(".db.bak").exists()


def test_profile_symlink_cannot_redirect_restored_environment(data_dir, tmp_path):
    db.init_db()
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (config.PROFILES_DIR / "moved").symlink_to(outside, target_is_directory=True)
    archive = fixture_archive(tmp_path / "source", ["moved"])
    with pytest.raises(ValueError, match="环境目录链接"):
        backup.restore(archive)
    assert list(outside.iterdir()) == []
    assert not config.DB_PATH.with_suffix(".db.bak").exists()


def test_backup_restores_recycled_profile_into_new_data_root(data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    from moltskin.services.core import main
    from moltskin.services.core.security import get_token

    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as source:
        created = source.post("/api/v1/profiles", json={"name": "回收站迁移", "engine": "camoufox"})
        profile = created.raise_for_status().json()["data"]
        ident = profile["id"]
        storage = Path(profile["user_data_dir"]) / "storage"
        storage.mkdir()
        (storage / "marker").write_bytes(b"archived local storage")
        source.post(f"/api/v1/profiles/{ident}/trash").raise_for_status()
        archive = source.get("/api/v1/backup?include_profiles=true").raise_for_status().content

    target = data_dir / "new-install"
    monkeypatch.setattr(config, "DATA_DIR", target)
    for name in ("DB_PATH", "PROFILES_DIR", "RUN_DIR", "LOG_DIR", "SELFTEST_DIR", "TOKEN_PATH"):
        monkeypatch.setattr(config, name, target / getattr(config, name).name)
    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as dest:
        response = dest.post("/api/v1/backup/restore", files={"file": ("backup.zip", archive)})
        manifest = response.raise_for_status().json()["data"]["manifest"]
        assert manifest["profiles"] == []
        assert [p["id"] for p in manifest["trashed_profiles"]] == [ident]
        assert dest.get(f"/api/v1/profiles/{ident}").status_code == 404
        restored = dest.post(f"/api/v1/trash/profiles/{ident}/restore").raise_for_status().json()["data"]
        assert Path(restored["user_data_dir"]) == config.PROFILES_DIR / ident
        assert (config.PROFILES_DIR / ident / "storage" / "marker").read_bytes() == b"archived local storage"


def test_invalid_recycled_session_cannot_replace_live_database(data_dir, tmp_path):
    db.init_db()
    source_db = tmp_path / "source.db"
    with closing(sqlite3.connect(source_db)) as conn, conn:
        conn.executescript(db.SCHEMA)
        profile = {"id": "recycled", "name": "Saved", "engine": "camoufox", "status": "ready"}
        conn.execute(
            "INSERT INTO trashed_profiles (id,name,engine,trashed_at,profile_json,sessions_json,extensions_json) "
            "VALUES (?,?,?,?,?,?,?)",
            ("recycled", "Saved", "camoufox", db.now(), json.dumps(profile),
             json.dumps([{"profile_id": "recycled", "state": "running"}]), "[]"),
        )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(source_db, backup.DB_NAME)
    with pytest.raises(ValueError, match="回收站会话"):
        backup.restore(archive.getvalue())
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trashed_profiles").fetchone()[0] == 0
    assert not config.DB_PATH.with_suffix(".db.bak").exists()


def test_chromium_login_state_backup_excludes_caches_and_restores_across_machines(
    data_dir, monkeypatch
):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from moltskin.services.core import main
    from moltskin.services.core.security import get_token

    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as source:
        created = source.post(
            "/api/v1/profiles",
            json={"name": "Chromium Shop", "engine": "chromium"},
        ).raise_for_status().json()["data"]
        pid = created["id"]
        pdir = Path(created["user_data_dir"])

        # Import persistent login cookies via the unified Cookie API.
        source.put(
            f"/api/v1/profiles/{pid}/cookies",
            json={
                "mode": "replace",
                "cookies": [
                    {
                        "name": "session_token",
                        "value": "sec_998877",
                        "domain": ".shop.example",
                        "path": "/",
                        "expires": 1893456000,
                        "secure": True,
                        "httpOnly": True,
                        "sameSite": "lax",
                    }
                ],
            },
        ).raise_for_status()

        # Simulate Chromium LocalStorage, IndexedDB, Extension Storage, plus caches & locks.
        for rel, content in (
            ("Last Version", b"158.0.8127.88"),
            ("Local State", b'{"os_crypt":{}}'),
            ("SingletonLock", b"stale-lock"),
            ("DevToolsActivePort", b"9222\n/devtools/browser/abc"),
            ("Default/Preferences", b'{"profile":{"name":"Shop"}}'),
            ("Default/LocalStorage/leveldb/000003.log", b"localstorage-kv"),
            ("Default/LocalStorage/leveldb/LOCK", b""),
            ("Default/IndexedDB/https_shop.example_0.indexeddb.leveldb/CURRENT", b"MANIFEST-000001\n"),
            ("Default/Local Extension Settings/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/000005.ldb", b"wallet-vault"),
            ("Default/WebStorage/1/IndexedDB/blob", b"bucket-idb"),
            ("Default/WebStorage/1/CacheStorage/giant.bin", b"X" * 4096),
            ("Default/Cache/Cache_Data/f_000001", b"Y" * 4096),
            ("Default/Code Cache/js/index", b"Z" * 4096),
            ("Default/GPUCache/data_1", b"G" * 1024),
        ):
            fp = pdir / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(content)

        # Simulate foreign OS Keychain encrypted_value in SQLite that a new machine cannot decrypt:
        # hydrate_portable_chromium_cookies must materialize plaintext v24 rows on the new machine!
        with closing(sqlite3.connect(pdir / "Default" / "Network" / "Cookies")) as c, c:
            c.execute("UPDATE cookies SET value='', encrypted_value=X'763130deadbeef'")

        archive = source.get("/api/v1/backup?include_profiles=true").raise_for_status().content

    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["profile_data_excluded"] == []
    assert f"profiles/{pid}/Default/LocalStorage/leveldb/000003.log" in names
    assert f"profiles/{pid}/Default/IndexedDB/https_shop.example_0.indexeddb.leveldb/CURRENT" in names
    assert f"profiles/{pid}/Default/Local Extension Settings/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/000005.ldb" in names
    assert f"profiles/{pid}/Default/WebStorage/1/IndexedDB/blob" in names
    assert f"profiles/{pid}/Default/Network/Cookies" in names
    # Locks and caches must never enter the backup archive.
    assert not any("Cache" in n or "SingletonLock" in n or "DevToolsActivePort" in n or n.endswith("/LOCK") for n in names)

    # Restore onto a brand-new machine data root with its own newly generated proxy.key!
    new_root = data_dir / "machine-b"
    new_root.mkdir(parents=True)
    (new_root / "proxy.key").write_bytes(Fernet.generate_key())
    monkeypatch.setattr(config, "DATA_DIR", new_root)
    for attr in ("DB_PATH", "PROFILES_DIR", "RUN_DIR", "LOG_DIR", "SELFTEST_DIR", "TOKEN_PATH"):
        monkeypatch.setattr(config, attr, new_root / getattr(config, attr).name)

    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as dest:
        res = dest.post("/api/v1/backup/restore", files={"file": ("backup.zip", archive)}).raise_for_status().json()["data"]
        assert res["profiles"] == 1

        restored_dir = config.PROFILES_DIR / pid
        assert (restored_dir / "Default/LocalStorage/leveldb/000003.log").read_bytes() == b"localstorage-kv"
        assert (restored_dir / "Default/Local Extension Settings/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/000005.ldb").read_bytes() == b"wallet-vault"
        assert not (restored_dir / "Default/Cache").exists()

        # Cookies API on Machine B reads decrypted portable cookies cleanly, and SQLite has hydrated plaintext for Chromium!
        cookies_resp = dest.get(f"/api/v1/profiles/{pid}/cookies").raise_for_status().json()["data"]
        assert len(cookies_resp["cookies"]) == 1
        assert cookies_resp["cookies"][0]["name"] == "session_token"
        assert cookies_resp["cookies"][0]["value"] == "sec_998877"

        with closing(sqlite3.connect(restored_dir / "Default" / "Network" / "Cookies")) as c:
            row = c.execute("SELECT value, encrypted_value FROM cookies WHERE name='session_token'").fetchone()
        assert row == ("sec_998877", b"")

        # Verify Chromium state-size and sync-from between two Chromium profiles.
        clone = dest.post(f"/api/v1/profiles/{pid}/duplicate").raise_for_status().json()["data"]
        sync_res = dest.post(f"/api/v1/profiles/{clone['id']}/sync-from/{pid}?parts=cookies,storage").raise_for_status().json()["data"]
        assert sync_res["cookies"] == 1
        assert sync_res["storage"] is True
        assert dest.get(f"/api/v1/profiles/{clone['id']}/cookies").raise_for_status().json()["data"]["total"] == 1


@pytest.mark.skipif(
    __import__("os").getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_native_chromium_loads_hydrated_cookies_and_restored_storage_across_roots(data_dir, tmp_path):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import subprocess
    import threading
    from chromium.native import default_binary, probe_capabilities
    from moltskin.services.core import chromium, cookies as cookie_store

    probe_capabilities(default_binary())
    seen: list[dict] = []
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            cookie_hdr = self.headers.get("Cookie", "")
            if self.path == "/seed":
                body = (
                    b"<html><body><script>"
                    b"localStorage.setItem('ls_token', 'persist_ls_42');"
                    b"const req = indexedDB.open('auth_db', 1);"
                    b"req.onupgradeneeded = () => req.result.createObjectStore('kv');"
                    b"req.onsuccess = () => {"
                    b"  const tx = req.result.transaction('kv', 'readwrite');"
                    b"  tx.objectStore('kv').put('persist_idb_99', 'jwt');"
                    b"  tx.oncomplete = () => fetch('/report', {method:'POST', body:JSON.stringify({seeded:true})});"
                    b"};"
                    b"</script></body></html>"
                )
            else:
                body = (
                    b"<html><body><script>"
                    b"const ls = localStorage.getItem('ls_token');"
                    b"const req = indexedDB.open('auth_db', 1);"
                    b"req.onsuccess = () => {"
                    b"  const tx = req.result.transaction('kv', 'readonly');"
                    b"  const g = tx.objectStore('kv').get('jwt');"
                    b"  g.onsuccess = () => fetch('/report', {method:'POST', body:JSON.stringify({cookie: "
                    + json.dumps(cookie_hdr).encode("utf-8")
                    + b", ls, idb: g.result})});"
                    b"};"
                    b"</script></body></html>"
                )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0"))
            seen.append(json.loads(self.rfile.read(n).decode("utf-8")))
            self.send_response(204)
            self.end_headers()
            done.set()

        def log_message(self, *_a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    db.init_db()
    pid = "pf_migrate_live"
    pdir = config.profile_directory(pid)
    pdir.mkdir(mode=0o700, parents=True)
    with db.session() as conn:
        conn.execute(
            "INSERT INTO profiles(id,name,engine,user_data_dir,status,created_at,updated_at) "
            "VALUES (?,?,?,?, 'ready', ?, ?)",
            (pid, "Migrate Live", "chromium", str(pdir), db.now(), db.now()),
        )

    def run_browser(target_dir: Path, path: str):
        done.clear()
        cmd, _ = chromium.prepare_launch(target_dir, "standard", True)
        cmd.insert(1, "--use-mock-keychain")
        cmd[-1] = f"http://127.0.0.1:{port}{path}"
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            assert done.wait(timeout=20)
            return seen[-1]
        finally:
            __import__("platforms").current.send_stop(proc.pid, "chromium")
            proc.wait(timeout=10)

    try:
        assert run_browser(pdir, "/seed") == {"seeded": True}
        cookie_store.write_chromium_cookies(
            pdir,
            [
                {
                    "name": "auth_sid",
                    "value": "portable_cookie_777",
                    "domain": "127.0.0.1",
                    "path": "/",
                    "expires": 1893456000,
                    "secure": False,
                    "httpOnly": True,
                    "sameSite": "lax",
                }
            ],
            mode="replace",
        )
        archive_path = backup.create(include_profiles=True)
        archive_bytes = archive_path.read_bytes()
        __import__("shutil").rmtree(archive_path.parent, ignore_errors=True)

        # Restore into a new data directory and launch Chromium to verify Cookie + LocalStorage + IndexedDB!
        dest_root = tmp_path / "restored-root"
        dest_root.mkdir(parents=True)
        config.DATA_DIR = dest_root
        config.DB_PATH = dest_root / "moltskin.db"
        config.PROFILES_DIR = dest_root / "profiles"
        config.RUN_DIR = dest_root / "run"
        config.LOG_DIR = dest_root / "logs"
        config.SELFTEST_DIR = dest_root / "selftest"
        config.TOKEN_PATH = dest_root / "api.token"
        backup.restore(archive_bytes)

        verify = run_browser(config.profile_directory(pid), "/verify")
        assert "auth_sid=portable_cookie_777" in verify["cookie"]
        assert verify["ls"] == "persist_ls_42"
        assert verify["idb"] == "persist_idb_99"
    finally:
        srv.shutdown()
        srv.server_close()
