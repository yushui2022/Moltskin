"""Validate that the native build can resume on a patched tree without touching the user's index."""

import json
import shutil
import subprocess

import pytest

from chromium import build_linux


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo)


@pytest.fixture
def series(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    git(src, "init", "-q")
    original = "upstream\n"
    first = src / "first"
    second = src / "second"
    first.write_text(original)
    second.write_text(original)
    git(src, "add", ".")
    git(src, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-qm", "upstream")
    first.write_text("first patch\n")
    header = src / "header"
    header.write_text("define A\n")
    git(src, "add", ".")
    patch1 = tmp_path / "0001.patch"
    patch1.write_bytes(git(src, "diff", "--cached"))
    first.write_text("second patch\n")
    header.write_text("define A and B\n")
    second.write_text("also patched\n")
    patch2 = tmp_path / "0002.patch"
    patch2.write_bytes(git(src, "diff"))
    git(src, "reset", "--hard", "-q", "HEAD")
    return src, [patch1, patch2]


def test_series_rerun_preserves_staged_work_and_new_header(series):
    src, patches = series
    staged = src / "user-note"
    staged.write_text("my change")
    git(src, "add", staged.name)
    before = git(src, "diff", "--cached")
    for _ in range(2):
        build_linux.apply_series(src, patches)
        assert (src / "first").read_text() == "second patch\n"
        assert (src / "header").read_text() == "define A and B\n"
        assert (src / "second").read_text() == "also patched\n"
        assert git(src, "diff", "--cached") == before


def test_conflict_leaves_all_original_files_intact(series):
    src, patches = series
    (src / "second").write_text("user change\n")
    with pytest.raises(subprocess.CalledProcessError):
        build_linux.apply_series(src, patches)
    assert (src / "first").read_text() == "upstream\n"
    assert (src / "second").read_text() == "user change\n"
    assert not (src / "header").exists()


def test_series_upgrades_already_installed_prefix(series):
    src, patches = series
    build_linux.apply_series(src, patches[:1])
    build_linux.apply_series(src, patches)
    assert (src / "first").read_text() == "second patch\n"
    assert (src / "second").read_text() == "also patched\n"
    assert git(src, "diff", "--cached") == b""


def test_wrong_prepared_revision_rejected_before_sync_or_patching(series):
    src, patches = series
    (src.parent / ".gclient").touch()
    with pytest.raises(ValueError, match="separate checkout"):
        build_linux.build(src.parent, jobs=4, install_deps=False, prepared=True)
    assert git(src, "diff") == b""
    assert not (src / build_linux.OUTPUT).exists()


def test_build_from_empty_then_resume_prepared_checkout(series, tmp_path, monkeypatch, capsys):
    upstream, patches = series
    revision = git(upstream, "rev-parse", "HEAD").decode().strip()
    module = tmp_path / "module"
    (module / "patches").mkdir(parents=True)
    (module / "upstream.json").write_text(json.dumps({"version": "1.2.3.4", "commit": revision}))
    (module / "args.gn").write_text("is_debug = false\n")
    for patch in patches:
        shutil.copy(patch, module / "patches")
    monkeypatch.setattr(build_linux, "MODULE", module)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    commands = []
    run = subprocess.run

    def build_tool(argv, **kwargs):
        if argv[0] not in {"gclient", "gn", "autoninja"}:
            return run(argv, **kwargs)
        commands.append(argv)
        if argv[:2] == ["gclient", "config"]:
            (checkout / ".gclient").touch()
        elif argv[:2] == ["gclient", "sync"]:
            run(["git", "clone", "-q", str(upstream), str(checkout / "src")], check=True)
        elif argv[0] == "autoninja":
            binary = checkout / "src" / build_linux.OUTPUT / "chrome"
            reports = ["Chromium 1.2.3.4", *sorted(build_linux.native.DESKTOP_CAPABILITIES)]
            binary.write_text("#!/bin/sh\n" + "\n".join("echo " + repr(line) for line in reports) + "\n")
            binary.chmod(0o755)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", build_tool)
    build_linux.build(checkout, jobs=3, install_deps=False, prepared=False)
    assert [c[:2] for c in commands] == [
        ["gclient", "config"], ["gclient", "sync"], ["gclient", "runhooks"], ["gn", "gen"], ["autoninja", "-C"],
    ]
    assert commands[1][-1] == "src@" + revision
    assert commands[-1][-3:] == ["-j", "3", "chrome"]
    commands.clear()
    build_linux.build(checkout, jobs=2, install_deps=False, prepared=True)
    assert [c[0] for c in commands] == ["gn", "autoninja"]
    assert "Verified Moltskin Chromium 1.2.3.4" in capsys.readouterr().out
