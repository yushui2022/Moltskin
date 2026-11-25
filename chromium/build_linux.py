"""Build the pinned Moltskin Chromium for native x86-64 Linux.

With depot_tools on PATH, run:
    python3 chromium/build_linux.py ~/chromium-baseline --install-deps
    python3 chromium/build_linux.py ~/chromium-baseline --prepared --jobs 4
"""

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

if __package__:
    from . import native
else:
    import native


MODULE = Path(__file__).resolve().parent
SOURCE_URL = "https://chromium.googlesource.com/chromium/src.git"
OUTPUT = Path("out/MoltskinLocation")


def apply_series(src, patches):
    """Fold overlapping patches into a single atomic git apply; leave the real index alone."""
    if not patches:
        raise ValueError("The Moltskin patch series is missing")

    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        subprocess.run(["git", "read-tree", "HEAD"], cwd=src, env=env, check=True)
        for patch in patches:
            subprocess.run(["git", "apply", "--cached", str(patch)], cwd=src, env=env, check=True)
        delta = subprocess.check_output(
            ["git", "diff", "--cached", "--binary", "--no-renames", "--no-ext-diff", "--no-textconv", "HEAD"],
            cwd=src, env=env,
        )
    if not delta:
        raise ValueError("The Moltskin patch series produces no changes")
    already = subprocess.run(["git", "apply", "--reverse", "--check", "-"], cwd=src,
                             input=delta, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if already.returncode == 0:
        print("Moltskin patches are already present.", flush=True)
        return
    # git apply checks all hunks before updating the working tree.
    subprocess.run(["git", "apply", "-"], cwd=src, input=delta, check=True)


def check_revision(src, pin):
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=src, text=True).strip()
    if head != pin:
        raise ValueError(f"{src} is at {head}, expected {pin}. Select a separate checkout for this release.")


def build(checkout, *, jobs, install_deps, prepared):
    pin = json.loads((MODULE / "upstream.json").read_text())
    src = checkout / "src"
    gclient = checkout / ".gclient"
    if not gclient.is_file():
        if prepared:
            raise ValueError("--prepared requires a previously synced gclient checkout")
        if any(entry.name != ".moltskin-build.lock" for entry in checkout.iterdir()):
            raise ValueError("Use an empty directory or an existing Chromium gclient checkout")
        subprocess.run(["gclient", "config", "--name=src", SOURCE_URL], cwd=checkout, check=True)
    if src.exists():
        if not (src / ".git").exists():
            raise ValueError(f"{src} is not a Git checkout")
        check_revision(src, pin["commit"])
    if not prepared:
        subprocess.run(["gclient", "sync", "--no-history", "--nohooks", "--revision", "src@" + pin["commit"]],
                       cwd=checkout, check=True)
    check_revision(src, pin["commit"])
    if install_deps:
        subprocess.run([str(src / "build/install-build-deps.sh")], cwd=src, check=True)
    if not prepared:
        subprocess.run(["gclient", "runhooks"], cwd=src, check=True)

    output = src / OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    args = output / "args.gn"
    settings = (MODULE / "args.gn").read_bytes()
    if args.exists() and args.read_bytes() != settings:
        raise ValueError(f"Build settings differ at {args}. Review your local args.gn before rebuilding.")
    apply_series(src, sorted((MODULE / "patches").glob("*.patch")))
    if not args.exists():
        args.write_bytes(settings)
    subprocess.run(["gn", "gen", str(OUTPUT)], cwd=src, check=True)
    subprocess.run(["autoninja", "-C", str(OUTPUT), "-j", str(jobs), "chrome"], cwd=src, check=True)
    binary, version, capabilities = native.probe_capabilities(output / "chrome")
    if version != pin["version"]:
        raise ValueError(f"{binary} reports {version}, expected {pin['version']}")
    missing = native.DESKTOP_CAPABILITIES - capabilities
    if missing:
        raise ValueError(f"{binary} is missing: {', '.join(sorted(missing))}")
    print(f"Verified Moltskin Chromium {version}\n{binary}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Build the pinned Moltskin Chromium for native x86-64 Linux.")
    parser.add_argument("checkout", type=Path, help="empty directory or gclient checkout containing src/")
    parser.add_argument("--jobs", type=int, default=4, help="parallel compile jobs (default: 4)")
    parser.add_argument("--install-deps", action="store_true", help="run Chromium's Linux system dependency installer")
    parser.add_argument("--prepared", action="store_true",
                        help="reuse an already synced checkout with completed gclient hooks")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        parser.error("run this build on x86-64 Linux")
    import fcntl
    for tool in ("git", "gn", "autoninja", *(() if args.prepared else ("gclient",))):
        if not shutil.which(tool):
            parser.error(f"{tool} is missing from PATH; install git and depot_tools")
    checkout = args.checkout.expanduser().resolve()
    if any(c.isspace() for c in str(checkout)):
        parser.error("Chromium needs a checkout path without spaces")
    try:
        checkout.mkdir(parents=True, exist_ok=True)
        with (checkout / ".moltskin-build.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                sys.exit(f"Another Moltskin build is using {checkout}")
            build(checkout, jobs=args.jobs, install_deps=args.install_deps, prepared=args.prepared)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        sys.exit(f"Linux build failed: {exc}")


if __name__ == "__main__":
    main()
