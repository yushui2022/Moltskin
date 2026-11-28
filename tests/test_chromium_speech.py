"""Stable speech-synthesis voices for the selected website language."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from chromium.native import PLATFORM_CAPABILITY, SPEECH_VOICES_CAPABILITY, default_binary, probe_capabilities
from moltskin.services.core import chromium


def test_launcher_passes_speech_language(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (
            tmp_path / "Chromium",
            "158.0.0.0",
            frozenset({SPEECH_VOICES_CAPABILITY, PLATFORM_CAPABILITY}),
        ),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        browser_language="en-US",
        chromium_platform="windows:10.0.0:x86_64",
    )
    assert "--accept-lang=en-US" in args
    assert "--moltskin-speech-voices=en-US" in args


def test_macos_profile_keeps_host_voices(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (
            tmp_path / "Chromium",
            "158.0.0.0",
            frozenset({SPEECH_VOICES_CAPABILITY, PLATFORM_CAPABILITY}),
        ),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        browser_language="en-US",
        chromium_platform="macos:14.6.0:arm64",
    )
    assert "--accept-lang=en-US" in args
    assert "--moltskin-speech-voices=en-US" not in args


def test_editor_has_no_speech_field():
    html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert "moltskin-speech-voices" not in html


VOICES = """async () => {
  const voices = await new Promise(resolve => {
    const check = () => speechSynthesis.getVoices().length
      ? resolve(speechSynthesis.getVoices())
      : setTimeout(check, 40);
    check();
  });
  return voices.map(voice => ({
    name: voice.name,
    lang: voice.lang,
    uri: voice.voiceURI,
    local: voice.localService,
    default: voice.default,
  }));
}"""


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_native_speech_voices_follow_language():
    binary, _, capabilities = probe_capabilities(default_binary())
    assert SPEECH_VOICES_CAPABILITY in capabilities
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=str(binary),
            headless=True,
            args=["--moltskin-speech-voices=en-US"],
        )
        try:
            page = browser.new_page()
            page.set_content("<html><body>speech probe</body></html>")
            voices = page.evaluate(VOICES)
        finally:
            browser.close()

    assert [voice["name"] for voice in voices] == [
        "Microsoft David - English (United States)",
        "Microsoft Zira - English (United States)",
        "Microsoft Mark - English (United States)",
    ]
    assert all(voice["lang"] == "en-US" for voice in voices)
    assert all(voice["local"] is True for voice in voices)
    assert [voice["default"] for voice in voices] == [True, False, False]
    assert all("Microsoft" in voice["uri"] for voice in voices)


@pytest.mark.skipif(
    os.getenv("MOLTSKIN_TEST_NATIVE_BROWSERS") != "1",
    reason="needs the installed Chromium build",
)
def test_native_locale_alone_keeps_host_voices():
    binary, _, capabilities = probe_capabilities(default_binary())
    assert SPEECH_VOICES_CAPABILITY in capabilities
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=str(binary),
            headless=True,
            args=["--moltskin-locale=en-US"],
        )
        try:
            page = browser.new_page()
            page.set_content("<html><body>speech probe</body></html>")
            voices = page.evaluate(VOICES)
        finally:
            browser.close()

    assert not any(voice["name"].startswith("Microsoft ") for voice in voices)
