"""Local dashboard workflows exercised in the installed Chromium."""

import os
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from chromium.native import default_binary, probe


pytestmark = pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium build")


@pytest.fixture
def panel(data_dir):
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
        yield page
        browser.close()


def test_nested_dialog_keyboard_and_mobile_navigation(panel):
    page = panel
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


def test_media_capture_dashboard_create_edit_and_batch(panel):
    panel.locator("#windowEmptyCreate").click()
    panel.locator("#fName").fill("Media A")
    panel.locator('button[aria-controls="profileBrowser"]').click()
    field = panel.locator("#fMediaCapture")
    expect(field).to_be_hidden()
    panel.locator("#fEngine").select_option("chromium")
    expect(field).to_have_value("standard")
    field.select_option("blocked")
    panel.locator("#confirmProfile").click()

    row = panel.locator("#rows tr").filter(has_text="Media A")
    row.get_by_role("button", name="Media A").click()
    expect(panel.locator("#fingerprintPreview")).to_contain_text("始终阻止")
    panel.keyboard.press("Escape")
    row.get_by_label("更多操作").click()
    panel.locator('[data-cmd="edit"]').click()
    panel.locator('button[aria-controls="profileBrowser"]').click()
    expect(field).to_have_value("blocked")
    field.select_option("standard")
    panel.locator("#confirmProfile").click()
    row.get_by_role("button", name="Media A").click()
    expect(panel.locator("#fingerprintPreview")).to_contain_text("由网站请求授权")
    panel.keyboard.press("Escape")

    panel.locator("#createMenuBtn").click()
    panel.locator('[data-create="batch"]').click()
    panel.locator("#bPrefix").fill("Media batch")
    panel.locator("#bCount").fill("2")
    panel.locator("#bEngine").select_option("chromium")
    panel.locator("#bMediaCapture").select_option("blocked")
    panel.locator("#confirmBatch").click()
    for name in ("Media batch-1", "Media batch-2"):
        panel.get_by_role("button", name=name, exact=True).click()
        expect(panel.locator("#fingerprintPreview")).to_contain_text("始终阻止")
        panel.keyboard.press("Escape")
