"""The suite must stay green whether or not the developer's machine holds a chain key (see tests/conftest.py)."""

import json
import os
import signal

import pytest

from ace_playbook import config as cfgmod

from .test_hooks import env  # noqa: F401  (fixture)


def test_a_config_file_without_chain_key_path_never_resolves_to_the_real_key(tmp_path, hermetic_chain_key):
    conf = tmp_path / "c.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "data")}))
    cfg = cfgmod.load(conf)
    assert cfg.chain_key_path == hermetic_chain_key and not cfg.chain_key_path.exists()
    assert ".config" not in cfg.chain_key_path.parts


def test_child_processes_get_a_throwaway_home(env):  # noqa: F811
    e, _ = env
    assert e["HOME"] != os.environ.get("HOME") and os.path.isdir(e["HOME"])
    assert not os.path.exists(os.path.join(e["HOME"], ".config"))


def test_every_test_runs_under_the_default_alarm_of_60_seconds():
    assert signal.getsignal(signal.SIGALRM) is not signal.SIG_DFL
    remaining = signal.alarm(0)                                       # disarms it for the rest of this test; the fixture re-checks at teardown
    assert 0 < remaining <= 60


@pytest.mark.timeout(1)
@pytest.mark.xfail(strict=True, raises=TimeoutError, reason="proof of tests/conftest.py per_test_timeout: an infinite loop fails after 1 s")
def test_an_infinite_loop_fails_with_timeout_error_instead_of_hanging():
    while True:
        pass
