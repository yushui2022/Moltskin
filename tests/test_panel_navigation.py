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


def test_open_progress_background_and_failure(panel):
    for name in ("Window A", "Window B"):
        panel.evaluate("name => api('/profiles', {method: 'POST', body: JSON.stringify({name, engine: 'chromium'})})", name)
    panel.evaluate("refresh()")
    rows = panel.locator("#rows tr")
    first = rows.filter(has_text="Window A")
    second = rows.filter(has_text="Window B")
    pending = []
    panel.route("**/profiles/*/launch", lambda route: pending.append(route))
    dialog = panel.get_by_role("dialog", name="正在启动浏览器")

    with panel.expect_request("**/launch"):
        first.get_by_role("button", name="打开", exact=True).press("Enter")
    expect(dialog).to_contain_text("Window A")
    expect(dialog.get_by_role("progressbar")).to_be_visible()
    assert dialog.get_by_role("progressbar").get_attribute("aria-valuenow") is None
    expect(dialog.get_by_role("button", name="后台继续")).to_be_focused()
    panel.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(first.get_by_role("button", name="Window A")).to_be_focused()
    expect(first.get_by_role("button", name="正在处理")).to_be_disabled()
    first.get_by_label("更多操作").click()
    expect(panel.locator('[data-cmd="start"]')).to_be_disabled()
    panel.keyboard.press("Escape")

    with panel.expect_request("**/launch"):
        second.get_by_role("button", name="打开", exact=True).click()
    expect(dialog).to_contain_text("Window B")
    assert len(pending) == 2
    pending[0].fulfill(json={"data": {"state": "running", "session_id": "a"}})
    expect(panel.locator("#toast")).to_have_text("「Window A」已启动")
    expect(dialog).to_contain_text("Window B")
    pending[1].fulfill(status=409, json={"error": {"message": "已达到并发上限"}})
    failed = panel.get_by_role("dialog", name="启动失败")
    expect(failed).to_be_visible()
    expect(failed.locator("#launchStatus")).to_have_text("已达到并发上限")
    expect(failed.get_by_role("progressbar")).to_have_count(0)
    failed.get_by_role("button", name="返回列表").click()
    expect(second.get_by_role("button", name="打开", exact=True)).to_be_enabled()

    with panel.expect_request("**/launch"):
        second.get_by_role("button", name="打开", exact=True).click()
    pending[2].fulfill(json={"data": {"state": "running", "session_id": "b"}})
    expect(dialog).to_have_count(0)
    expect(panel.locator("#toast")).to_have_text("「Window B」已启动")


def test_proxy_selection_create_edit_and_batch(panel):
    def add_proxy(name, **extra):
        return panel.evaluate("""proxy => api('/proxies', {method: 'POST', body: JSON.stringify(proxy)})""",
                              {"name": name, "host": "office.example.test", "port": 8100, **extra})["id"]

    fixed = add_proxy("Office")
    authenticated = add_proxy("Accounts", username="member")
    panel.evaluate("refreshProxies()")
    panel.locator("#windowEmptyCreate").click()
    panel.locator("#fName").fill("Office window")
    panel.locator('button[aria-controls="profileNetwork"]').click()
    proxy = panel.locator("#fProxy")
    proxy.select_option(authenticated)
    panel.locator('button[aria-controls="profileBrowser"]').click()
    panel.locator("#fEngine").select_option("chromium")
    panel.locator("#confirmProfile").click()
    row = panel.locator("#rows tr").filter(has_text="Office window")
    expect(row.locator(".addr")).to_have_text("Accounts")
    row.get_by_label("更多操作").click()
    panel.locator('[data-cmd="edit"]').click()
    panel.locator('button[aria-controls="profileNetwork"]').click()
    expect(proxy).to_have_value(authenticated)
    proxy.select_option(fixed)
    expect(panel.locator("#fNetworkSummary")).to_contain_text("Office")
    panel.locator("#confirmProfile").click()
    expect(row.locator(".addr")).to_have_text("Office")
    row.get_by_label("更多操作").click()
    panel.locator('[data-cmd="edit"]').click()
    panel.locator('button[aria-controls="profileNetwork"]').click()
    proxy.select_option("")
    panel.locator("#confirmProfile").click()
    expect(row.locator(".addr")).to_have_text("系统网络")

    panel.locator("#createMenuBtn").click()
    panel.locator('[data-create="batch"]').click()
    panel.locator("#bPrefix").fill("Team")
    panel.locator("#bCount").fill("2")
    panel.locator("#bEngine").select_option("chromium")
    bulk = panel.locator("#bProxy")
    expect(bulk.locator(f'option[value="{authenticated}"]')).not_to_be_disabled()
    bulk.select_option(fixed)
    panel.locator("#confirmBatch").click()
    for name in ("Team-1", "Team-2"):
        expect(panel.locator("#rows tr").filter(has_text=name).locator(".addr")).to_have_text("Office")


def test_chromium_permission_controls_create_edit_and_batch(panel, monkeypatch):
    from chromium import native

    # Exercise the panel for a capable build even when the installed UI test browser is older.
    probe_capabilities = native.probe_capabilities

    def privacy_capabilities(binary):
        path, version, capabilities = probe_capabilities(binary)
        return path, version, capabilities | {
            native.CANVAS_READBACK_CAPABILITY, native.WEBGL_DEBUG_INFO_CAPABILITY, native.HARDWARE_REPORTING_CAPABILITY,
        }

    monkeypatch.setattr(native, "probe_capabilities", privacy_capabilities)
    panel.reload()
    panel.locator("#windowEmptyCreate").click()
    panel.locator("#fName").fill("Media A")
    panel.locator('button[aria-controls="profileBrowser"]').click()
    field = panel.locator("#fMediaCapture")
    location = panel.locator("#fGeolocation")
    canvas = panel.locator("#fCanvasReadback")
    webgl = panel.locator("#fWebglDebugInfo")
    expect(canvas).to_be_hidden()
    expect(location).to_be_hidden()
    expect(field).to_be_hidden()
    hardware = panel.locator("#fHardwareReporting")
    expect(hardware).to_be_hidden()
    panel.locator("#fEngine").select_option("chromium")
    panel.locator('button[aria-controls="profileHardware"]').click()
    expect(hardware).to_have_value("standard")
    hardware.select_option("limited")
    expect(panel.locator("#fHardwareSummary")).to_have_text("硬件读数受限")
    panel.locator('button[aria-controls="profileFingerprint"]').click()
    expect(field).to_have_value("standard")
    field.select_option("blocked")
    expect(location).to_have_value("standard")
    location.select_option("blocked")
    expect(canvas).to_have_value("standard")
    canvas.select_option("blocked")
    webgl.select_option("blocked")
    panel.locator("#confirmProfile").click()

    row = panel.locator("#rows tr").filter(has_text="Media A")
    row.get_by_role("button", name="Media A").click()
    expect(panel.locator("#fingerprintPreview .kv").filter(has_text="网页可读取的硬件信息")).to_contain_text("最多 4 个逻辑处理器、4 GiB")
    expect(panel.locator("#fingerprintPreview .kv").filter(has_text="摄像头和麦克风")).to_contain_text("始终阻止")
    expect(panel.locator("#fingerprintPreview .kv").filter(has_text="Canvas 像素读取")).to_contain_text("限制读取")
    expect(panel.locator("#fingerprintPreview .kv").filter(has_text="WebGL 显卡信息")).to_contain_text("限制读取")
    panel.keyboard.press("Escape")
    row.get_by_label("更多操作").click()
    panel.locator('[data-cmd="edit"]').click()
    panel.locator('button[aria-controls="profileFingerprint"]').click()
    expect(field).to_have_value("blocked")
    expect(location).to_have_value("blocked")
    expect(canvas).to_have_value("blocked")
    canvas.select_option("standard")
    expect(webgl).to_have_value("blocked")
    webgl.select_option("standard")
    field.select_option("standard")
    location.select_option("standard")
    panel.locator('button[aria-controls="profileHardware"]').click()
    expect(hardware).to_have_value("limited")
    hardware.select_option("standard")
    with panel.expect_response(lambda r: r.request.method == "PATCH" and "/profiles/" in r.url) as updated:
        panel.locator("#confirmProfile").click()
    assert updated.value.json()["data"]["hardware_reporting"] == "standard"
    assert updated.value.json()["data"]["geolocation"] == "standard"
    assert updated.value.json()["data"]["canvas_readback"] == "standard"
    assert updated.value.json()["data"]["webgl_debug_info"] == "standard"
    row.get_by_role("button", name="Media A").click()
    expect(panel.locator("#fingerprintPreview .kv").filter(has_text="地理位置")).to_contain_text("由网站请求授权")
    panel.keyboard.press("Escape")

    panel.locator("#createMenuBtn").click()
    panel.locator('[data-create="batch"]').click()
    panel.locator("#bPrefix").fill("Media batch")
    panel.locator("#bCount").fill("2")
    panel.locator("#bEngine").select_option("chromium")
    panel.locator("#bPreviewPlatform").select_option("Linux")
    panel.locator("#bPreviewArchitecture").select_option("arm64")
    panel.locator("#bPreviewPlatformVersion").fill("6.1")
    panel.locator("#bUserAgent").fill("Moltskin-Batch-QA/1.0")
    panel.locator("#bBrowserLanguage").select_option("en-GB")
    panel.locator("#bBrowserTimezone").select_option("Europe/London")
    panel.locator("#bBrowserWindowSize").select_option("1440x900")
    panel.locator("#bRendererLimit").select_option("2")
    panel.locator("#bHardwareReporting").select_option("limited")
    panel.locator("#bHeapLimit").select_option("256")
    panel.locator("#bMuteAudio").check()
    panel.locator("#bMediaCapture").select_option("blocked")
    panel.locator("#bGeolocation").select_option("blocked")
    panel.locator("#bCanvasReadback").select_option("blocked")
    panel.locator("#bWebglDebugInfo").select_option("blocked")
    panel.locator("#confirmBatch").click()
    for name in ("Media batch-1", "Media batch-2"):
        panel.get_by_role("button", name=name, exact=True).click()
        expect(panel.locator("#fingerprintPreview")).to_contain_text("Moltskin-Batch-QA/1.0")
        expect(panel.locator("#fingerprintPreview")).to_contain_text("Linux · arm64 · 系统版本 6.1")
        expect(panel.locator("#fingerprintPreview")).to_contain_text("en-GB")
        expect(panel.locator("#fingerprintPreview")).to_contain_text("Europe/London")
        expect(panel.locator("#fingerprintPreview")).to_contain_text("1440 × 900")
        expect(panel.locator("#fingerprintPreview")).to_contain_text("2 个进程")
        expect(panel.locator("#fingerprintPreview")).to_contain_text("256 MiB")
        expect(panel.locator("#fingerprintPreview")).to_contain_text("最多 4 个逻辑处理器、4 GiB")
        expect(panel.locator("#fingerprintPreview .kv").filter(has_text="静音播放")).to_contain_text("开启")
        expect(panel.locator("#fingerprintPreview .kv").filter(has_text="摄像头和麦克风")).to_contain_text("始终阻止")
        expect(panel.locator("#fingerprintPreview .kv").filter(has_text="地理位置")).to_contain_text("始终阻止")
        expect(panel.locator("#fingerprintPreview .kv").filter(has_text="Canvas 像素读取")).to_contain_text("限制读取")
        expect(panel.locator("#fingerprintPreview .kv").filter(has_text="WebGL 显卡信息")).to_contain_text("限制读取")
        panel.keyboard.press("Escape")


def test_chromium_fingerprint_choices_saved_by_window_and_locked_while_running(panel):
    from moltskin.services.core import db

    panel.set_viewport_size({"width": 390, "height": 844})
    panel.locator("#windowEmptyCreate").click()
    panel.locator("#fName").fill("东京窗口")
    panel.locator('button[aria-controls="profileBrowser"]').click()
    settings_tab = panel.locator('button[aria-controls="profileFingerprint"]')
    expect(settings_tab).to_be_hidden()
    panel.locator("#fEngine").select_option("chromium")
    settings_tab.click()
    language = panel.get_by_label("网站首选语言", exact=True).filter(visible=True)
    timezone = panel.locator("#fBrowserTimezone")
    size = panel.locator("#fBrowserWindowSize")
    expect(language).to_have_value("")
    expect(timezone).to_have_value("")
    expect(size).to_have_value("")
    language.select_option("ja-JP")
    timezone.select_option("Asia/Tokyo")
    size.select_option("1280x800")
    with panel.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/profiles")) as created:
        panel.locator("#confirmProfile").click()
    profile_id = created.value.json()["data"]["id"]
    panel.get_by_role("button", name="东京窗口", exact=True).click()
    detail = panel.locator("#fingerprintPreview")
    for text in ("ja-JP", "Asia/Tokyo", "1280 × 800"):
        expect(detail).to_contain_text(text)
    panel.get_by_role("button", name="编辑指纹设置").click()
    expect(panel.locator("#profileFingerprint")).to_have_attribute("open", "")
    expect(language).to_have_value("ja-JP")
    expect(timezone).to_have_value("Asia/Tokyo")
    expect(size).to_have_value("1280x800")
    language.select_option("")
    with panel.expect_response(lambda r: r.request.method == "PATCH" and r.url.endswith(profile_id)) as updated:
        panel.locator("#confirmProfile").click()
    assert updated.value.json()["data"]["browser_language"] == ""

    with db.session() as conn:
        conn.execute("UPDATE profiles SET status='running' WHERE id=?", (profile_id,))
    panel.reload()
    panel.get_by_role("button", name="东京窗口", exact=True).click()
    expect(detail).to_contain_text("浏览器默认")
    expect(detail).to_contain_text("Asia/Tokyo")
    panel.get_by_role("button", name="查看指纹设置").click()
    expect(language).to_be_disabled()
    expect(timezone).to_be_disabled()
    expect(size).to_be_disabled()
    for control in ("fHardwareReporting", "fCanvasReadback", "fWebglDebugInfo", "fChromiumVersion", "fRendererLimit", "fHeapLimit", "fMuteAudio", "fUserAgent", "fResetUserAgent",
                    "fPreviewPlatform", "fPreviewPlatformVersion", "fPreviewArchitecture", "fResetPlatformPreview"):
        expect(panel.locator("#" + control)).to_be_disabled()
    expect(panel.locator("#fRegionHint")).to_contain_text("关闭此窗口后")


def test_chromium_build_pin_and_resource_controls(panel):
    page = panel
    page.set_viewport_size({"width": 320, "height": 720})
    page.locator("#windowEmptyCreate").click()
    page.locator("#fName").fill("Pinned window")
    page.locator('button[aria-controls="profileBrowser"]').click()
    pin = page.get_by_label("启动内核版本", exact=True).filter(visible=True)
    page.locator("#fEngine").select_option("chromium")
    version = probe(default_binary())[1]
    expect(pin).to_have_value("")
    pin.select_option(version)
    expect(page.locator("#fHostOs")).not_to_have_text("未提供")
    expect(page.locator("#fHostArch")).not_to_be_empty()
    page.locator('button[aria-controls="profileHardware"]').click()
    assert page.locator(".editor-nav").evaluate("nav => nav.scrollWidth <= nav.clientWidth")
    page.locator("#fRendererLimit").select_option("4")
    page.locator("#fHeapLimit").select_option("512")
    expect(page.locator("#fHardwareSummary")).to_have_text("4 个进程 · 512 MiB")
    link = page.locator("#profileHardware").get_by_role("link", name="查看当前浏览器实测值")
    expect(link).to_have_attribute("href", "/runtime.html")
    page.locator('button[aria-controls="profileStartup"]').click()
    page.locator("#fMuteAudio").check()
    with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/profiles")) as created:
        page.locator("#confirmProfile").click()
    profile = created.value.json()["data"]
    assert (profile["chromium_version"], profile["chromium_renderer_limit"],
            profile["chromium_js_heap_mb"], profile["chromium_mute_audio"]) == (version, 4, 512, True)
    page.get_by_role("button", name="Pinned window", exact=True).click()
    preview = page.locator("#fingerprintPreview")
    for rendered in (version, "4 个进程", "512 MiB"):
        expect(preview).to_contain_text(rendered)
    page.get_by_role("button", name="编辑指纹设置").click()
    page.locator('button[aria-controls="profileBrowser"]').click()
    expect(pin).to_have_value(version)
    pin.select_option("")
    page.locator('button[aria-controls="profileHardware"]').click()
    expect(page.locator("#fHeapLimit")).to_have_value("512")
    page.locator("#fHeapLimit").select_option("0")
    with page.expect_response(lambda r: r.request.method == "PATCH") as edited:
        page.locator("#confirmProfile").click()
    saved = edited.value.json()["data"]
    assert (saved["chromium_version"], saved["chromium_js_heap_mb"], saved["chromium_renderer_limit"]) == ("", 0, 4)


def test_only_installed_build_enables_its_own_permissions(panel):
    version = "150.0.2.3"
    older_version = "149.0.1.0"
    panel.route("**/api/v1/engine", lambda route: route.fulfill(json={"data": {
        "browsers": [], "chromium": {
            "version": None, "available": True, "installed_versions": [
                {"version": version, "supports_media_capture_policy": True, "supports_geolocation_policy": False,
                 "supports_canvas_readback_policy": True, "supports_webgl_debug_info_policy": True,
                 "supports_hardware_reporting_policy": True},
                {"version": older_version, "supports_webgl_debug_info_policy": True}],
            "supports_media_capture_policy": False, "supports_geolocation_policy": False,
            "supports_canvas_readback_policy": False, "supports_webgl_debug_info_policy": False,
        },
    }}))
    panel.goto("http://panel.test/?build-catalog#/windows/new/chromium/browser")
    expect(panel.locator("#profileBrowser")).to_have_attribute("open", "")
    expect(panel.locator("#fUserAgent")).to_be_disabled()
    for field in ("PreviewPlatform", "PreviewArchitecture", "PreviewPlatformVersion", "RunPlatformPreview"):
        expect(panel.locator("#f" + field)).to_be_disabled()
    expect(panel.locator("#fPreviewAvailability")).to_contain_text("当前服务版本")
    expect(panel.locator("#fUserAgentHint")).to_contain_text("当前服务版本")
    hint = panel.locator("#fChromiumVersionHint")
    expect(hint).to_contain_text("缺少默认内核")
    panel.locator("#fChromiumVersion").select_option(version)
    expect(hint).to_contain_text("2 个版本")
    panel.locator('button[aria-controls="profileHardware"]').click()
    hardware = panel.locator("#fHardwareReporting")
    hardware.select_option("limited")
    panel.locator('button[aria-controls="profileFingerprint"]').click()
    expect(panel.locator('#fMediaCapture option[value="blocked"]')).to_be_enabled()
    expect(panel.locator('#fGeolocation option[value="blocked"]')).to_be_disabled()
    webgl = panel.locator("#fWebglDebugInfo")
    canvas = panel.locator("#fCanvasReadback")
    canvas.select_option("blocked")
    webgl.select_option("blocked")
    panel.locator('button[aria-controls="profileBrowser"]').click()
    panel.locator("#fChromiumVersion").select_option(older_version)
    expect(canvas.locator('option[value="blocked"]')).to_be_disabled()
    expect(webgl.locator('option[value="blocked"]')).to_be_enabled()
    expect(hardware.locator('option[value="limited"]')).to_be_disabled()
    panel.locator("#fChromiumVersion").select_option("")
    expect(panel.locator('#fMediaCapture option[value="blocked"]')).to_be_disabled()
    expect(canvas.locator('option[value="blocked"]')).to_be_disabled()
    expect(webgl.locator('option[value="blocked"]')).to_be_disabled()
    panel.locator('button[aria-controls="profileBasics"]').click()
    panel.locator("#fName").fill("GPU permissions")
    panel.locator("#confirmProfile").click()
    expect(hardware).to_be_focused()
    expect(hardware).to_have_value("limited")
    expect(panel.locator("#fHardwareReportingHint")).to_contain_text("不支持")
    hardware.select_option("standard")
    panel.locator("#confirmProfile").click()
    expect(canvas).to_be_focused()
    expect(canvas).to_have_value("blocked")
    expect(panel.locator("#fCanvasHint")).to_contain_text("不支持")
    canvas.select_option("standard")
    panel.locator("#confirmProfile").click()
    expect(webgl).to_be_focused()
    expect(webgl).to_have_value("blocked")
    expect(panel.locator("#fWebglHint")).to_contain_text("不支持")
    webgl.select_option("standard")
    panel.locator('button[aria-controls="profileStartup"]').click()
    expect(panel.locator("#fDashboardHint")).to_have_text("此启动方式默认打开空白页。")
    with panel.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/profiles")) as saved:
        panel.locator("#confirmProfile").click()
    assert saved.value.status == 201
    assert saved.value.json()["data"]["canvas_readback"] == "standard"
    assert saved.value.json()["data"]["webgl_debug_info"] == "standard"
    panel.goto("http://panel.test/?build-catalog#/windows/new/chromium/hardware")
    expect(panel.locator("#profileHardware")).to_have_attribute("open", "")


def test_local_platform_preview_measures_draft_and_restores_saved_choices(panel):
    panel.set_viewport_size({"width": 320, "height": 720})
    panel.locator("#windowEmptyCreate").click()
    panel.locator("#fName").fill("Platform QA")
    browser_tab = panel.locator('button[aria-controls="profileBrowser"]')
    browser_tab.click()
    fields = panel.locator("#fPlatformPreview")
    expect(fields).to_be_hidden()
    panel.locator("#fEngine").select_option("chromium")
    expect(fields).to_contain_text("常规网页仍使用实际运行环境")
    assert fields.evaluate("el => el.scrollWidth <= el.clientWidth")
    platform = fields.get_by_label("测试系统", exact=True)
    arch = fields.get_by_label("测试处理器架构")
    version = fields.get_by_label("系统版本（可选）")
    run = fields.get_by_role("button", name="运行本地预览")
    result = fields.get_by_role("status", include_hidden=True)
    pin = probe(default_binary())[1]
    panel.locator("#fChromiumVersion").select_option(pin)
    platform.select_option("Windows")
    arch.select_option("x86_64")
    for invalid in ("15..1", "６", "1.2.3.4", "123456"):
        version.fill(invalid)
        expect(run).to_be_disabled()
        expect(panel.locator("#fPreviewVersionError")).to_be_visible()
    panel.locator('button[aria-controls="profileStartup"]').click()
    panel.locator("#confirmProfile").click()
    expect(version).to_be_focused()
    version.fill("15.4.1")
    settings = {"preview_platform": "Windows", "preview_architecture": "x86_64", "preview_platform_version": "15.4.1"}
    with panel.expect_response("**/chromium/platform-preview") as measured:
        run.click()
    assert measured.value.request.post_data_json == {**settings, "chromium_version": pin}
    assert measured.value.ok
    expect(result).to_contain_text("系统 Windows · 系统版本 15.4.1 · 架构 x86 · 位数 64")
    version.fill("")
    run.click()
    expect(result).to_contain_text("系统 Windows · 系统版本 浏览器未提供 · 架构 x86 · 位数 64")
    version.fill("15.4.1")
    arch.select_option("")
    expect(result).to_be_empty()
    expect(platform).to_have_value("Windows")
    expect(version).to_have_value("15.4.1")
    arch.select_option("x86_64")
    with panel.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/profiles")) as created:
        panel.locator("#confirmProfile").click()
    profile = created.value.json()["data"]
    assert {k: profile[k] for k in settings} == settings
    panel.get_by_role("button", name="Platform QA", exact=True).click()
    expect(panel.locator("#fingerprintPreview")).to_contain_text("Windows · x86_64 · 系统版本 15.4.1")
    panel.get_by_role("button", name="编辑指纹设置").click()
    browser_tab.click()
    expect(platform).to_have_value("Windows")
    expect(arch).to_have_value("x86_64")
    expect(version).to_have_value("15.4.1")
    expect(result).to_be_empty()
    fields.get_by_role("button", name="重置预览设置").click()
    for field in (platform, arch, version):
        expect(field).to_have_value("")
    with panel.expect_response(lambda r: r.request.method == "PATCH") as updated:
        panel.locator("#confirmProfile").click()
    assert all(updated.value.json()["data"][key] == "" for key in settings)


def test_local_preview_ignores_superseded_requests_and_shows_inline_failures(panel):
    panel.goto("http://panel.test/#/windows/new/chromium/browser")
    endpoint = "**/chromium/platform-preview"
    pending = []
    panel.route(endpoint, lambda route: pending.append(route))
    run = panel.locator("#fRunPlatformPreview")
    result = panel.locator("#fPlatformPreviewResult")
    with panel.expect_request(endpoint):
        run.click()
    expect(run).to_be_disabled()
    expect(result).to_contain_text("正在读取")
    panel.locator("#fPreviewArchitecture").select_option("arm64")
    pending.pop().fulfill(json={"data": {"platform": "过期", "architecture": "x86", "bitness": "64", "platform_version": ""}})
    expect(result).to_be_empty()
    expect(run).to_be_enabled()
    panel.route(endpoint, lambda route: route.fulfill(status=503, json={"error": {"message": "测试内核不可用"}}))
    run.click()
    expect(result).to_contain_text("本地预览失败：测试内核不可用")
    panel.locator("#fResetPlatformPreview").click()
    expect(result).to_be_empty()


def test_chromium_startup_page_distinguishes_workbench_and_manager(panel):
    panel.goto("http://panel.test/#/windows/new/chromium/browser")
    panel.locator('button[aria-controls="profileStartup"]').click()
    startup = panel.locator("#fStartupPage")
    hint = panel.locator("#fDashboardHint")
    expect(startup).to_have_value("default")
    expect(hint).to_contain_text("首个标签页显示当前环境的工作台")
    panel.locator("#fHeadless").select_option("1")
    expect(hint).to_contain_text("空白页")
    startup.select_option(label="管理首页（全部环境）")
    expect(startup).to_have_value("dashboard")
    expect(hint).to_be_hidden()
    startup.select_option(label="空白页")
    expect(startup).to_have_value("blank")
    panel.keyboard.press("Escape")
    panel.locator("#createMenuBtn").click()
    panel.locator('[data-create="batch"]').click()
    panel.locator("#bEngine").select_option("chromium")
    expect(panel.locator("#bDashboardHint")).to_contain_text("当前环境的工作台")
    panel.locator("#bHeadless").select_option("1")
    expect(panel.locator("#bDashboardHint")).to_contain_text("空白页")
    panel.keyboard.press("Escape")
    panel.get_by_role("link", name="设置", exact=True).click()
    expect(panel.locator("#sManagerHint")).to_contain_text("无头 Chromium 打开空白页")


def test_chromium_manual_ua_validation_save_and_reset(panel):
    ua = panel.locator("#fUserAgent")
    reset = panel.locator("#fResetUserAgent")
    error = panel.locator("#fUserAgentError")
    panel.locator("#windowEmptyCreate").click()
    panel.locator("#fName").fill("UA test window")
    browser_tab = panel.locator('button[aria-controls="profileBrowser"]')
    browser_tab.click()
    expect(ua).to_be_hidden()
    panel.locator("#fEngine").select_option("chromium")
    expect(ua).to_have_value("")
    expect(reset).to_be_disabled()
    for value, feedback in [("test\nheader", "单行"), ("UA 尾", "ASCII"), (" UA/1", "首尾空格"), ("A" * 513, "512")]:
        ua.fill(value)
        expect(error).to_contain_text(feedback)
    panel.locator('button[aria-controls="profileStartup"]').click()
    panel.locator("#confirmProfile").click()
    expect(ua).to_be_focused()
    custom = "Moltskin-Test/1.0 (<lab> & local)"
    ua.fill(custom)
    expect(error).to_be_hidden()
    with panel.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/profiles")) as created:
        panel.locator("#confirmProfile").click()
    assert created.value.json()["data"]["chromium_user_agent"] == custom
    panel.get_by_role("button", name="UA test window").click()
    preview = panel.locator("#fingerprintPreview")
    expect(preview).to_contain_text(custom)
    expect(preview.locator("lab")).to_have_count(0)
    panel.get_by_role("button", name="编辑指纹设置").click()
    browser_tab.click()
    expect(ua).to_have_value(custom)
    reset.click()
    expect(ua).to_be_focused()
    expect(ua).to_have_value("")
    expect(reset).to_be_disabled()
    with panel.expect_response(lambda r: r.request.method == "PATCH") as updated:
        panel.locator("#confirmProfile").click()
    assert updated.value.json()["data"]["chromium_user_agent"] == ""
    panel.get_by_role("button", name="UA test window").click()
    expect(preview.locator(".kv").filter(has_text="User-Agent")).to_contain_text("跟随内核默认")


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
    from moltskin.services.core.secret_store import decrypt, is_encrypted

    with db.session() as conn:
        saved = conn.execute("SELECT protocol, host, port, username, password FROM proxies ORDER BY port").fetchall()
    assert [tuple(row)[:4] for row in saved] == [
        ("socks5", "127.0.0.1", 18080, "fixture-user"),
        ("https", "127.0.0.1", 18443, "another-user"),
    ]
    assert [decrypt(row["password"]) for row in saved] == ["fixture-secret", "pass,with,comma"]
    assert all(is_encrypted(row["password"]) for row in saved)


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


@pytest.fixture
def proxy_inventory(panel):
    from moltskin.services.core import db

    with db.session() as conn:
        conn.executemany(
            "INSERT INTO proxies(id,name,host,status,exit_ip,protocol,port,created_at,updated_at) "
            "VALUES (?,?,?,?,?,'http',8080,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            [
                ("px_alpha", "Office A", "alpha.example.test", "ok", "192.0.2.10"),
                ("px_beta", "Office B", "beta.example.test", "slow", "192.0.2.11"),
                ("px_gamma", "Lab", "gamma.example.test", "failed", None),
                ("px_delta", "Spare", "delta.example.test", "unchecked", None),
            ],
        )
    panel.locator('a[href="#/proxies"]').click()
    panel.locator("#refreshProxies").click()
    expect(panel.locator("#proxyRows tr")).to_have_count(4)
    return panel


def test_workspace_header_images_decode_locally(panel):
    windows_header = panel.locator("#page-windows .page-head")
    texture_url = windows_header.evaluate("el => getComputedStyle(el, '::before').backgroundImage")
    assert texture_url.startswith('url("http://panel.test/') and texture_url.endswith('")')
    assert panel.evaluate("""async src => {
      const image = new Image();
      image.src = src;
      await image.decode();
      return image.naturalWidth > 0;
    }""", texture_url[5:-2])

    panel.locator('a[href="#/proxies"]').click()
    hero = panel.locator("#page-proxies .proxy-hero-image")
    assert hero.evaluate("""async image => {
      await image.decode();
      return new URL(image.currentSrc).origin === location.origin && image.naturalWidth > 0;
    }""")


def test_proxy_inventory_combines_search_and_status_and_refreshes(proxy_inventory):
    from moltskin.services.core import db

    page = proxy_inventory
    search = page.locator("#proxySearch")
    status = page.locator("#proxyStatusFilter")
    rows = page.locator("#proxyRows tr")
    search.fill("office")
    expect(rows).to_have_count(2)
    status.select_option("slow")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("Office B")
    expect(page.locator("#proxyTotal")).to_have_text("4")
    expect(page.locator("#proxyAvailable")).to_have_text("2")
    expect(page.locator("#proxyFailed")).to_have_text("1")
    expect(page.locator("#proxyUnchecked")).to_have_text("1")

    search.fill("alpha.example")
    expect(rows).to_have_count(0)
    expect(page.locator("#proxyNoMatches")).to_be_visible()
    expect(page.locator("#proxyEmpty")).to_be_hidden()
    page.locator("#clearProxyFilters").click()
    expect(search).to_have_value("")
    expect(status).to_have_value("all")
    expect(rows).to_have_count(4)

    for query in ("ALPHA.EXAMPLE", "192.0.2.10"):
        search.fill(query)
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("Office A")
    status.select_option("ok")
    with db.session() as conn:
        conn.execute("UPDATE proxies SET status='failed' WHERE id='px_alpha'")
    page.locator("#refreshProxies").click()
    expect(page.locator("#proxyNoMatches")).to_be_visible()
    expect(search).to_have_value("192.0.2.10")
    expect(status).to_have_value("ok")
    expect(page.locator("#proxyFailed")).to_have_text("2")


def test_proxy_inventory_mobile_address_and_actions_fit(proxy_inventory):
    page = proxy_inventory
    page.set_viewport_size({"width": 390, "height": 844})
    row = page.locator("#proxyRows tr").filter(has_text="Office A")
    endpoint = row.get_by_text("http://alpha.example.test:8080", exact=True)
    check = row.get_by_role("button", name=re.compile("检测"))
    delete = row.get_by_role("button", name=re.compile("删除"))
    row.scroll_into_view_if_needed()
    for element in (endpoint, check, delete):
        expect(element).to_be_visible()
        box = element.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    delete.click()
    expect(page.locator("#confirmMask")).to_contain_text("Office A")
    page.keyboard.press("Escape")
    expect(row).to_be_visible()
