"""Signed window lookups read only the installed IP database, including partial city records."""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from moltskin.services.core import db, main, workbench_location
from moltskin.services.core.security import workbench_signature


@pytest.fixture
def signed_session(data_dir, monkeypatch):
    monkeypatch.setattr(workbench_location, "user_cache_dir", lambda name: data_dir / name)
    sid = "ss_123456789abc"
    with TestClient(main.app) as api:
        with db.session() as conn:
            conn.execute(
                "INSERT INTO profiles(id,name,engine,user_data_dir,created_at,updated_at,last_session_id) "
                "VALUES ('pf_test','test','chromium',?,?,?,?)", (str(data_dir / "test"), db.now(), db.now(), sid),
            )
            conn.execute(
                "INSERT INTO sessions(id,profile_id,state,started_at) VALUES (?,'pf_test','running',?)", (sid, db.now()),
            )
        yield api, f"/workbench/{sid}/{workbench_signature(sid)}/location"


def test_session_location_reads_only_matching_installed_database(signed_session, data_dir, monkeypatch):
    import maxminddb

    api, endpoint = signed_session
    directory = data_dir / "camoufox" / "geoip" / "mmdb"
    directory.mkdir(parents=True)
    path = directory / "geoip aio by daijro-combined.mmdb"
    assert api.post(endpoint, json={"ip": "8.8.8.8"}).json() == {"data": {}}
    path.write_bytes(b"damaged database")
    assert api.post(endpoint, json={"ip": "8.8.8.8"}).json() == {"data": {}}
    # AIO may supply just country and coordinates; other city datasets include names and postcodes.
    records = {
        "8.8.8.8": {"country": {"iso_code": "US"}, "location": {"latitude": 37.38605, "longitude": -122.08385,
                                                                   "time_zone": "America/Los_Angeles"}},
        "2606:4700:4700::1111": {"country": {"iso_code": "AU"}, "city": {"names": {"en": "Sydney", "zh-CN": "悉尼"}},
                                 "postal": {"code": "2000"}, "subdivisions": [{"names": {"en": "New South Wales"}}]},
        "1.1.1.1": {"autonomous_system_number": 13335},
    }
    opened = []

    @contextmanager
    def read_local(filename):
        assert filename == str(path)
        opened.append(filename)
        yield records

    monkeypatch.setattr(maxminddb, "open_database", read_local)
    result = api.post(endpoint, json={"ip": "8.8.8.8"}).raise_for_status()
    assert result.headers["cache-control"] == "no-store"
    assert result.json() == {"data": {"country": "US", "loc": "37.38605,-122.08385", "timezone": "America/Los_Angeles"}}
    assert api.post(endpoint, json={"ip": "2606:4700:4700:0:0:0:0:1111"}).json() == {
        "data": {"city": "悉尼", "country": "AU", "region": "New South Wales", "postal": "2000"},
    }
    assert api.post(endpoint, json={"ip": "1.1.1.1"}).json() == {"data": {}}

    invalid = ("192.168.1.1", "127.0.0.1", "100.64.1.1", "169.254.1.1", "224.0.0.1", "::1", "fe80::1", "fec0::7",
               "ff05::1", "::ffff:127.0.0.1", "2606:4700:4700::1111%en0", "8.8.8.8:80", "localhost", "https://1.1.1.1")
    for value in invalid:
        assert api.post(endpoint, json={"ip": value}).status_code == 422
    assert api.post(endpoint, json={"ip": 134744072}).status_code == 422
    assert api.post(endpoint, json={"ip": "8.8.8.8", "path": "/untrusted"}).status_code == 422
    tampered = endpoint.replace("ss_123456789abc", "ss_123456789def")
    assert api.post(tampered, json={"ip": "8.8.8.8"}).status_code == 404
    with db.session() as conn:
        conn.execute("UPDATE sessions SET state='stopped'")
    assert api.post(endpoint, json={"ip": "8.8.8.8"}).status_code == 404
    assert len(opened) == 3


def test_flat_ipv4_record_with_no_ipv6_database(signed_session, data_dir, monkeypatch):
    import maxminddb

    api, endpoint = signed_session
    directory = data_dir / "camoufox" / "geoip" / "mmdb"
    directory.mkdir(parents=True)
    path = directory / "maxmind geolite2-ipv4.mmdb"
    path.touch()

    @contextmanager
    def read_local(filename):
        assert filename == str(path)
        yield {"1.1.1.1": {"country_code": "AU", "city_name": "Sydney", "state1_name": "New South Wales",
                            "latitude": 0, "longitude": 0, "timezone": "Australia/Sydney"}}

    monkeypatch.setattr(maxminddb, "open_database", read_local)
    assert api.post(endpoint, json={"ip": "2606:4700:4700::1111"}).json() == {"data": {}}
    assert api.post(endpoint, json={"ip": "1.1.1.1"}).json() == {
        "data": {"country": "AU", "city": "Sydney", "region": "New South Wales", "loc": "0,0", "timezone": "Australia/Sydney"},
    }
