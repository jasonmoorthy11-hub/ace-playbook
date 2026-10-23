"""Hermetic defaults for every test: nothing under `tests/` may reach the operator's real config file or chain key.

Before the key default was removed, a config file written without `chain_key_path` made `config.load` fill in the real default under
the user's config directory, and the first `compile keygen` on the developer's Mac turned six unrelated tests red (their tmp approvals
chains were read as keyed-without-heads). Now `config.load` implies no key at all (tests/test_keypath.py, tests/test_hermetic.py), so the
only ambient input left is the config file itself: `ACE_PLAYBOOK_CONFIG` is pinned to a missing file per test.
`tests.test_hooks.env` gives child processes a throwaway HOME for the same reason.
"""

from __future__ import annotations

import math
import signal
import threading
from pathlib import Path

import pytest

from ace_playbook import config as cfgmod

FAKE_READER = Path(__file__).resolve().parent / "fixtures" / "watch" / "fake_reader.py"


@pytest.fixture(autouse=True)
def hermetic_config(tmp_path_factory, monkeypatch):
    """A test that forgets to name its config file fails loudly (`ACE_PLAYBOOK_CONFIG` → a missing file) instead of reading the operator's;
    the default location points at the same missing file, so a test that unsets the variable cannot see the real config or the heads beside it,
    and the default data dir is a missing tmp dir, so a bare `Config()` cannot read the operator's chains (`key_of` reads them)."""
    base = tmp_path_factory.mktemp("no-operator-config")
    path = base / "no-config.json"
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(path))
    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG", path)
    monkeypatch.setattr(cfgmod, "DEFAULT_DATA", base / "no-data")
    yield path


@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):
    """The watcher's address guard resolves host names; no test may reach a real resolver. Every name answers with one public address
    (a test of the guard replaces `netguard.RESOLVE` itself)."""
    from ace_playbook.compile.watch import netguard
    monkeypatch.setattr(netguard, "RESOLVE", lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])


@pytest.fixture(autouse=True)
def fake_reader_claude(monkeypatch):
    """The watcher's Reader runs `$ACE_WATCH_CLAUDE`, else `watch_claude`, else `claude` from the PATH: every test gets the fake reader as
    the first, so no test can reach the real binary (tests/test_watch_reader.py has the `fake` fixture for the tests that read its calls)."""
    monkeypatch.setenv("ACE_WATCH_CLAUDE", str(FAKE_READER))


@pytest.fixture(autouse=True)
def fake_notifier(tmp_path_factory, monkeypatch):
    """`hooks/brief_lib.sh` notify runs $ACE_BRIEF_NOTIFIER (else `terminal-notifier` from the PATH, /opt/homebrew/bin included): every test
    gets a recording fake, so no run of brief.sh from a test can open the operator's real notification. Yields the log of its argv."""
    base = tmp_path_factory.mktemp("fake-notifier")
    log, script = base / "argv.log", base / "terminal-notifier"
    script.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" >> "{log}"\n')
    script.chmod(0o755)
    monkeypatch.setenv("ACE_BRIEF_NOTIFIER", str(script))
    yield log


TIMEOUT_SECONDS = 60         # per test; `@pytest.mark.timeout(n)` overrides it (seconds, may be fractional)


class PerTestTimeout(BaseException):
    """Raised by the per-test alarm. A BaseException on purpose: the fail-open paths (`catalogue.evaluate` non-strict, `modes.read_*`,
    `shadow.py`) catch `Exception`, and a swallowed timeout would let the loop resume with nothing left to stop it."""


def timeout_seconds(arg) -> float:
    """The seconds of a `@pytest.mark.timeout(arg)`: a positive finite number (not a bool); ValueError otherwise (`inf` would never fire)."""
    if isinstance(arg, bool) or not isinstance(arg, (int, float)) or not (arg > 0 and math.isfinite(arg)):
        raise ValueError("timeout marker must be a positive finite number")
    return float(arg)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    yield
    if call.excinfo is not None and call.excinfo.errisinstance(PerTestTimeout):
        item.timeout_surfaced = True                       # the timeout reached a report (any phase): teardown has nothing to add
        if hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGALRM, signal.SIG_IGN)  # it has surfaced: the interval timer must not re-fire into the other fixtures' teardown
            signal.setitimer(signal.ITIMER_REAL, 0)


@pytest.fixture(autouse=True)
def per_test_timeout(request):
    """An infinite loop (the mutation run found two) fails its test instead of hanging the suite. Stdlib only.

    A SIGALRM handler raises `PerTestTimeout` in the main thread. The interval timer re-fires every second, so a handler call that a
    `except BaseException` swallows is followed by another; if the test still passes after a firing, teardown fails it
    ("per-test timeout fired"). The hard stop is pytest's own `faulthandler_timeout` / `faulthandler_exit_on_timeout` (pyproject.toml,
    125 s): it dumps every thread's stack and ends the run. The alarm is armed before the test body, so it fires before a child process's
    own `timeout=`, which only matters for a hung child: `subprocess.run` kills the child on any exception. Skipped where there is no
    SIGALRM (Windows) or off the main thread; the timer is disarmed as soon as the alarm has surfaced in a report
    (`pytest_runtest_makereport`, so it cannot re-fire into teardown) and again, with the previous handler restored, in `finally`."""
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        yield
        return
    marker = request.node.get_closest_marker("timeout")
    seconds = TIMEOUT_SECONDS
    if marker is not None:
        try:
            seconds = timeout_seconds(marker.args[0] if marker.args else None)
        except ValueError as exc:
            pytest.fail(str(exc))
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
