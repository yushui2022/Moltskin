"""Restore configuration backups into a different data root."""

import io
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from moltskin.services.core import backup, config, db


def fixture_archive(source: Path, ids, *, legacy=False):
    source.mkdir()
    database = source / "source.db"
    with closing(sqlite3.connect(database)) as c, c:
        c.executescript(db.SCHEMA)
        for i, ident in enumerate(ids):
            c.execute(
                "INSERT INTO profiles(id,name,engine,user_data_dir,local_fonts,status,last_session_id,created_at,updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)",
                (ident, f"profile {i}", "camoufox" if legacy or i != 1 else "chromium",
                 str(source / "profiles" / ident), "restricted" if not legacy and i == 1 else "standard",
                 f"current{i}", db.now(), db.now()),
            )
            c.execute(
                "INSERT INTO sessions(id,profile_id,pid,state,started_at) VALUES (?,?,100,'running',?)",
                (f"current{i}", ident, db.now()),
            )
        if ids:
            c.execute(
                "INSERT INTO sessions(id,profile_id,pid,state,started_at,ended_at) "
                "VALUES ('old-completed', ?, 456, 'stopped', 'yesterday', 'then')", (ids[0],),
            )
        if legacy:
            c.execute("ALTER TABLE profiles DROP COLUMN local_fonts")
            c.execute("ALTER TABLE profiles DROP COLUMN engine")
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as zf:
        zf.write(database, backup.DB_NAME)
    return result.getvalue()


@pytest.mark.parametrize("legacy, ids", [(True, ["old-id"]), (False, ["pf_fox", "pf_chrome"])])
def test_cross_root_restore_rebases_paths_and_disarms_stale_sessions(data_dir, tmp_path, legacy, ids):
    db.init_db()
    archive = fixture_archive(tmp_path / "source", ids, legacy=legacy)
    with closing(db.connect()) as live:
        # Hold this connection open so the newest committed row still lives in the WAL.
        live.execute("PRAGMA wal_autocheckpoint=0")
        live.execute("INSERT INTO groups (id,name,created_at,updated_at) VALUES ('previous','before restore',?,?)",
                     (db.now(), db.now()))
        live.commit()
        assert Path(str(config.DB_PATH) + "-wal").stat().st_size > 0
        result = backup.restore(archive)
        assert live.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == len(ids)
        assert not live.execute("SELECT 1 FROM groups WHERE id='previous'").fetchone()
    with closing(sqlite3.connect(result["backup_path"])) as before:
        assert before.execute("SELECT name FROM groups WHERE id='previous'").fetchone() == ("before restore",)
    db.init_db()
    with db.session() as c:
        rows = c.execute("SELECT * FROM profiles ORDER BY name").fetchall()
        assert [Path(row["user_data_dir"]) for row in rows] == [config.PROFILES_DIR / ident for ident in ids]
        assert all(row["status"] == "ready" and row["last_session_id"] is None for row in rows)
        if not legacy:
            assert (rows[1]["engine"], rows[1]["local_fonts"]) == ("chromium", "restricted")
        sessions = c.execute("SELECT * FROM sessions WHERE id LIKE 'current%'").fetchall()
        assert len(sessions) == len(ids)
        assert all(row["pid"] is None and row["state"] == "stopped" and row["ended_at"] for row in sessions)
        assert tuple(c.execute("SELECT pid, state, ended_at FROM sessions WHERE id='old-completed'").fetchone()) == (
            456, "stopped", "then",
        )


@pytest.mark.parametrize("unsafe_id", ["..", "../other", r"sub\other", "C:escape"])
def test_invalid_id_cannot_replace_live_database(data_dir, tmp_path, unsafe_id):
    db.init_db()
    archive = fixture_archive(tmp_path / "source", ["ordinary", unsafe_id])
    with pytest.raises(ValueError, match="环境 ID"):
        backup.restore(archive)
    with db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
    assert not config.DB_PATH.with_suffix(".db.bak").exists()


def test_profile_symlink_cannot_redirect_restored_environment(data_dir, tmp_path):
    db.init_db()
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (config.PROFILES_DIR / "moved").symlink_to(outside, target_is_directory=True)
    archive = fixture_archive(tmp_path / "source", ["moved"])
    with pytest.raises(ValueError, match="环境目录链接"):
        backup.restore(archive)
    assert list(outside.iterdir()) == []
    assert not config.DB_PATH.with_suffix(".db.bak").exists()
