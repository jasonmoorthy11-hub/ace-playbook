"""Keyed (HMAC) approvals and demotions chains (docs/design/compiler.md 7.3, "Keyed chains (as built)"): the primitives and the
verifier rule. The heads, keygen, hook and CLI tests are in test_compile_keyed_heads.py."""

import hashlib
import hmac
import json
import os
from pathlib import Path

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.canon import GENESIS, chain_key, keyed_hash, record_hash
from ace_playbook.compile import approvals, chains, chains_io
from ace_playbook.compile import demotions as dm
from ace_playbook.compile.chains import Keyring

K1, K2 = bytes(range(32)), bytes(range(1, 33))
SHA, TS = "a" * 64, "2026-10-19T00:00:00Z"
COMPILE = Path(__file__).resolve().parent.parent / "ace_playbook" / "compile"


def write_key(path, key=K1, mode=0o600):
    path.write_text(key.hex())
    path.chmod(mode)
    return path


# ------------------------------------------------------------------ the primitives
def test_keyed_hash_matches_a_hand_computed_hmac_over_the_canonical_row_without_its_hash():
    row = {"b": "x", "a": 1, "hash": "ignored"}
    by_hand = hmac.new(K1, b'{"a":1,"b":"x"}', hashlib.sha256).hexdigest()
    assert by_hand == "e8ecd7b7a19fbb61a416b75ab54c2a6423a27cb4ce35427987f737f196c3a25d"
    assert keyed_hash(K1, row) == by_hand == keyed_hash(K1, {"a": 1, "b": "x"})
    assert keyed_hash(K2, row) != by_hand and keyed_hash(K1, row) != record_hash(row)


@pytest.mark.parametrize("case", ["ok", "ok_newline", "ok_0400", "mode_0644", "mode_0640", "mode_0200", "symlink", "short", "long",
                                  "non_hex", "missing", "directory", "empty", "fifo"])
def test_chain_key_reads_only_an_owner_only_regular_file_of_64_hex_characters(tmp_path, case):
    path = tmp_path / "key"
    if case in ("ok", "ok_0400", "mode_0644", "mode_0640", "mode_0200"):
        write_key(path, mode={"ok": 0o600, "ok_0400": 0o400, "mode_0644": 0o644, "mode_0640": 0o640, "mode_0200": 0o200}[case])
    elif case == "ok_newline":
        path.write_text(K1.hex() + "\n")
        path.chmod(0o600)
    elif case == "symlink":
        write_key(tmp_path / "real")
        path.symlink_to(tmp_path / "real")
    elif case in ("short", "long", "non_hex", "empty"):
        path.write_text({"short": K1.hex()[:63], "long": K1.hex() + "00", "non_hex": "z" * 64, "empty": ""}[case])
        path.chmod(0o600)
    elif case == "directory":
        path.mkdir(mode=0o700)
    elif case == "fifo":
        os.mkfifo(path, 0o600)
    assert chain_key(path) == (K1 if case in ("ok", "ok_newline", "ok_0400") else None)


def test_chain_key_never_raises_on_odd_arguments():
    assert chain_key(None) is None and chain_key("") is None and chain_key("a\0b") is None


# ------------------------------------------------------------------ the verifier rule, on hand-forged chains
def arow(i, v, prev, signer, **over):
    row = {"ts": TS, "v": v, "proposal_id": f"P-{i:08d}", "proposal_sha256": SHA, "mode": "observe", "reviewed_by": "operator-tty",
           "tp_refs": [], "fp_refs": [], "review_sha": "-", "prev_hash": prev, **over}
    return {**row, "hash": over["hash"] if "hash" in over else record_hash(row) if signer is None else keyed_hash(signer, row)}


def drow(i, v, prev, signer, **over):
    row = {"ts": TS, "v": v, "proposal_id": f"P-{i:08d}", "from_mode": "block", "to_mode": "warn", "cause": "label_fp",
           "ref": "0123abcd", "review_required": False, "prev_hash": prev, **over}
    return {**row, "hash": over["hash"] if "hash" in over else record_hash(row) if signer is None else keyed_hash(signer, row)}


def forge(data, make, name, specs, **over):
    prev, lines = GENESIS, []
    for i, (v, signer) in enumerate(specs, 1):
        row = make(i, v, prev, signer, **over)
        prev = row["hash"] if isinstance(row["hash"], str) else prev
        lines.append(json.dumps(row, sort_keys=True))
    (data / name).write_text("\n".join(lines) + "\n")


MODS = [pytest.param(approvals, arow, 1, id="approvals"), pytest.param(dm, drow, 0, id="demotions")]   # base: the number of row 0
RING = Keyring(K1)                                             # no heads file: the heads are tested in test_compile_keyed_heads.py
# (specs of (v, signed with), the keyring given to the reader, 0-based index of the first bad row or None)
RULE = [
    ([(2, K1), (2, K1)], RING, None),              # key readable: v2 only
    ([(1, None), (1, None)], RING, 0),             # the downgrade: a chain rewritten as v1 with plain hashes
    ([(2, K1), (1, None)], RING, 1),               # a v1 row after v2
    ([(1, None), (2, K1)], RING, 0),               # v1 before v2 no longer verifies either
    ([(2, K1), (2, K2)], RING, 1),                 # signed with another key
    ([(2, None)], RING, 0),                        # v2 carrying a plain sha256 hash
    ([(1, None), (1, None)], None, None),          # no key: v1 only (installs from before keygen)
    ([(1, None), (2, K1)], None, 1),               # no key: a v2 row is a break
    ([(2, K1)], None, 0),
    ([(1, K1)], None, 0),                          # v1 carrying a keyed hash
]


@pytest.mark.parametrize("mod,make,base", MODS)
@pytest.mark.parametrize("specs,ring,bad", RULE)
def test_the_verifier_rule(tmp_path, mod, make, base, specs, ring, bad):
    forge(tmp_path, make, mod.FILE, specs)
    rows, broken = mod.read_chain(tmp_path, ring)
    assert broken == (None if bad is None else bad + base)
    assert len(rows) == (len(specs) if bad is None else bad)


@pytest.mark.parametrize("mod,make,base", MODS)
@pytest.mark.parametrize("v", [2.0, "2", True, None, [2]])
def test_a_version_that_is_not_the_int_2_never_verifies_under_a_key(tmp_path, mod, make, base, v):
    forge(tmp_path, make, mod.FILE, [(v, K1)])
    assert mod.read_chain(tmp_path, RING) == ([], base)


@pytest.mark.parametrize("mod,make,base", MODS)
@pytest.mark.parametrize("h", [None, 0, ["x"], {"a": 1}, True, "é" * 64])
def test_a_hash_that_is_not_an_ascii_string_never_verifies(tmp_path, mod, make, base, h):
    forge(tmp_path, make, mod.FILE, [(2, K1)], hash=h)
    assert mod.read_chain(tmp_path, RING) == ([], base) and mod.read_chain(tmp_path) == ([], base)


@pytest.mark.parametrize("mod,make,base", MODS)
def test_the_default_keyring_is_none_so_existing_v1_callers_keep_working(tmp_path, mod, make, base):
    forge(tmp_path, make, mod.FILE, [(1, None)])
    assert mod.read_chain(tmp_path)[1] is None


# ------------------------------------------------------------------ append
def add(mod, data, ring, i):
    if mod is approvals:
        return approvals.append(data, {"proposal_id": f"P-{i:08d}", "proposal_sha256": SHA, "mode": "observe", "ts": TS}, ring)
    return dm.append(data, {"proposal_id": f"P-{i:08d}", "from_mode": "block", "to_mode": "warn", "cause": "label_fp",
                            "ref": "0123abcd", "review_required": False}, ring=ring)


@pytest.mark.parametrize("mod", [approvals, dm], ids=["approvals", "demotions"])
def test_append_without_a_key_keeps_writing_v1_on_an_empty_or_v1_only_chain(tmp_path, mod):
    r1, r2 = add(mod, tmp_path, None, 1), add(mod, tmp_path, None, 2)
    assert (r1["v"], r2["v"]) == (1, 1) and r2["hash"] == record_hash(r2) and r2["prev_hash"] == r1["hash"]
    assert mod.read_chain(tmp_path) == ([r1, r2], None)


@pytest.mark.parametrize("mod", [approvals, dm], ids=["approvals", "demotions"])
def test_append_with_a_key_always_writes_v2_and_the_chain_verifies_only_with_that_key(tmp_path, mod):
    r1, r2 = add(mod, tmp_path, RING, 1), add(mod, tmp_path, RING, 2)
    assert (r1["v"], r2["v"]) == (2, 2) and r2["hash"] == keyed_hash(K1, r2) and r2["prev_hash"] == r1["hash"]
    assert mod.read_chain(tmp_path, RING) == ([r1, r2], None)
    assert mod.read_chain(tmp_path, Keyring(K2))[1] is not None and mod.read_chain(tmp_path)[1] is not None


@pytest.mark.parametrize("mod", [approvals, dm], ids=["approvals", "demotions"])
def test_append_with_a_key_refuses_a_v1_chain_it_cannot_verify(tmp_path, mod):
    add(mod, tmp_path, None, 1)                                                # a v1 row, now there is a key
    before = (tmp_path / mod.FILE).read_bytes()
    with pytest.raises(ValueError, match="chain broken at row"):
        add(mod, tmp_path, RING, 2)
    assert (tmp_path / mod.FILE).read_bytes() == before


@pytest.mark.parametrize("mod", [approvals, dm], ids=["approvals", "demotions"])
def test_append_refuses_without_the_key_once_the_chain_has_a_v2_row(tmp_path, mod):
    add(mod, tmp_path, RING, 1)
    before = (tmp_path / mod.FILE).read_bytes()
    with pytest.raises(ValueError, match="^chain key missing$"):
        add(mod, tmp_path, None, 2)
    with pytest.raises(ValueError, match="chain broken"):                      # a wrong key is a broken chain, not a missing one
        add(mod, tmp_path, Keyring(K2), 2)
    assert (tmp_path / mod.FILE).read_bytes() == before


@pytest.mark.parametrize("mod,make,base", MODS)
def test_garbage_that_only_says_v2_is_a_broken_chain_not_a_missing_key(tmp_path, mod, make, base):
    (tmp_path / mod.FILE).write_text('{"v":2}\n{"v": 2, "hash": "x"}\n[2]\n')
    assert not chains_io.file_has_v2(tmp_path, mod)
    with pytest.raises(ValueError, match="chain broken at row"):
        add(mod, tmp_path, None, 1)
    forge(tmp_path, make, mod.FILE, [(2, K1)])
    assert chains_io.file_has_v2(tmp_path, mod)
    with pytest.raises(ValueError, match="^chain key missing$"):
        add(mod, tmp_path, None, 2)


# ------------------------------------------------------------------ config
def test_chain_key_path_has_no_default_and_can_be_set_or_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cfgmod.from_dict({}).chain_key_path is None                          # no ambient key (tests/test_keypath.py)
    assert cfgmod.from_dict({"chain_key_path": "~/k"}).chain_key_path == tmp_path / "k"
    assert cfgmod.from_dict({"chain_key_path": None}).chain_key_path is None
    assert cfgmod.Config().chain_key_path is None                              # a bare Config has no key: tests stay hermetic


def test_the_heads_file_sits_beside_the_key(tmp_path):
    write_key(tmp_path / "chain_key")
    ring = chains.key_of(cfgmod.Config(chain_key_path=tmp_path / "chain_key"))
    assert ring.key == K1 and ring.heads_path == tmp_path / "chain_heads.json"
    bare = cfgmod.Config(data_dir=tmp_path)                                    # never the default data dir: key_of reads the chain files there
    assert chains.key_of(bare) is None and chains.key_of(cfgmod.Config(data_dir=tmp_path, chain_key_path=tmp_path / "none")) is None


# ------------------------------------------------------------------ structure
def test_the_keyed_chain_modules_stay_within_their_line_budgets():
    top = Path(cfgmod.__file__).parent
    for path, cap in ((top / "canon.py", 60), (COMPILE / "chains.py", 150), (COMPILE / "chains_io.py", 150), (COMPILE / "chains_cli.py", 150), (COMPILE / "approvals.py", 150),
                      (COMPILE / "demotions.py", 150), (COMPILE / "modes.py", 150), (COMPILE / "shadow.py", 300),
                      (COMPILE / "__main__.py", 300), (COMPILE / "ladder.py", 300), (COMPILE / "offline.py", 300),
                      (Path(__file__), 400), (Path(__file__).with_name("test_compile_keyed_heads.py"), 400),
                      (Path(__file__).with_name("test_compile_keyed_marker.py"), 400)):
        assert len(path.read_text().splitlines()) <= cap, path.name
