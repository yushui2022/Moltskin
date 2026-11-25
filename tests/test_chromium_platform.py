"""Check the installed Chromium's PDF viewer and desktop site isolation."""

import os

import pytest
from playwright.sync_api import expect, sync_playwright

from chromium.native import default_binary, probe


pytestmark = pytest.mark.skipif(os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1", reason="needs the installed Chromium build")


@pytest.fixture
def page():
    binary, _ = probe(default_binary())
    with sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=str(binary), headless=True, chromium_sandbox=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


def test_builtin_viewer_opens_two_page_pdf(page, tmp_path):
    doc = tmp_path / "two-pages.pdf"
    page.set_content('<p style="break-after:page">First page</p><p>Second page</p>')
    page.pdf(path=doc)
    with page.expect_event("framenavigated", predicate=lambda frame: frame.url.startswith("chrome-extension://")) as loaded:
        page.goto(doc.as_uri())
    viewer = loaded.value.locator("pdf-viewer")
    expect(viewer).to_be_visible()
    expect(viewer.locator("viewer-page-selector #pagelength")).to_have_text("2")


def test_cross_site_frame_has_its_own_renderer_target(page):
    origin = "http://127.0.0.1"
    same = origin + "/same"
    cross = "http://localhost/cross"
    content = f'<iframe title="same" src="{same}"></iframe><iframe title="cross" src="{cross}"></iframe>'
    page.route(origin + "/", lambda route: route.fulfill(content_type="text/html", body=content))
    page.route(same, lambda route: route.fulfill(content_type="text/html", body="<p>Same site</p>"))
    page.route(cross, lambda route: route.fulfill(content_type="text/html", body="<p>Other site</p>"))
    page.goto(origin + "/")
    expect(page.frame_locator('[title=same]').locator("p")).to_have_text("Same site")
    expect(page.frame_locator('[title=cross]').locator("p")).to_have_text("Other site")

    # Chromium exposes an out-of-process child frame as a separate iframe target.
    session = page.context.new_cdp_session(page)
    targets = session.send("Target.getTargets")["targetInfos"]
    children = {target["url"] for target in targets if target["type"] == "iframe"}
    assert cross in children
    assert same not in children
