"""The data root holds proxy credentials and must stay account-private."""

import stat

from moltskin.services.core import config


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_runtime_directories_are_private(data_dir):
    config.ensure_dirs()

    for path in (config.DATA_DIR, config.PROFILES_DIR, config.RUN_DIR,
                 config.LOG_DIR, config.SELFTEST_DIR):
        assert mode(path) == 0o700, path


def test_existing_world_readable_data_root_is_tightened(data_dir):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.DATA_DIR.chmod(0o755)

    config.ensure_dirs()

    assert mode(config.DATA_DIR) == 0o700
