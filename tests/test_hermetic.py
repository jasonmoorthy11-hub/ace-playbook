"""The suite must stay green whether or not the developer's machine holds a chain key (see tests/conftest.py)."""

import dataclasses
import json
import os
import signal

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.compile import catalogue, events
from ace_playbook.compile import state as state_mod

from .conftest import PerTestTimeout
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


def test_every_test_runs_under_the_default_re_arming_alarm_of_60_seconds():
    assert signal.getsignal(signal.SIGALRM).__name__ == "expired"            # the handler installed by conftest.per_test_timeout
    delay, interval = signal.getitimer(signal.ITIMER_REAL)
    assert 0 < delay <= 60 and interval == 1.0                               # fires again every second if a firing is swallowed


@pytest.mark.timeout(1)
@pytest.mark.xfail(strict=True, raises=PerTestTimeout, reason="proof of conftest.per_test_timeout: an infinite loop fails after 1 s")
def test_an_infinite_loop_fails_with_the_timeout_instead_of_hanging():
    while True:
        pass


@pytest.mark.timeout(1)
@pytest.mark.xfail(strict=True, raises=PerTestTimeout, reason="proof: the fail-open path (non-strict evaluate, `except Exception`) cannot swallow it")
def test_a_looping_detector_under_the_non_strict_evaluate_still_fails_with_the_timeout(monkeypatch):
    def loop(event, state):
        while True:
            pass

    row = catalogue.CATALOGUE["rm_rf"]
    monkeypatch.setitem(catalogue.CATALOGUE, "rm_rf", dataclasses.replace(row, fire=loop))
    catalogue.evaluate(events.Event(tier="live"), state_mod.new_state("s"), names=["rm_rf"], strict=False)
