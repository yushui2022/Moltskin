"""Local dashboard workflows exercised in the installed Chromium."""

import os
import re
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


def test_proxy_import_formats_credentials_and_physical_lines(panel):
    lines = [
        "\ufeff# proxies", "", "socks5://user%3Aone:p%40ss%2Fword@[2001:0db8::1]:1080",
        "SOCKS5,[2001:db8::1],1080,,", "host,port,username,password",
        'example.test.,80,fixture,"comma, double""quote"', "EXAMPLE.test:80:other:private",
        "127.0.0.2:3128:name:with:colons", "https;localhost;443;;", "localhost 8888 name pass",
        "protocol,host,port,username,password", "https,localhost,443,,", "192.0.2.3:1080",
        "[2001:db8::1],8080,name,pass",
    ]
    existing = [{"protocol": "http", "host": "192.0.2.3", "port": 1080}]
    rows = panel.evaluate("([text, saved]) => parseProxyImport(text, saved)", ["\r\n".join(lines), existing])
    assert [(row["line"], row["status"]) for row in rows] == [
        (3, "valid"), (4, "duplicate"), (6, "valid"), (7, "duplicate"), (8, "valid"),
        (9, "valid"), (10, "valid"), (12, "duplicate"), (13, "duplicate"), (14, "valid"),
    ]
    assert rows[0]["item"] == {"protocol": "socks5", "host": "[2001:db8::1]", "port": 1080,
                               "username": "user:one", "password": "p@ss/word"}
    assert rows[2]["item"]["password"] == 'comma, double"quote'
    assert rows[4]["item"]["password"] == "with:colons"
    assert rows[-2]["reason"] != rows[1]["reason"]
    assert all("password" not in row["item"] and "username" not in row["item"]
               for row in rows if row["status"] == "duplicate")

    invalid = [
        "192.0.2.256:80", "127.1:80", "0177.0.0.1:80", "0x7f.0.0.1:80", "[2001::x]:80",
        "http://name:fixture-secret@host:65536", "https://u%ZZ:fixture-secret@host:443",
        "http://u%3Aone:fixture-secret@host:80", "http://u:%0a@host:80",
        "https://user:fixture-secret@host", "https://host:80/path", "invalid@host:8080",
        "ftp://name:fixture-secret@host:21", 'host,80,user,"unterminated', "host,80,user,pass,extra",
        "example.test,8080,,fixture-secret",
    ]
    issues = panel.evaluate("text => parseProxyImport(text)", "\n".join(invalid))
    assert len(issues) == len(invalid)
    assert all(row["status"] == "invalid" and "item" not in row and "fixture-secret" not in row["reason"]
               for row in issues)


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


def test_proxy_import_txt_and_csv_previews_and_imports_valid_rows(panel, monkeypatch):
    from moltskin.services.core import db, proxy_checker

    monkeypatch.setattr(proxy_checker, "check_and_save", lambda _: pytest.fail("Importing must not contact proxies"))
    panel.locator('a[href="#/proxies"]').click()
    start = panel.locator("#bulkProxyBtn")
    file = panel.locator("#bulkProxyFile")
    preview = panel.locator("#bulkProxyPreview")
    confirm = panel.locator("#confirmBulkProxy")
    rows = panel.locator("#proxyRows tr")
    dialog = panel.locator("#bulkProxyMask")

    start.click()
    expect(file).to_be_attached()
    with panel.expect_file_chooser() as picker:
        dialog.get_by_role("button", name="选择文件").click()
    picker.value.set_files({
        "name": "endpoints.txt", "mimeType": "text/plain",
        "buffer": b"socks5://fixture-user:fixture-secret@127.0.0.1:18080\r\n"
                  b"socks5://127.0.0.1:18080\r\ninvalid-endpoint",
    })
    for summary in ("可导入 1 条", "重复 1 条", "无效 1 条"):
        expect(preview).to_contain_text(summary)
    expect(dialog).to_contain_text(re.compile(r"第\s*3\s*行"))
    expect(preview).not_to_contain_text("fixture-user")
    expect(preview).not_to_contain_text("fixture-secret")
    with panel.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/proxies/bulk")) as imported:
        confirm.click()
    assert imported.value.json()["data"]["created"] == 1
    expect(dialog).to_be_visible()
    expect(panel.locator("#bulkProxyResult")).to_contain_text("已导入 1 条代理，跳过 2 条")
    expect(confirm).to_be_disabled()
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("socks5://127.0.0.1:18080")

    panel.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    start.click()
    file.set_input_files({
        "name": "endpoints.csv", "mimeType": "text/csv",
        "buffer": b"protocol,host,port,username,password\r\n"
                  b"socks5,127.0.0.1,18080,,\r\n"
                  b'https,127.0.0.1,18443,another-user,"pass,with,comma"\r\n',
    })
    for summary in ("可导入 1 条", "重复 1 条", "无效 0 条"):
        expect(preview).to_contain_text(summary)
    expect(preview).not_to_contain_text("another-user")
    expect(preview).not_to_contain_text("pass,with,comma")
    with panel.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/proxies/bulk")) as imported:
        confirm.click()
    assert imported.value.json()["data"]["created"] == 1
    expect(dialog).not_to_be_visible()
    expect(rows).to_have_count(2)
    with db.session() as conn:
        saved = conn.execute("SELECT protocol, host, port, username, password FROM proxies ORDER BY port").fetchall()
    assert [tuple(row) for row in saved] == [
        ("socks5", "127.0.0.1", 18080, "fixture-user", "fixture-secret"),
        ("https", "127.0.0.1", 18443, "another-user", "pass,with,comma"),
    ]


def test_proxy_import_cancel_resets_file_text_and_preview(panel):
    panel.locator('a[href="#/proxies"]').click()
    start = panel.locator("#bulkProxyBtn")
    file = panel.locator("#bulkProxyFile")
    text = panel.locator("#bulkProxyText")
    preview = panel.locator("#bulkProxyPreview")
    confirm = panel.locator("#confirmBulkProxy")
    start.click()
    expect(file).to_be_attached()
    file.set_input_files({"name": "cancelled.txt", "mimeType": "text/plain", "buffer": b"127.0.0.1:18111"})
    expect(preview).to_contain_text("可导入 1 条")
    expect(confirm).to_be_enabled()
    panel.locator("#bulkProxyMask").get_by_role("button", name="取消").click()
    start.click()
    expect(file).to_have_value("")
    expect(text).to_have_value("")
    expect(preview).not_to_contain_text("18111")
    expect(confirm).to_be_disabled()
    text.fill("only-a-host")
    expect(panel.locator("#bulkProxyMask")).to_contain_text(re.compile(r"第\s*1\s*行"))
    expect(confirm).to_be_disabled()
    expect(panel.locator("#proxyRows tr")).to_have_count(0)


def test_proxy_import_corrects_only_the_selected_line_after_partial_import(panel):
    from moltskin.services.core import db

    panel.locator('a[href="#/proxies"]').click()
    panel.locator("#bulkProxyBtn").click()
    dialog = panel.locator("#bulkProxyMask")
    text = panel.locator("#bulkProxyText")
    confirm = panel.locator("#confirmBulkProxy")
    initial = "# 🦎 本地测试\n\n127.0.0.1:18111\nmissing-port\n# end"
    text.fill(initial)
    confirm.click()
    expect(panel.locator("#bulkProxyResult")).to_contain_text("已导入 1 条代理，跳过 1 条")
    expect(confirm).to_be_disabled()

    edit = dialog.get_by_role("button", name="修改第 4 行", exact=True)
    expect(edit).to_be_visible()
    edit.click()
    expect(text).to_be_focused()
    assert text.evaluate("el => el.value.slice(el.selectionStart, el.selectionEnd)") == "missing-port"
    panel.keyboard.insert_text("127.0.0.1:18112")
    expect(text).to_have_value(initial.replace("missing-port", "127.0.0.1:18112"))
    expect(panel.locator("#bulkProxyCounts")).to_contain_text("可导入 1 条")
    with panel.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/proxies/bulk")) as corrected:
        confirm.click()
    assert [item["port"] for item in corrected.value.request.post_data_json["proxies"]] == [18112]
    assert corrected.value.json()["data"]["created"] == 1
    expect(dialog).not_to_be_visible()
    with db.session() as conn:
        saved = conn.execute("SELECT host, port FROM proxies ORDER BY port").fetchall()
    assert [tuple(row) for row in saved] == [("127.0.0.1", 18111), ("127.0.0.1", 18112)]


def test_proxy_import_limits_and_unreadable_files_preserve_draft(panel):
    from moltskin.services.core import db

    panel.locator('a[href="#/proxies"]').click()
    panel.locator("#bulkProxyBtn").click()
    text = panel.locator("#bulkProxyText")
    file = panel.locator("#bulkProxyFile")
    confirm = panel.locator("#confirmBulkProxy")
    feedback = panel.locator("#bulkProxyResult")
    draft = "\n".join(f"127.0.0.1:{port}" for port in range(18000, 18500))
    text.fill(draft)
    expect(confirm).to_be_enabled()
    too_many = draft + "\n127.0.0.1:18500"
    text.fill(too_many)
    expect(panel.locator("#bulkProxyCounts")).to_contain_text("每次最多 500 条")
    expect(confirm).to_be_disabled()

    for name, data, reason in [
        ("too-large.txt", b"x" * (2 * 1024 * 1024 + 1), "超过 2 MiB"),
        ("not-utf8.csv", b"\xff\x80", "UTF-8"),
    ]:
        file.set_input_files({"name": name, "mimeType": "text/plain", "buffer": data})
        expect(feedback).to_contain_text(reason)
        expect(text).to_have_value(too_many)
        expect(confirm).to_be_disabled()
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM proxies").fetchone()[0] == 0
