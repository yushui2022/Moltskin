"""WebRTC must not bypass a Chromium profile's fixed proxy."""

from moltskin.services.core import chromium


def test_proxy_profile_disables_non_proxied_webrtc(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (tmp_path / "Chromium", "158.0.0.0", frozenset()),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        proxy_server="http://127.0.0.1:8080",
    )
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args


def test_direct_profile_keeps_default_webrtc_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (tmp_path / "Chromium", "158.0.0.0", frozenset()),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    args, _ = chromium.prepare_launch(tmp_path, "standard", True)
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" not in args
