"""Offline pytest fixtures. Live-service checks run via their script entry points."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

collect_ignore = ["test_m0.py", "test_edge.py", "test_e2e.py", "test_runtime.py"]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MOLTSKIN_DATA_DIR", str(tmp_path))
    from moltskin.services.core import config

    for name in ("DB_PATH", "PROFILES_DIR", "RUN_DIR", "LOG_DIR", "SELFTEST_DIR", "TOKEN_PATH"):
        monkeypatch.setattr(config, name, tmp_path / getattr(config, name).name)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path
