"""Verify the native switch that keeps navigator.webdriver false."""

import os

import pytest
from playwright.sync_api import sync_playwright

from chromium.native import WEBDRIVER_CAPABILITY, default_binary, probe_capabilities


def test_launcher_adds_webdriver_switch(tmp_path, monkeypatch):
    from moltskin.services.core import chromium

    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (tmp_path / "Chromium", "158.0.0.0", frozenset()),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(tmp_path, "standard", True)
    assert f"--{WEBDRIVER_CAPABILITY}" in args


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_native_build_reports_webdriver_capability():
    _, _, capabilities = probe_capabilities(default_binary())
    assert WEBDRIVER_CAPABILITY in capabilities


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_navigator_webdriver_stays_false_with_switch():
    binary, _, _ = probe_capabilities(default_binary())
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=str(binary),
            headless=True,
            args=[f"--{WEBDRIVER_CAPABILITY}"],
        )
        try:
            page = browser.new_page()
            page.set_content("<html><body>webdriver probe</body></html>")
            assert page.evaluate("navigator.webdriver") is False
        finally:
            browser.close()
