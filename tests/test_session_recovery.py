"""Reconcile persisted sessions after the original supervisor has exited."""

import json
import os
import signal
from unittest.mock import Mock

import pytest

from moltskin.services.core import config, db, launcher


@pytest.fixture
def session_row(data_dir):
    db.init_db()

    def make(engine="camoufox", state="running", pid=None):
        sid = "ss_123456789abc"
        with db.session() as conn:
            conn.execute(
                "INSERT INTO profiles(id,name,user_data_dir,engine,status,last_session_id,created_at,updated_at) "
                "VALUES ('pf_test','test',?,?,'running',?,?,?)",
                (str(data_dir / "profiles/pf_test"), engine, sid, db.now(), db.now()),
            )
            conn.execute("INSERT INTO sessions(id,profile_id,state,pid,started_at) VALUES (?,'pf_test',?,?,?)",
                         (sid, state, pid, db.now()))
        return sid

    return make


def saved_state(sid):
    with db.session() as conn:
        return dict(conn.execute("SELECT state,error_code,ended_at FROM sessions WHERE id=?", (sid,)).fetchone())


@pytest.mark.parametrize(("report", "session_state", "profile_status", "error"), [
    ({"state": "stopped"}, "stopped", "ready", None),
    ({"state": "failed", "error_code": "LAUNCH_FAILED", "error_message": "test"}, "failed", "crashed", "LAUNCH_FAILED"),
    ({"state": "running"}, "crashed", "crashed", "ORPHAN"),
])
def test_camoufox_terminal_report_survives_service_restart(session_row, report, session_state, profile_status, error):
    sid = session_row()
    (config.RUN_DIR / f"{sid}.json").write_text(json.dumps(report))
    assert launcher.cleanup_orphans() == 1
    assert launcher.status("pf_test")["status"] == profile_status
    result = saved_state(sid)
    assert result["state"] == session_state and result["error_code"] == error
    assert result["ended_at"]
    assert launcher.cleanup_orphans() == 0
    assert launcher.stop(sid)["state"] == session_state
    assert saved_state(sid) == result


def test_chromium_disappearance_without_exit_code_is_inconclusive(session_row):
    sid = session_row("chromium")
    assert launcher.status("pf_test")["status"] == "ready"
    result = saved_state(sid)
    assert (result["state"], result["error_code"]) == ("stopped", "EXIT_UNCONFIRMED")
    # A poll can briefly win the race with the original child watcher.
    launcher._monitor(sid, "pf_test", Mock(wait=Mock(return_value=9)), None)
    assert saved_state(sid)["state"] == "crashed"
    assert saved_state(sid)["ended_at"] == result["ended_at"]


def test_stop_returns_saved_terminal_state_for_exited_browser(session_row):
    sid = session_row(state="stopping")
    result = launcher.stop(sid)
    assert result["state"] == saved_state(sid)["state"] == "stopped"
    assert launcher.stop(sid) == result


def test_reused_pid_cannot_receive_stop_signal(session_row, monkeypatch):
    sid = session_row(pid=os.getpid())
    signals = []
    monkeypatch.setattr(launcher.os, "kill", lambda pid, sig: signals.append(sig))
    assert launcher.stop(sid)["state"] == "crashed"
    assert signals and all(sig == 0 for sig in signals)


def test_recover_only_the_owned_camoufox_main_with_a_spaced_data_path(session_row, monkeypatch, data_dir):
    sid = session_row()
    (config.RUN_DIR / f"{sid}.json").write_text('{"state":"stopped"}')
    assert launcher.cleanup_orphans() == 1
    root = data_dir / "data files" / "profiles"
    monkeypatch.setattr(config, "PROFILES_DIR", root)
    directory = str(root / "pf_test")
    with db.session() as conn:
        conn.execute("UPDATE profiles SET user_data_dir=? WHERE id='pf_test'", (directory,))
    args = {
        "91": f"/opt/Camoufox Browser/camoufox -no-remote -profile {directory} -juggler-pipe about:blank",
        "92": f"/opt/sys/other -profile {directory} -juggler-pipe",
        "93": f"/opt/Camoufox Browser/camoufox -profile {directory}-other -juggler-pipe",
        "94": f"/opt/Camoufox Browser/camoufox -contentproc -profile {directory} 1",
    }

    def inspect_ps(argv, **kwargs):
        if "-axo" in argv:
            return "\n".join(f"{pid} {command}" for pid, command in args.items())
        pid = argv[argv.index("-p") + 1]
        if argv[-1] == "comm=":
            return "/opt/sys/other" if pid == "92" else "/opt/Camoufox Browser/camoufox"
        return args[pid]

    signals = []
    monkeypatch.setattr(launcher.subprocess, "check_output", inspect_ps)
    monkeypatch.setattr(launcher.os, "kill", lambda pid, sig: signals.append(pid))
    assert launcher.cleanup_browser_orphans() == 1
    assert signals == [91]


@pytest.mark.parametrize(("exit_code", "state"), [(-signal.SIGINT, "stopped"), (-signal.SIGKILL, "killed")])
def test_requested_shutdown_signals_are_terminal_not_crashes(session_row, exit_code, state):
    sid = session_row("chromium", state="stopping")
    launcher._monitor(sid, "pf_test", Mock(wait=Mock(return_value=exit_code)), None)
    assert saved_state(sid)["state"] == state
    assert launcher.status("pf_test")["status"] == "ready"
