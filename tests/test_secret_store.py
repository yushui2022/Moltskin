"""Proxy credentials are encrypted at rest and never returned by the API."""

import os
import stat

from fastapi.testclient import TestClient

from moltskin.services.core import config, db, main, proxy_checker
from moltskin.services.core.security import get_token
from moltskin.services.core.secret_store import decrypt, encrypt, is_encrypted, key_path


def test_encrypt_decrypt_and_key_permissions(data_dir):
    db.init_db()
    token = encrypt("s3cret")
    assert is_encrypted(token)
    assert decrypt(token) == "s3cret"
    assert stat.S_IMODE(os.stat(key_path()).st_mode) == 0o600


def test_init_db_encrypts_legacy_plaintext_password(data_dir):
    db.init_db()
    with db.session() as conn:
        conn.execute(
            "INSERT INTO proxies(id,name,protocol,host,port,username,password,status,created_at,updated_at) "
            "VALUES ('px_legacy','legacy','http','proxy.test',8080,'user','plain',"
            "'unchecked',?,?)",
            (db.now(), db.now()),
        )
    db.init_db()
    with db.session() as conn:
        password = conn.execute("SELECT password FROM proxies WHERE id='px_legacy'").fetchone()[0]
    assert is_encrypted(password)
    assert decrypt(password) == "plain"


def test_checker_receives_decrypted_password(data_dir, monkeypatch):
    db.init_db()
    captured = {}

    class FakeProxy:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        def as_string(self):
            return "http://proxy.test:8080"

    monkeypatch.setattr(proxy_checker, "Proxy", FakeProxy)
    monkeypatch.setattr(proxy_checker, "proxy_exit_geo", lambda value: (_ for _ in ()).throw(ValueError("offline")))
    proxy_checker.check_proxy("http", "proxy.test", 8080, "user", encrypt("secret"))
    assert captured["password"] == "secret"


def test_api_does_not_return_password(data_dir):
    db.init_db()
    headers = {"Authorization": f"Bearer {get_token()}"}
    with TestClient(main.app, headers=headers) as client:
        created = client.post("/api/v1/proxies", json={
            "name": "residential", "host": "proxy.test", "port": 8080,
            "username": "user", "password": "secret",
        })
        assert created.status_code == 201
        assert "secret" not in created.text
        listed = client.get("/api/v1/proxies").json()["data"]
        assert listed[0]["has_password"] is True
        assert "secret" not in str(listed)
    with db.session() as conn:
        password = conn.execute("SELECT password FROM proxies").fetchone()[0]
    assert is_encrypted(password) and "secret" not in password


def test_backup_moves_proxy_key_and_reencrypts(data_dir, monkeypatch):
    from moltskin.services.core import main
    from moltskin.services.core.security import get_token

    db.init_db()
    headers = {"Authorization": f"Bearer {get_token()}"}
    with TestClient(main.app, headers=headers) as source:
        source.post("/api/v1/proxies", json={
            "name": "backup", "host": "proxy.test", "port": 8080,
            "username": "user", "password": "backup-secret",
        }).raise_for_status()
        archive = source.get("/api/v1/backup").raise_for_status().content

    target = data_dir / "other-install"
    monkeypatch.setattr(config, "DATA_DIR", target)
    for name in ("DB_PATH", "PROFILES_DIR", "RUN_DIR", "LOG_DIR", "SELFTEST_DIR", "TOKEN_PATH"):
        monkeypatch.setattr(config, name, target / getattr(config, name).name)
    with TestClient(main.app, headers={"Authorization": f"Bearer {get_token()}"}) as dest:
        dest.post("/api/v1/backup/restore", files={"file": ("backup.zip", archive)}).raise_for_status()
    with db.session() as conn:
        password = conn.execute("SELECT password FROM proxies").fetchone()[0]
    assert is_encrypted(password)
    assert decrypt(password) == "backup-secret"
