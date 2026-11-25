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
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

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


def test_chromium_permission_controls_create_edit_and_batch(panel):
    panel.locator("#windowEmptyCreate").click()
    panel.locator("#fName").fill("Media A")
    panel.locator('button[aria-controls="profileBrowser"]').click()
    field = panel.locator("#fMediaCapture")
    location = panel.locator("#fGeolocation")
    expect(location).to_be_hidden()
    expect(field).to_be_hidden()
    panel.locator("#fEngine").select_option("chromium")
    expect(field).to_have_value("standard")
    field.select_option("blocked")
    expect(location).to_have_value("standard")
    location.select_option("blocked")
    panel.locator("#confirmProfile").click()

    row = panel.locator("#rows tr").filter(has_text="Media A")
    row.get_by_role("button", name="Media A").click()
    expect(panel.locator("#fingerprintPreview .kv").filter(has_text="摄像头和麦克风")).to_contain_text("始终阻止")
    panel.keyboard.press("Escape")
    row.get_by_label("更多操作").click()
    panel.locator('[data-cmd="edit"]').click()
    panel.locator('button[aria-controls="profileBrowser"]').click()
    expect(field).to_have_value("blocked")
    expect(location).to_have_value("blocked")
    field.select_option("standard")
    location.select_option("standard")
    with panel.expect_response(lambda r: r.request.method == "PATCH" and "/profiles/" in r.url) as updated:
        panel.locator("#confirmProfile").click()
    assert updated.value.json()["data"]["geolocation"] == "standard"
    row.get_by_role("button", name="Media A").click()
    expect(panel.locator("#fingerprintPreview .kv").filter(has_text="地理位置")).to_contain_text("由网站请求授权")
    panel.keyboard.press("Escape")

    panel.locator("#createMenuBtn").click()
    panel.locator('[data-create="batch"]').click()
    panel.locator("#bPrefix").fill("Media batch")
    panel.locator("#bCount").fill("2")
    panel.locator("#bEngine").select_option("chromium")
    panel.locator("#bMediaCapture").select_option("blocked")
    panel.locator("#bGeolocation").select_option("blocked")
    panel.locator("#confirmBatch").click()
    for name in ("Media batch-1", "Media batch-2"):
        panel.get_by_role("button", name=name, exact=True).click()
        expect(panel.locator("#fingerprintPreview .kv").filter(has_text="摄像头和麦克风")).to_contain_text("始终阻止")
        expect(panel.locator("#fingerprintPreview .kv").filter(has_text="地理位置")).to_contain_text("始终阻止")
        panel.keyboard.press("Escape")


def test_hubstudio_file_import_targets_selected_camoufox_window(panel):
    panel.locator("#windowEmptyCreate").click()
    panel.locator("#fName").fill("运营环境甲")
    panel.locator("#confirmProfile").click()
    panel.get_by_role("button", name="运营环境甲", exact=True).click()
    panel.locator("#cookieImportBtn").click()
    expect(panel.locator("#cookieTarget")).to_contain_text("运营环境甲")
    panel.locator("#cookieFile").set_input_files({
        "name": "environment-A.txt", "mimeType": "text/plain",
        "buffer": b'[{"Name":"sid","Value":"fixture-a","Domain":".example.test","Path":"/"}]',
    })
    panel.locator("#cookieMode").select_option("replace")
    panel.locator("#cookieImportOk").click()
    expect(panel.locator("#cookieMask")).not_to_be_visible()
    expect(panel.locator("#cookieSummary")).to_contain_text("共 1 条")

    panel.locator("#cookieImportBtn").click()
    panel.locator("#cookieMode").select_option("replace")
    panel.locator("#cookieText").fill("[{")
    with panel.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/cookies")) as response:
        panel.locator("#cookieImportOk").click()
    assert response.value.status == 422
    expect(panel.locator("#cookieMask")).to_be_visible()
    panel.keyboard.press("Escape")
    panel.locator("#cookieRefresh").click()
    expect(panel.locator("#cookieSummary")).to_contain_text("共 1 条")
    with panel.expect_response(lambda r: "/cookies/export?" in r.url) as export:
        with panel.expect_download() as download:
            panel.locator("#cookieExportJson").click()
    assert export.value.status == 200
    assert "filename*=UTF-8''" in export.value.headers["content-disposition"]
    assert download.value.suggested_filename == "cookies-运营环境甲.json"


def test_netscape_export_explains_scope_error_without_downloading(panel):
    import sqlite3
    from contextlib import closing
    from moltskin.services.core import config, cookies

    panel.locator("#windowEmptyCreate").click()
    panel.locator("#fName").fill("Scoped cookie export")
    with panel.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/profiles")) as created:
        panel.locator("#confirmProfile").click()
    profile_id = created.value.json()["data"]["id"]
    database = cookies.cookies_path(config.profile_directory(profile_id))
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.executescript(cookies.SCHEMA)
        conn.execute(
            "INSERT INTO moz_cookies(name,host,path,value,originAttributes) "
            "VALUES ('scoped','example.test','/','fixture','^userContextId=1')"
        )
    panel.get_by_role("button", name="Scoped cookie export", exact=True).click()
    downloads = []
    panel.on("download", lambda download: downloads.append(download))
    with panel.expect_response(lambda r: r.url.endswith("/cookies/export?format=netscape")) as response:
        panel.locator("#cookieExportNetscape").click()
    assert response.value.status == 422
    expect(panel.locator("#toast")).to_contain_text(response.value.json()["error"]["message"])
    assert not downloads
