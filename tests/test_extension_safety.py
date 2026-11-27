"""Uploaded browser extensions must stay inside their own directory."""

import io
import json
import zipfile

import pytest

from moltskin.services.core import extensions


def make_xpi(files: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data.encode() if isinstance(data, str) else data)
    return buffer.getvalue()


def manifest(**extra) -> str:
    return json.dumps({"name": "Test addon", "version": "1.0", **extra})


def test_valid_extension_installs(data_dir):
    info = extensions.install(
        "ext_valid",
        make_xpi({"manifest.json": manifest(), "content.js": "console.log(1);"}),
        "valid.xpi",
    )

    assert info["name"] == "Test addon"
    assert (extensions.addon_dir("ext_valid") / "content.js").read_text() == "console.log(1);"


def test_extension_with_top_level_folder_installs(data_dir):
    info = extensions.install(
        "ext_nested",
        make_xpi({"addon/manifest.json": manifest(), "addon/content.js": "ok"}),
        "nested.xpi",
    )

    assert info["name"] == "Test addon"
    assert (extensions.addon_dir("ext_nested") / "manifest.json").is_file()


@pytest.mark.parametrize("member", ["../ext_escape_evil/escaped.txt", "/tmp/escaped.txt", "..\\escaped.txt"])
def test_extension_rejects_members_outside_its_directory(data_dir, member):
    with pytest.raises(ValueError, match="路径非法"):
        extensions.install(
            "ext_escape",
            make_xpi({"manifest.json": manifest(), member: "escaped"}),
            "escape.xpi",
        )

    assert not (extensions.store_dir() / "ext_escape_evil").exists()
    assert not (extensions.store_dir() / "escaped.txt").exists()
    assert not extensions.addon_dir("ext_escape").exists()


def test_extension_rejects_oversized_uncompressed_payload(data_dir, monkeypatch):
    monkeypatch.setattr(extensions, "MAX_EXTRACTED_SIZE", 128)

    with pytest.raises(ValueError, match="大小限制"):
        extensions.install(
            "ext_bomb",
            make_xpi({"manifest.json": manifest(), "large.bin": b"x" * 1024}),
            "large.xpi",
        )

    assert not extensions.addon_dir("ext_bomb").exists()


def test_extension_icon_cannot_escape_through_sibling_prefix(data_dir):
    addon = extensions.addon_dir("ext_icon")
    addon.mkdir(parents=True)
    (addon / "manifest.json").write_text(
        json.dumps({"icons": {"48": "../ext_icon_evil/secret.png"}}), encoding="utf-8"
    )
    outside = extensions.store_dir() / "ext_icon_evil"
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    assert extensions.icon_data_url("ext_icon") is None


def test_extension_icon_symlink_cannot_escape(data_dir, tmp_path):
    addon = extensions.addon_dir("ext_link")
    addon.mkdir(parents=True)
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"\x89PNG\r\n\x1a\n")
    (addon / "icon.png").symlink_to(secret)
    (addon / "manifest.json").write_text(json.dumps({"icons": {"48": "icon.png"}}), encoding="utf-8")

    assert extensions.icon_data_url("ext_link") is None
