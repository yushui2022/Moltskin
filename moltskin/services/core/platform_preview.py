"""Measure platform client hints in a temporary Chromium on one offline document."""

from threading import BoundedSemaphore

from playwright.sync_api import sync_playwright

from chromium.native import resolve_build


DOCUMENT = "http://127.0.0.1/moltskin-platform-preview"
_slots = BoundedSemaphore(2)
_read_hints = """() => navigator.userAgentData.getHighEntropyValues(
    ['architecture', 'bitness', 'platformVersion', 'model', 'fullVersionList'])"""


class PreviewBusy(Exception):
    pass


def measure(*, platform: str, platform_version: str, architecture: str, chromium_version: str) -> dict[str, str]:
    if not _slots.acquire(blocking=False):
        raise PreviewBusy
    try:
        binary = resolve_build(chromium_version).binary
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=str(binary), headless=True,
                                             chromium_sandbox=True, timeout=12000)
            try:
                context = browser.new_context(offline=True, service_workers="block")

                def serve(route):
                    if route.request.url == DOCUMENT and route.request.method == "GET":
                        route.fulfill(content_type="text/html", body="<!doctype html><title>Moltskin</title>")
                    else:
                        route.abort()

                context.route("**/*", serve)
                page = context.new_page()
                page.goto(DOCUMENT, timeout=5000)
                hints = page.evaluate(_read_hints)
                if platform or platform_version or architecture:
                    if platform and platform != hints["platform"]:
                        hints.update(platform=platform, platformVersion="")
                    if platform_version:
                        hints["platformVersion"] = platform_version
                    if architecture:
                        hints.update(architecture={"arm64": "arm", "x86_64": "x86"}[architecture], bitness="64")
                    cdp = context.new_cdp_session(page)
                    cdp.send("Emulation.setUserAgentOverride", {
                        "userAgent": page.evaluate("navigator.userAgent"), "userAgentMetadata": hints,
                    })
                    page.reload(timeout=5000)
                    hints = page.evaluate(_read_hints)
                return {"platform": hints["platform"], "platform_version": hints["platformVersion"],
                        "architecture": hints["architecture"], "bitness": hints["bitness"]}
            finally:
                browser.close()
    finally:
        _slots.release()
