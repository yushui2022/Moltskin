"""Build a local Ubuntu 24.04 amd64 package from a Moltskin Chromium build."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if not __package__:
    sys.path.insert(0, str(ROOT))
from chromium import native


INSTALL_DIR = "opt/moltskin-preview"
EXECUTABLES = ("chrome", "chrome_crashpad_handler")
RESOURCES = (
    "chrome_100_percent.pak", "chrome_200_percent.pak", "resources.pak", "icudtl.dat",
    "v8_context_snapshot.bin", "libEGL.so", "libGLESv2.so", "locales/en-US.pak",
)
EXTRA_RESOURCES = (
    "snapshot_blob.bin", "libvk_swiftshader.so", "libvulkan.so.1", "vk_swiftshader_icd.json",
    "libqt5_shim.so", "libqt6_shim.so",
)
APPARMOR_PROFILE = """abi <abi/4.0>,
include <tunables/global>

profile moltskin-preview /opt/moltskin-preview/chrome flags=(unconfined) {
  userns,
  include if exists <local/moltskin-preview>
}
"""
DEPENDS = (
    "apparmor (>= 4), libasound2t64, libatk-bridge2.0-0t64, libatk1.0-0t64, "
    "libatspi2.0-0t64, libc6, libcairo2, libcups2t64, libdbus-1-3, libexpat1, "
    "libgbm1, libgcc-s1, libglib2.0-0t64, libnspr4, libnss3, libpango-1.0-0, "
    "libudev1, libx11-6, libxcb1, libxcomposite1, libxdamage1, libxext6, "
    "libxfixes3, libxkbcommon0, libxrandr2"
)


def verify_build(build):
    binary = build / "chrome"
    if binary.is_symlink() or not binary.is_file():
        raise ValueError("expected a regular chrome executable in the build directory")
    with binary.open("rb") as f:
        header = f.read(20)
    if not header.startswith(b"\x7fELF\x02\x01") or header[18:20] != b"\x3e\x00":
        raise ValueError("expected a Linux x86-64 ELF build")
    pin = json.loads((ROOT / "chromium/upstream.json").read_text())
    if build.parent.name == "out" and (build.parent.parent / ".git").exists():
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=build.parent.parent, text=True).strip()
        if head != pin["commit"]:
            raise ValueError(f"expected Chromium source revision {pin['commit']}, found {head}")
    _, version, capabilities = native.probe_capabilities(binary)
    if version != pin["version"]:
        raise ValueError(f"expected Chromium {pin['version']}, found {version}")
    missing = native.DESKTOP_CAPABILITIES - capabilities
    if missing:
        raise ValueError("build lacks " + ", ".join(sorted(missing)))
    return version


def stage(build, tree, version):
    install = tree / INSTALL_DIR
    resources = {build / name for name in (*EXECUTABLES, *RESOURCES)}
    resources.update(build / name for name in EXTRA_RESOURCES if (build / name).exists())
    resources.update((build / "locales").glob("*.pak"))
    for name in ("IwaKeyDistribution", "MEIPreload", "resources", "hyphen-data"):
        resources.update(p for p in (build / name).rglob("*") if not p.is_dir() or p.is_symlink())
    for source in sorted(resources):
        if not source.is_file() or source.resolve() != source:
            raise ValueError(f"expected a regular build resource: {source}")
        relative = source.relative_to(build)
        target = install / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o755 if str(relative) in EXECUTABLES else 0o644)

    profile = tree / "etc/apparmor.d/moltskin-preview"
    profile.parent.mkdir(parents=True)
    profile.write_text(APPARMOR_PROFILE)
    doc = tree / "usr/share/doc/moltskin-linux-preview"
    doc.mkdir(parents=True)
    # A standalone build can supply Chromium's LICENSE next to the binary.
    license_file = build / "LICENSE"
    if not license_file.exists() and build.parent.name == "out":
        license_file = build.parent.parent / "LICENSE"
    chromium_license = license_file.read_text()
    shutil.copyfile(ROOT / "LICENSE", doc / "LICENSE.Moltskin")
    credits = build / "gen/components/resources/about_credits.html"
    if credits.is_file() and "This is sample credits page." not in credits.read_text():
        shutil.copyfile(credits, doc / "chromium-credits.html")
        (doc / "chromium-credits.html").chmod(0o644)
    (doc / "copyright").write_text(
        "Moltskin patches: Copyright 2026 Moltskin contributors, see LICENSE.Moltskin.\n"
        "Chromium and bundled components: full credits are available at chrome://credits "
        "in builds made with generate_about_credits=true.\n\n" + chromium_license
    )
    control = tree / "DEBIAN/control"
    control.parent.mkdir()
    control.write_text(
        f"Package: moltskin-linux-preview\nVersion: {version}-1\nArchitecture: amd64\n"
        f"Maintainer: Moltskin contributors <noreply@github.com>\nDepends: {DEPENDS}\n"
        "Description: Moltskin Chromium local preview for Ubuntu 24.04\n"
    )
    # Builder workspaces may use umask 077. Installed runtime paths must be traversable.
    for path in tree.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
    tree.chmod(0o755)
    for path in (profile, control, doc / "copyright", doc / "LICENSE.Moltskin"):
        path.chmod(0o644)


def package(build, destination):
    build = build.resolve(strict=True)
    destination = destination.absolute()
    if destination.suffix != ".deb" or destination.is_symlink():
        raise ValueError("choose a .deb output file, not a symlink")
    version = verify_build(build)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        workspace = Path(temp)
        tree = workspace / "package"
        stage(build, tree, version)
        archive = workspace / "preview.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zzstd", "-z3", "--build", tree, archive], check=True)
        os.replace(archive, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_output", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() == 0:
        parser.error("package the Linux build as a regular user on Ubuntu")
    try:
        package(args.build_output, args.destination)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Package build failed: {exc}\n")
    print(args.destination.absolute())


if __name__ == "__main__":
    main()
