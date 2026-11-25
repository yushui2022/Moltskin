"""Render a session's first page and exercise browser-side IP lookup and retry."""

import os
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from chromium.native import default_binary, probe
from moltskin.services.core import db, main
from moltskin.services.core.security import get_token, workbench_signature


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium")
def test_window_workbench_reads_its_browser_and_recovers_from_ip_failure(data_dir):
    binary, version = probe(default_binary())
    with TestClient(main.app) as service, sync_playwright() as driver:
        sid = "ss_123456780abc"
        label = "QA & Development <West>"
        profile = service.post("/api/v1/profiles", json={"name": label, "engine": "chromium"},
                               headers={"Authorization": "Bearer " + get_token()}).raise_for_status().json()["data"]
        with db.session() as conn:
            conn.execute("UPDATE profiles SET last_session_id=? WHERE id=?", (sid, profile["id"]))
            conn.execute("INSERT INTO sessions(id,profile_id,state,started_at,browser_version) VALUES (?,?,?,?,?)",
                         (sid, profile["id"], "running", db.now(), version))
        browser = driver.chromium.launch(executable_path=str(binary), chromium_sandbox=True)
        try:
            page = browser.new_page(timezone_id="Europe/Paris", viewport={"width": 375, "height": 800})
            requests = []
            fallback_requests = []
            local_lookups = []

            def send(route):
                url = urlsplit(route.request.url)
                if url.hostname == "127.0.0.1" and url.path.endswith("/location"):
                    assert url.path == f"/workbench/{sid}/{workbench_signature(sid)}/location"
                    assert route.request.method == "POST" and "authorization" not in route.request.headers
                    local_lookups.append(route.request.post_data_json)
                    route.fulfill(json={"data": {"city": "Sydney", "country": "AU", "timezone": "Australia/Sydney"}})
                elif url.hostname == "127.0.0.1":
                    response = service.get(url.path).raise_for_status()
                    route.fulfill(status=response.status_code, body=response.content, headers=dict(response.headers))
                elif route.request.url == "https://ipinfo.io/json":
                    requests.append(route.request.headers)
                    if len(requests) != 2:
                        route.abort()
                    else:
                        route.fulfill(json={"ip": "2001:db8::123", "region": "Tokyo", "country": "JP",
                                            "timezone": "Asia/Tokyo", "postal": "100-0001", "loc": "35.6,139.7"},
                                      headers={"Access-Control-Allow-Origin": "*"})
                elif url.hostname == "api64.ipify.org":
                    fallback_requests.append(route.request.headers)
                    if len(requests) == 3:
                        route.fulfill(json={"ip": "1.1.1.1"}, headers={"Access-Control-Allow-Origin": "*"})
                    else:
                        route.abort()
                else:
                    route.abort()

            page.route("**/*", send)
            page.goto(f"http://127.0.0.1/workbench/{sid}/{workbench_signature(sid)}")
            expect(page.locator("#windowName")).to_have_text(label)
            expect(page.locator("#kernel")).to_contain_text(version)
            expect(page.locator("#ua")).to_have_text(page.evaluate("navigator.userAgent"))
            expect(page.locator("#browserTimezone")).to_have_text("Europe/Paris")
            expect(page.locator("#networkStatus")).to_contain_text("失败")
            page.locator("#retryIp").click()
            expect(page.locator("#ip")).to_have_text("2001:db8::123")
            expect(page.locator("#ipRegion")).to_contain_text("Tokyo")
            expect(page.locator("#ipTimezone")).to_have_text("Asia/Tokyo")
            expect(page.locator("#ipPostal")).to_have_text("100-0001")
            expect(page.locator("#ipClock")).not_to_have_text("—")
            assert len(requests) == 2
            page.reload()
            expect(page.locator("#ip")).to_have_text("1.1.1.1")
            expect(page.locator("#ipRegion")).to_contain_text("Sydney")
            expect(page.locator("#ipTimezone")).to_have_text("Australia/Sydney")
            expect(page.locator("#browserTimezone")).to_have_text("Europe/Paris")
            assert len(fallback_requests) == 2 and local_lookups == [{"ip": "1.1.1.1"}]
            assert all("referer" not in headers and "authorization" not in headers for headers in fallback_requests)
            assert all("referer" not in headers and "authorization" not in headers for headers in requests)
            assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
        finally:
            browser.close()
