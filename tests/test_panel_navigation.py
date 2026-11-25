"""Keyboard navigation and responsive view preferences in the installed Chromium."""

import os
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from chromium.native import default_binary, probe


@pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium build")
def test_nested_dialog_keyboard_and_mobile_navigation(data_dir):
    from moltskin.services.core.main import app

    with TestClient(app) as server, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(probe(default_binary())[0]))
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        def serve(route):
            request = route.request
            url = urlsplit(request.url)
            target = url.path + ("?" + url.query if url.query else "")
            response = server.request(request.method, target, headers=request.headers, content=request.post_data_buffer)
            route.fulfill(status=response.status_code, content_type=response.headers.get("content-type"), body=response.content)

        page.route("http://panel.test/**", serve)
        page.goto("http://panel.test/#/windows")
        trigger = page.locator("#windowEmptyCreate")
        trigger.press("Enter")
        name = page.locator("#fName")
        expect(name).to_be_focused()
        name.fill("Editor draft")
        new_group = page.locator("#newGroupInline")
        new_group.press("Enter")
        expect(page.locator("#promptInput")).to_be_focused()
        page.keyboard.press("Escape")
        expect(new_group).to_be_focused()
        expect(name).to_have_value("Editor draft")
        page.keyboard.press("Escape")
        expect(trigger).to_be_focused()

        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator("#viewGrid")).to_have_attribute("aria-pressed", "true")
        menu = page.locator("#mobileNavBtn")
        menu.press("Enter")
        expect(page.locator("#mobileNavDrawer")).to_be_visible()
        page.keyboard.press("Escape")
        expect(menu).to_be_focused()
        page.set_viewport_size({"width": 1280, "height": 800})
        expect(page.locator("#viewTable")).to_have_attribute("aria-pressed", "true")
        browser.close()
