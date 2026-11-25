"""The local report must describe the window viewing it, without reading saved profile data."""

import os

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from chromium.native import default_binary, probe
from moltskin.services.core.main import app
from moltskin.services.core.security import get_token


def test_report_is_separate_from_manager_credentials(data_dir):
    with TestClient(app) as client:
        response = client.get("/runtime.html", headers={"User-Agent": "Moltskin-QA/1.0 (A & B <preview>)"}).raise_for_status()
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert get_token() not in response.text
        assert "__MOLTSKIN_TOKEN__" not in response.text
        assert response.headers["content-type"].startswith("text/html")
        assert '<code id="httpUa">Moltskin-QA/1.0 (A &amp; B &lt;preview&gt;)</code>' in response.text
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium")
def test_report_reads_the_current_window(data_dir):
    binary, version = probe(default_binary())
    with TestClient(app) as client, sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=str(binary), chromium_sandbox=True)
        try:
            page = browser.new_page(locale="ja-JP", timezone_id="Asia/Tokyo", viewport={"width": 960, "height": 720})
            def serve_local(route):
                path = route.request.url.removeprefix("http://127.0.0.1")
                assert path in ("/runtime.html", "/logo.svg", "/favicon-32.png")
                response = client.get(path, headers=route.request.headers).raise_for_status()
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

            page.route("**/*", serve_local)
            page.goto("http://127.0.0.1/runtime.html")
            expect(page.locator("#version")).to_have_text(version)
            expect(page.locator("#language")).to_contain_text("ja-JP")
            expect(page.locator("#timezone")).to_have_text("Asia/Tokyo")
            expect(page.locator("#ua")).to_have_text(page.evaluate("navigator.userAgent"))
            expect(page.locator("#httpUa")).to_have_text(page.evaluate("navigator.userAgent"))
            # Keep the user's system clipboard out of the test.
            page.evaluate("navigator.clipboard.writeText = async text => { window.copiedUA = text }")
            page.get_by_role("button", name="复制 UA").click()
            expect(page.get_by_role("status")).to_have_text("已复制")
            assert page.evaluate("window.copiedUA") == page.locator("#ua").text_content()
            expect(page.locator("#cpu")).to_have_text(str(page.evaluate("navigator.hardwareConcurrency")))
            expect(page.locator("#memory")).to_have_text(page.evaluate(
                "navigator.deviceMemory ? navigator.deviceMemory + ' GiB' : '浏览器未提供'"
            ))
            expect(page.locator("#inner")).to_have_text("960 × 720 CSS px")
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.locator("#inner")).to_have_text("390 × 844 CSS px")
            assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
        finally:
            browser.close()
