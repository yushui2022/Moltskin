"""Backup archives are untrusted input: validate before extracting."""

import io
import shutil
import sqlite3
import zipfile
from contextlib import closing

import pytest

from moltskin.services.core import backup, config, db


def make_database(path):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(db.SCHEMA)


def make_archive(database, extra=()):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.write(database, backup.DB_NAME)
        for name, data in extra:
            zf.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "member",
    [
        "../escaped.txt",
        "profiles/../../escaped.txt",
        "/tmp/escaped.txt",
        "..\\escaped.txt",
        "C:/escaped.txt",
        "profiles//escaped.txt",
    ],
)
def test_restore_rejects_members_outside_the_archive(data_dir, tmp_path, monkeypatch, member):
    db.init_db()
    source = tmp_path / "source.db"
    make_database(source)
    archive = make_archive(source, [(member, b"escaped")])
    workdir = tmp_path / "restore"
    monkeypatch.setattr(backup.tempfile, "mkdtemp", lambda *args, **kwargs: str(workdir))

    with pytest.raises(ValueError, match="备份包"):
        backup.restore(archive)

    assert not (tmp_path / "escaped.txt").exists()
    assert not config.DB_PATH.with_suffix(".db.bak").exists()
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0


def test_restore_rejects_an_archive_over_the_uncompressed_limit(data_dir, tmp_path, monkeypatch):
    db.init_db()
    source = tmp_path / "source.db"
    make_database(source)
    archive = make_archive(source)
    monkeypatch.setattr(backup, "MAX_BACKUP_UNCOMPRESSED", 32)

    with pytest.raises(ValueError, match="超过大小限制"):
        backup.restore(archive)

    assert not config.DB_PATH.with_suffix(".db.bak").exists()


def test_restore_rejects_too_many_members(data_dir, tmp_path, monkeypatch):
    db.init_db()
    source = tmp_path / "source.db"
    make_database(source)
    archive = make_archive(source, [("extensions/a", b"a")])
    monkeypatch.setattr(backup, "MAX_BACKUP_MEMBERS", 1)

    with pytest.raises(ValueError, match="条目过多"):
        backup.restore(archive)

    assert not config.DB_PATH.with_suffix(".db.bak").exists()


def test_backup_skips_symlinks_outside_the_data_root(data_dir, tmp_path):
    db.init_db()
    with db.session() as conn:
        conn.execute(
            "INSERT INTO profiles(id,name,engine,user_data_dir,status,created_at,updated_at) "
            "VALUES ('fox','Fox','camoufox',?,'ready',?,?)",
            (str(config.profile_directory("fox")), db.now(), db.now()),
        )
    base = config.profile_directory("fox")
    (base / "storage").mkdir(parents=True)
    secret = tmp_path / "outside-secret"
    secret.write_text("do not archive")
    (base / "storage" / "leak").symlink_to(secret)
    extensions = config.DATA_DIR / "extensions" / "leak"
    extensions.mkdir(parents=True)
    (extensions / "leak").symlink_to(secret)

    archive = backup.create(include_profiles=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
    finally:
        shutil.rmtree(archive.parent, ignore_errors=True)

    assert "profiles/fox/storage/leak" not in names
    assert "extensions/leak/leak" not in names


def test_invalid_recycled_json_is_a_bad_backup(data_dir, tmp_path):
    db.init_db()
    source = tmp_path / "source.db"
    make_database(source)
    with closing(sqlite3.connect(source)) as conn, conn:
        conn.execute(
            "INSERT INTO trashed_profiles (id,name,engine,trashed_at,profile_json,sessions_json,extensions_json) "
            "VALUES (?,?,?,?,?,?,?)",
            ("recycled", "Saved", "camoufox", db.now(), "{not json", "[]", "[]"),
        )
    archive = make_archive(source)

    with pytest.raises(ValueError, match="配置无效"):
        backup.restore(archive)

    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trashed_profiles").fetchone()[0] == 0
    assert not config.DB_PATH.with_suffix(".db.bak").exists()
