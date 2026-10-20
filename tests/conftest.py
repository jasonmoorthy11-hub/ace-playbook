"""Hermetic defaults for every test: nothing under `tests/` may reach the operator's real chain key or config file.

Before this fixture existed the suite was green only while no chain key existed on the machine: a config file written without
`chain_key_path` made `config.load` fill in the real default under `~/.config/ace-playbook`, and the first `compile keygen` on the
developer's Mac turned six unrelated tests red (their tmp approvals chains were read as keyed-without-heads). CI never saw it.
`tests.test_hooks.env` gives child processes a throwaway HOME for the same reason. A test that checks the production default
uses `PRODUCTION_CHAIN_KEY` (captured here before any patch) so the literal stays pinned (tests/test_compile_keyed.py).
"""

from __future__ import annotations

import signal
import threading

import pytest

from ace_playbook import config as cfgmod

PRODUCTION_CHAIN_KEY = cfgmod.DEFAULT_CHAIN_KEY          # captured at import, before the autouse patch below


@pytest.fixture(autouse=True)
def hermetic_chain_key(tmp_path_factory, monkeypatch):
    """`config.load` of a file without `chain_key_path` resolves to a per-test path that must never come to exist, and a test that
    forgets to name its config file fails loudly instead of reading the operator's (`ACE_PLAYBOOK_CONFIG` → a missing file)."""
    base = tmp_path_factory.mktemp("no-operator-key")
    path = base / "chain_key"
    monkeypatch.setattr(cfgmod, "DEFAULT_CHAIN_KEY", str(path))
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(base / "no-config.json"))
    yield path
    assert not path.exists(), "a test created the redirected default chain key; it must set chain_key_path explicitly"


TIMEOUT_SECONDS = 60          # per test; `@pytest.mark.timeout(n)` overrides it (seconds, may be fractional)


class PerTestTimeout(BaseException):
    """Raised by the per-test alarm. A BaseException on purpose: the fail-open paths (`catalogue.evaluate` non-strict, `modes.read_*`,
    `shadow.py`) catch `Exception`, and a swallowed timeout would let the loop resume with nothing left to stop it."""


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    yield
    if call.excinfo is not None and call.excinfo.errisinstance(PerTestTimeout):
        item.timeout_surfaced = True                       # the timeout reached a report (any phase): teardown has nothing to add


@pytest.fixture(autouse=True)
def per_test_timeout(request):
    """An infinite loop (the mutation run found two) fails its test instead of hanging the suite. Stdlib only.

    A SIGALRM handler raises `PerTestTimeout` in the main thread. The interval timer re-fires every second, so a handler call that a
    `except BaseException` swallows is followed by another; if the test still passes after a firing, teardown fails it
    ("per-test timeout fired"). The hard stop is pytest's own `faulthandler_timeout` / `faulthandler_exit_on_timeout` (pyproject.toml,
    125 s): it dumps every thread's stack and ends the run. The alarm is armed before the test body, so it fires before a child process's
    own `timeout=`, which only matters for a hung child: `subprocess.run` kills the child on any exception. Skipped where there is no
    SIGALRM (Windows) or off the main thread; the timer is disarmed and the previous handler restored in `finally`."""
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        yield
        return
    marker = request.node.get_closest_marker("timeout")
    seconds = TIMEOUT_SECONDS
    if marker is not None:
        arg = marker.args[0] if marker.args else None
        if isinstance(arg, bool) or not isinstance(arg, (int, float)) or not arg > 0:
            pytest.fail("timeout marker must be a positive number")
        seconds = float(arg)
    fired = []

    def expired(signum, frame):
        fired.append(signum)
        raise PerTestTimeout(f"test exceeded {seconds} s")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds, 1.0)
    try:
        yield
    finally:
        signal.signal(signal.SIGALRM, signal.SIG_IGN)       # first: a firing pending at disarm time must not raise in here
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, signal.SIG_DFL if previous is None else previous)
    if fired and not getattr(request.node, "timeout_surfaced", False):
        pytest.fail(f"per-test timeout fired after {seconds} s but the test finished: something swallowed it")
