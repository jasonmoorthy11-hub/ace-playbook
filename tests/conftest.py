"""Hermetic default for every test: nothing under `tests/` may reach the operator's real chain key.

Before this fixture existed the suite was green only while no chain key existed on the machine: a config file written without
`chain_key_path` made `config.load` fill in the real default under `~/.config/ace-playbook`, and the first `compile keygen` on the
developer's Mac turned six unrelated tests red (their tmp approvals chains were read as keyed-without-heads). CI never saw it.
`tests.test_hooks.env` gives child processes a throwaway HOME for the same reason. A test that checks the production default
restores the literal itself (tests/test_compile_keyed.py).
"""

from __future__ import annotations

import pytest

from ace_playbook import config as cfgmod


@pytest.fixture(autouse=True)
def hermetic_chain_key(tmp_path_factory, monkeypatch):
    """`config.load` of a file without `chain_key_path` resolves to a path that never exists (a never-keyed install)."""
    path = tmp_path_factory.getbasetemp() / "no-operator-key" / "chain_key"     # never created
    monkeypatch.setattr(cfgmod, "DEFAULT_CHAIN_KEY", str(path))
    return path
