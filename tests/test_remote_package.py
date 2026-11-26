"""Cover Ubuntu package staging and prevent incomplete or unsafe preview packages."""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from chromium.remote import package


def test_package_keeps_only_runtime_files_and_installs_fixed_profile(tmp_path, monkeypatch):
    src = tmp_path / "checkout/src"
    build = src / "out/privacy"
    for name in (*package.EXECUTABLES, *package.RESOURCES, "locales/zh-CN.pak", "locales/en-US.pak.info",
                 "hyphen-data/manifest.json", "obj/debug.o", "profile/Cookies", "chrome_sandbox"):
        path = build / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"resource")
        path.chmod(0o777)
    (build / "chrome").write_bytes(b"\x7fELF\x02\x01" + bytes(12) + b"\x3e\x00")
    (src / "LICENSE").write_text("Chromium notice")
    pin = json.loads((package.ROOT / "chromium/upstream.json").read_text())
    monkeypatch.setattr(package.native, "probe_capabilities",
                        lambda binary: (binary, pin["version"], package.native.DESKTOP_CAPABILITIES))
    output = tmp_path / "preview.deb"
    output.write_bytes(b"previous")

    def inspect(args, *, check):
        assert "--root-owner-group" in args and check
        root, archive = map(Path, args[-2:])
        dest = root / package.INSTALL_DIR
        assert (dest / "locales/zh-CN.pak").is_file()
        assert (dest / "hyphen-data/manifest.json").is_file()
        assert {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()} == {
            *package.EXECUTABLES, *package.RESOURCES, "locales/zh-CN.pak", "hyphen-data/manifest.json"
        }
        assert "profile moltskin-preview /opt/moltskin-preview/chrome " in (
            root / "etc/apparmor.d/moltskin-preview"
        ).read_text()
        assert "Chromium notice" in (root / "usr/share/doc/moltskin-linux-preview/copyright").read_text()
        assert "Architecture: amd64" in (root / "DEBIAN/control").read_text()
        for path in root.rglob("*"):
            mode = 0o755 if path.is_dir() or path.name in package.EXECUTABLES else 0o644
            assert stat.S_IMODE(path.stat().st_mode) == mode
        assert output.read_bytes() == b"previous"
        archive.write_bytes(b"complete package")

    monkeypatch.setattr(subprocess, "run", inspect)
    old_umask = os.umask(0o077)
    try:
        package.package(build, output)
    finally:
        os.umask(old_umask)
    assert output.read_bytes() == b"complete package"

    (build / "locales/zh-CN.pak").unlink()
    (build / "locales/zh-CN.pak").symlink_to(tmp_path / "preview.deb")
    with pytest.raises(ValueError, match="regular build resource"):
        package.package(build, output)
    assert output.read_bytes() == b"complete package"


def test_package_requires_pinned_patched_linux_binary(tmp_path, monkeypatch):
    binary = tmp_path / "chrome"
    binary.write_bytes(b"Mac executable")
    with pytest.raises(ValueError, match="x86-64 ELF"):
        package.verify_build(tmp_path)
    binary.write_bytes(b"\x7fELF\x02\x01" + bytes(12) + b"\x3e\x00")
    version = json.loads((package.ROOT / "chromium/upstream.json").read_text())["version"]
    monkeypatch.setattr(package.native, "probe_capabilities", lambda path: (path, "1.2.3.4", set()))
    with pytest.raises(ValueError, match="expected Chromium"):
        package.verify_build(tmp_path)
    monkeypatch.setattr(package.native, "probe_capabilities", lambda path: (path, version, set()))
    with pytest.raises(ValueError, match="build lacks"):
        package.verify_build(tmp_path)
