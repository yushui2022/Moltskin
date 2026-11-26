"""The Moltskin toolbar button follows the active session workbench."""

from moltskin.services.core import chromium, config
from moltskin.services.core.security import workbench_signature


def test_workbench_url_contains_session_signature():
    url = chromium.workbench_url("ss_0123456789ab")
    assert url == (
        f"http://127.0.0.1:{config.PORT}/workbench/ss_0123456789ab/"
        f"{workbench_signature('ss_0123456789ab')}"
    )


def test_launcher_passes_workbench_button_url(tmp_path, monkeypatch):
    monkeypatch.setattr(
        chromium,
        "resolve_build",
        lambda version: (tmp_path / "Chromium", "158.0.0.0", frozenset()),
    )
    monkeypatch.setattr(chromium, "check_access_policies", lambda *args: None)
    monkeypatch.setattr(chromium, "chromium_owns_directory", lambda path: False)
    target = "http://127.0.0.1:18865/workbench/ss_0123456789ab/signature"
    args, _ = chromium.prepare_launch(
        tmp_path,
        "standard",
        True,
        workbench_button_url=target,
        workbench_session="ss_0123456789ab",
    )
    assert f"--moltskin-workbench-url={target}" in args
    assert args[-1] == target
