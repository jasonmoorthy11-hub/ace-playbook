"""Hermetic defaults for every test: nothing under `tests/` may reach the operator's real chain key or config file.

Before this fixture existed the suite was green only while no chain key existed on the machine: a config file written without
`chain_key_path` made `config.load` fill in the real default under `~/.config/ace-playbook`, and the first `compile keygen` on the
developer's Mac turned six unrelated tests red (their tmp approvals chains were read as keyed-without-heads). CI never saw it.
`tests.test_hooks.env` gives child processes a throwaway HOME for the same reason. A test that checks the production default
uses `PRODUCTION_CHAIN_KEY` (captured here before any patch) so the literal stays pinned (tests/test_compile_keyed.py).
"""

from __future__ import annotations

import math
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


TIMEOUT_SECONDS = 60          # per test; `@pytest.mark.timeout(n)` overrides it (whole seconds, `signal.alarm` has no finer grain)


@pytest.fixture(autouse=True)
def per_test_timeout(request):
    """An infinite loop (the mutation run found two) fails its test with `TimeoutError` instead of hanging the suite.

    Stdlib only: a SIGALRM handler raises in the main thread. It is skipped where there is no SIGALRM (Windows) or the test does not run
    on the main thread, and the previous handler is restored. Subprocess and pty tests keep their own timeouts, which fire first."""
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        yield
        return
    marker = request.node.get_closest_marker("timeout")
    seconds = max(1, math.ceil(marker.args[0])) if marker and marker.args else TIMEOUT_SECONDS

    def expired(signum, frame):
        raise TimeoutError(f"test exceeded {seconds} s")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, signal.SIG_DFL if previous is None else previous)
