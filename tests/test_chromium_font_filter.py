"""Windows-like CSS font family filtering for Chromium profiles."""

import os
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from chromium.native import FONT_FILTER_CAPABILITY, PLATFORM_CAPABILITY, default_binary, probe_capabilities
from moltskin.services.core import chromium

FONT_APIS = """async () => {
  const names = ['PingFang HK', 'Helvetica', 'Apple SD Gothic Neo', 'Futura', 'Arial', 'Georgia'];
  const checks = {};
  for (const name of names) checks[name] = document.fonts.check(`0px "${name}"`);
  const loads = {};
  for (const name of names) {
    try {
      const face = new FontFace(name, `local("${name}")`);
      await face.load();
      loads[name] = face.status;
    } catch {
      loads[name] = 'error';
    }
  }
  return { checks, loads };
}"""

FONT_WIDTHS = """() => {
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  const width = family => {
    context.font = '16px ' + JSON.stringify(family) + ', monospace';
    return context.measureText('mmmmmmmmmmlli').width;
  };
  return {
    monospace: width('monospace'),
    arial: width('Arial'),
    georgia: width('Georgia'),
    pingfang: width('PingFang HK'),
    helvetica: width('Helvetica'),
    appleGothic: width('Apple SD Gothic Neo'),
    futura: width('Futura'),
  };
}"""


def test_launcher_enables_font_filter_for_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (
            tmp_path / "Chromium",
            "158.0.0.0",
            frozenset({FONT_FILTER_CAPABILITY, PLATFORM_CAPABILITY}),
        ),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        chromium_platform="windows:10.0.0:x86_64",
    )
    assert "--moltskin-font-filter=windows" in args


def test_same_os_windows_keeps_host_fonts(tmp_path, monkeypatch):
    monkeypatch.setattr(chromium, "_host_platform_family", lambda: "windows")
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (
            tmp_path / "Chromium",
            "158.0.0.0",
            frozenset({FONT_FILTER_CAPABILITY, PLATFORM_CAPABILITY}),
        ),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        chromium_platform="windows:10.0.0:x86_64",
    )
    assert "--moltskin-font-filter=windows" not in args


def test_launcher_keeps_host_fonts_for_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (
            tmp_path / "Chromium",
            "158.0.0.0",
            frozenset({FONT_FILTER_CAPABILITY, PLATFORM_CAPABILITY}),
        ),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        chromium_platform="macos:14.6.0:arm64",
    )
    assert "--moltskin-font-filter=windows" not in args


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1" or sys.platform != "darwin",
    reason="needs the installed macOS Chromium and its local fonts",
)
def test_native_font_filter_hides_macos_families():
    binary, _, capabilities = probe_capabilities(default_binary())
    assert FONT_FILTER_CAPABILITY in capabilities

    def read(args, script):
        with sync_playwright() as driver:
            browser = driver.chromium.launch(
                executable_path=str(binary),
                headless=True,
                args=args,
            )
            try:
                page = browser.new_page()
                page.set_content("<html><body>font probe</body></html>")
                return page.evaluate(script)
            finally:
                browser.close()

    def widths(args):
        return read(args, FONT_WIDTHS)

    def apis(args):
        return read(args, FONT_APIS)

    baseline = widths([])
    filtered = widths(["--moltskin-font-filter=windows"])
    baseline_apis = apis([])
    filtered_apis = apis(["--moltskin-font-filter=windows"])

    assert baseline["pingfang"] != baseline["monospace"]
    assert baseline["helvetica"] != baseline["monospace"]
    assert filtered["pingfang"] == filtered["monospace"]
    assert filtered["helvetica"] == filtered["monospace"]
    assert filtered["appleGothic"] == filtered["monospace"]
    assert filtered["futura"] == filtered["monospace"]
    assert filtered["arial"] != filtered["monospace"]
    assert filtered["georgia"] != filtered["monospace"]

    assert baseline_apis["checks"]["PingFang HK"] is True
    assert filtered_apis["checks"]["PingFang HK"] is False
    assert filtered_apis["checks"]["Helvetica"] is False
    assert filtered_apis["checks"]["Arial"] is True
    assert filtered_apis["loads"]["PingFang HK"] == "error"
    assert filtered_apis["loads"]["Arial"] == "loaded"


FONT_WEB = """async (dataUri) => {
  const width = family => {
    const context = document.createElement('canvas').getContext('2d');
    context.font = '32px ' + JSON.stringify(family) + ', monospace';
    return context.measureText('mmmmmmmmmmlli').width;
  };
  const fallback = width('InterMoltskinWeb');
  const face = new FontFace('InterMoltskinWeb', `url(${dataUri})`);
  await face.load();
  document.fonts.add(face);
  const loadedWidth = width('InterMoltskinWeb');
  const remotePingFang = new FontFace('PingFang HK', `url(${dataUri})`);
  await remotePingFang.load();

  const style = document.createElement('style');
  style.textContent = `@font-face { font-family: InterMoltskinCss; src: url(${dataUri}); }`;
  document.head.append(style);
  await document.fonts.load('32px InterMoltskinCss');
  const cssWidth = width('InterMoltskinCss');
  return {
    fallback,
    loadedWidth,
    cssWidth,
    checkLoaded: document.fonts.check('32px InterMoltskinWeb'),
    checkCss: document.fonts.check('32px InterMoltskinCss'),
    faceStatus: face.status,
    remotePingFangStatus: remotePingFang.status,
    remotePingFangCheck: document.fonts.check('32px "PingFang HK"'),
  };
}"""


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1" or sys.platform != "darwin",
    reason="needs the installed macOS Chromium and its local fonts",
)
def test_native_font_filter_keeps_remote_web_fonts():
    import base64

    binary, _, capabilities = probe_capabilities(default_binary())
    assert FONT_FILTER_CAPABILITY in capabilities
    font = (Path(__file__).resolve().parents[1] / "web" / "fonts" / "inter-400.woff2").read_bytes()
    data_uri = "data:font/woff2;base64," + base64.b64encode(font).decode("ascii")

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=str(binary),
            headless=True,
            args=["--moltskin-font-filter=windows"],
        )
        try:
            page = browser.new_page()
            page.set_content("<html><body>web font probe</body></html>")
            result = page.evaluate(FONT_WEB, data_uri)
        finally:
            browser.close()

    assert result["faceStatus"] == "loaded"
    assert result["checkLoaded"] is True
    assert result["checkCss"] is True
    assert result["remotePingFangStatus"] == "loaded"
    assert result["loadedWidth"] != result["fallback"]
    assert result["cssWidth"] != result["fallback"]
