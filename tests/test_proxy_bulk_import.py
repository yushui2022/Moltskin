"""Bulk proxy imports validate rows, preserve credentials, and serialize duplicates."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from moltskin.services.core import db, main
from moltskin.services.core.secret_store import decrypt, is_encrypted
from moltskin.services.core.security import get_token


URL = "/api/v1/proxies/bulk"


@pytest.fixture
def client(data_dir):
    with TestClient(main.app) as api:
        api.headers["Authorization"] = f"Bearer {get_token()}"
        yield api


def test_bulk_rows_preserve_source_lines_and_canonicalize_legacy_hosts(client):
    client.post("/api/v1/proxies", json={"name": "legacy", "host": "Proxy.TeSt.", "port": 8080}).raise_for_status()
    rows = [
        {"host": "proxy.test", "protocol": "HTTP", "port": "8080", "line": 3},
        {"host": "[2001:0DB8::0010]", "port": 443, "protocol": "https", "line": 4,
         "username": "operator", "password": "test@secret"},
        {"host": "2001:db8:0:0::10", "port": 443, "protocol": "https", "line": 7},
        {"host": "2001:db8::10", "port": 443, "protocol": "socks5", "line": 9},
        {"host": "192.0.2.12", "port": 1080, "name": "staging"},
        {"host": "New.TeSt.", "port": 8090, "line": 11},
    ]
    response = client.post(URL, json={"proxies": rows}).raise_for_status()
    assert "test@secret" not in response.text and "operator" not in response.text
    data = response.json()["data"]
    assert data["created"] == 4
    assert [(r["line"], r["name"]) for r in data["items"]] == [
        (4, "[2001:db8::10]:443"), (9, "[2001:db8::10]:443"), (5, "staging"), (11, "new.test:8090"),
    ]
    assert [(r["line"], r["status"]) for r in data["skipped"]] == [(3, "duplicate"), (7, "duplicate")]
    with db.session() as conn:
        record = conn.execute("SELECT host,username,password FROM proxies WHERE id=?", (data["items"][0]["id"],)).fetchone()
        assert (record["host"], record["username"]) == ("[2001:db8::10]", "operator")
        assert is_encrypted(record["password"]) and "test@secret" not in record["password"]
        assert decrypt(record["password"]) == "test@secret"
        assert conn.execute("SELECT COUNT(*) FROM proxies").fetchone()[0] == 5
        assert "test@secret" not in str(conn.execute("SELECT group_concat(detail) FROM logs").fetchone()[0])


def test_invalid_rows_skip_safely_without_losing_valid_rows(client):
    rows = [None, "private_value", [], {"host": "private_value@proxy.test", "port": 80}, {
        "host": "example.test", "port": 1, "username": "user", "password": {"private_value": True},
    }]
    for host in ("1.2.3", "001.2.3.4", "0x7f000001", "bad..test", "-test", "proxy.test/path",
                 "[not-an-ip]", "[fe80::1%en0]", "user:private_value@host", "[::1", "中文.test"):
        rows.append({"host": host, "port": 80})
    for port in (0, 65536, [], True, 1.5, "-1", "Infinity"):
        rows.append({"host": "good.test", "port": port})
    for fields in ({"protocol": []}, {"name": False}, {"password": "private_value"}, {"username": "x\ny"},
                   {"protocol": "https", "username": "bad:user", "password": "private_value"}):
        rows.append({"host": "good.test", "port": 80, **fields})
    rows.append({"host": "good.test", "port": 80})
    response = client.post(URL, json={"proxies": rows}).raise_for_status()
    data = response.json()["data"]
    assert data["created"] == 1
    assert [(r["line"], r["status"]) for r in data["skipped"]] == [(i, "invalid") for i in range(1, len(rows))]
    assert "private_value" not in response.text


def test_invalid_envelope_and_oversized_batch_are_rejected_before_any_writes(client):
    for payload in ("private_value", {"proxies": "private_value"}, {"proxies": []}):
        response = client.post(URL, json=payload)
        assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_PAYLOAD"
        assert "private_value" not in response.text
    rows = [{"host": "localhost", "port": i + 1} for i in range(501)]
    failure = client.post(URL, json={"proxies": rows})
    assert failure.status_code == 422 and failure.json()["error"]["code"] == "PROXY_BATCH_LIMIT"
    assert client.get("/api/v1/proxies").json()["data"] == []
    assert client.post(URL, json={"proxies": rows[:500]}).raise_for_status().json()["data"]["created"] == 500


def test_simultaneous_bulk_imports_create_only_one_endpoint(client):
    start = Barrier(4)

    def submit(i):
        start.wait(timeout=10)
        response = client.post(URL, json={"proxies": [
            {"host": "Proxy.Test." if i % 2 else "proxy.test", "port": 443, "username": f"user{i}"}
        ]})
        return response.raise_for_status().json()["data"]

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(submit, range(4)))
    assert sum(r["created"] for r in results) == 1
    assert [s["status"] for r in results for s in r["skipped"]] == ["duplicate"] * 3
    assert len(client.get("/api/v1/proxies").json()["data"]) == 1
