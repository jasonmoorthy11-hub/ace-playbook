"""Chain heads, `compile keygen` (re-seal, --rehead), the hook, the offline pass and the CLI writers with keyed chains."""

import json
import os
import stat
import sys
from types import SimpleNamespace

import pytest

from ace_playbook.canon import GENESIS, chain_key, keyed_hash
from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import approvals, chains, ladder, offline
from ace_playbook.compile import demotions as dm
from ace_playbook.compile.chains import Keyring

from . import compile_support as cs
from .test_compile_cli import approved_dir, run as cli_run, tty, world, written  # noqa: F401  (fixtures)
from .test_compile_keyed import K1, K2, SHA, add, arow, drow, forge, write_key
from .test_compile_shadow import RM, Runner


@pytest.fixture
def w(tmp_path):
    return cs.real_world(tmp_path)


@pytest.fixture
def run(w, monkeypatch, capsys):
    return Runner(w, monkeypatch, capsys)


def keyed(w, key=K1):
    """The key file and empty signed heads, as `keygen` leaves a fresh install; returns the Keyring."""
    write_key(w.cfg.chain_key_path, key)
    ring = chains.key_of(w.cfg)
    ring.update(approvals=GENESIS, demotions=GENESIS)
    return ring


def promote(w, mode, ring, observe_first=False):
    p = w.props["rm_rf_nonscratch"]
    for m in (["observe"] if observe_first else []) + [mode]:
        path = cs.approve_file(w.cfg, p, m)
        approvals.append(w.data, {"proposal_id": p["id"], "proposal_sha256": json.loads(path.read_text())["proposal_sha256"], "mode": m}, ring)
    return p


def demote(w, ring, to="observe", ts="2026-12-31T00:00:00Z", frm="warn", ref="0123abcd"):
    return dm.append(w.data, {"ts": ts, "proposal_id": w.props["rm_rf_nonscratch"]["id"], "from_mode": frm, "to_mode": to,
                              "cause": "label_fp", "ref": ref, "review_required": False}, ring=ring)


def forged_demotion(w, signer):
    """A demotion row appended by hand under `signer`, heads untouched."""
    prev = dm.read_chain(w.data, Keyring(K1))[0]
    row = {"ts": "2026-12-31T00:00:00Z", "v": 2, "proposal_id": w.props["rm_rf_nonscratch"]["id"], "from_mode": "warn", "to_mode": "observe",
           "cause": "label_fp", "ref": "0123abcd", "review_required": False, "prev_hash": prev[-1]["hash"] if prev else GENESIS}
    with open(w.data / dm.FILE, "a") as fh:
        fh.write(json.dumps({**row, "hash": keyed_hash(signer, row)}, sort_keys=True) + "\n")


def drop_last_line(path):
    lines = path.read_text().splitlines()
    path.write_text("".join(line + "\n" for line in lines[:-1]))


# ------------------------------------------------------------------ the heads file
def test_the_heads_file_is_signed_private_and_updated_by_every_keyed_append(tmp_path):
    ring = Keyring(K1, tmp_path / chains.HEADS)
    ring.update(approvals=GENESIS, demotions=GENESIS)
    assert stat.S_IMODE(os.stat(ring.heads_path).st_mode) == 0o600 and ring.heads()["approvals"] == GENESIS
    r1, d1 = add(approvals, tmp_path, ring, 1), add(dm, tmp_path, ring, 1)
    assert {k: v for k, v in ring.heads().items() if k != "mac"} == {"approvals": r1["hash"], "demotions": d1["hash"]}
    r2 = add(approvals, tmp_path, ring, 2)
    assert ring.heads()["approvals"] == r2["hash"] and ring.heads()["demotions"] == d1["hash"]


def test_a_heads_file_that_is_edited_unsigned_signed_with_another_key_or_a_symlink_is_invalid(tmp_path):
    ring = Keyring(K1, tmp_path / chains.HEADS)
    ring.update(approvals="b" * 64, demotions=GENESIS)
    good = json.loads(ring.heads_path.read_text())
    for bad in ({**good, "approvals": "c" * 64}, {k: v for k, v in good.items() if k != "mac"}, {**good, "extra": "x"}, [1]):
        ring.heads_path.write_text(json.dumps(bad))
        assert ring.heads() is None
    Keyring(K2, ring.heads_path).update(approvals="b" * 64)
    assert ring.heads() is None
    ring.heads_path.unlink()
    (tmp_path / "real").write_text(json.dumps(good))
    ring.heads_path.symlink_to(tmp_path / "real")
    assert ring.heads() is None


@pytest.mark.parametrize("mod", [approvals, dm], ids=["approvals", "demotions"])
def test_a_chain_cut_by_one_row_is_a_head_mismatch_and_append_will_not_extend_it(tmp_path, mod):
    ring = Keyring(K1, tmp_path / chains.HEADS)
    ring.update(approvals=GENESIS, demotions=GENESIS)
    for i in (1, 2, 3):
        add(mod, tmp_path, ring, i)
    assert mod.read_chain(tmp_path, ring)[1] is None
    drop_last_line(tmp_path / mod.FILE)
    rows, broken = mod.read_chain(tmp_path, ring)
    assert broken == "head_mismatch" and len(rows) == 2
    with pytest.raises(ValueError, match="head mismatch"):
        add(mod, tmp_path, ring, 4)
    (tmp_path / mod.FILE).unlink()
    assert mod.read_chain(tmp_path, ring) == ([], "head_mismatch")             # deleting the file does not hide the cut


@pytest.mark.parametrize("mod", [approvals, dm], ids=["approvals", "demotions"])
def test_without_the_heads_file_a_keyed_chain_is_broken_even_when_empty(tmp_path, mod):
    ring = Keyring(K1, tmp_path / chains.HEADS)
    assert mod.read_chain(tmp_path, ring) == ([], "heads_missing")
    add(mod, tmp_path, Keyring(K1), 1)                                          # a row written without heads bookkeeping
    assert mod.read_chain(tmp_path, ring)[1] == "heads_missing"
    with pytest.raises(ValueError, match="chain_heads.json"):
        add(mod, tmp_path, ring, 2)


def test_a_rollback_to_an_earlier_state_without_its_heads_is_a_head_mismatch(tmp_path):
    ring = Keyring(K1, tmp_path / chains.HEADS)
    ring.update(approvals=GENESIS, demotions=GENESIS)
    add(approvals, tmp_path, ring, 1)
    early = (tmp_path / approvals.FILE).read_bytes()
    add(approvals, tmp_path, ring, 2)
    (tmp_path / approvals.FILE).write_bytes(early)
    assert approvals.read_chain(tmp_path, ring)[1] == "head_mismatch"


# ------------------------------------------------------------------ keygen
def test_keygen_on_a_fresh_install_makes_the_key_and_empty_heads_and_no_chain_files(w, tty, capsys):
    assert cli.main(["--config", w.conf, "keygen"]) == 0
    out, err = capsys.readouterr()
    key_path = w.cfg.chain_key_path
    assert out.splitlines()[-1] == str(key_path) and "kept:" in err and len(chain_key(key_path)) == 32 and len(tty.asked) == 1
    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600 and key_path.read_text() not in out
    ring = chains.key_of(w.cfg)
    assert ring.heads()["approvals"] == GENESIS and ring.heads()["demotions"] == GENESIS
    assert not (w.data / approvals.FILE).exists() and not (w.data / dm.FILE).exists()


def test_keygen_makes_a_missing_parent_0700_and_refuses_to_overwrite_or_without_a_terminal(tmp_path, tty, monkeypatch, capsys):
    key_path = tmp_path / "cfgdir" / "chain_key"
    conf = tmp_path / "c.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "chain_key_path": str(key_path)}))
    monkeypatch.setattr(sys, "stdin", open(os.devnull))
    assert cli.main(["--config", str(conf), "keygen"]) == 2 and not key_path.parent.exists()
    assert "needs an interactive terminal" in capsys.readouterr().err
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    assert cli.main(["--config", str(conf), "keygen"]) == 0
    capsys.readouterr()
    assert stat.S_IMODE(os.stat(key_path.parent).st_mode) == 0o700
    before = key_path.read_bytes()
    assert cli.main(["--config", str(conf), "keygen"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and len(err.strip().splitlines()) == 1 and key_path.read_bytes() == before


def test_keygen_re_seals_a_v1_chain_as_v2_with_the_same_content_and_signs_the_heads(w, tty, capsys):
    for i in (1, 2, 3):
        add(approvals, w.data, None, i)
        add(dm, w.data, None, i)
    old_a, old_d = approvals.read_chain(w.data)[0], dm.read_chain(w.data)[0]
    assert cli.main(["--config", w.conf, "keygen"]) == 0
    ring = chains.key_of(w.cfg)
    (new_a, ba), (new_d, bd) = approvals.read_chain(w.data, ring), dm.read_chain(w.data, ring)
    assert ba is None and bd is None and [r["v"] for r in new_a + new_d] == [2] * 6
    strip = lambda rows: [{k: v for k, v in r.items() if k not in ("v", "prev_hash", "hash")} for r in rows]    # noqa: E731
    assert strip(new_a) == strip(old_a) and strip(new_d) == strip(old_d)
    assert new_a[0]["prev_hash"] == GENESIS and new_a[1]["prev_hash"] == new_a[0]["hash"] and new_a[0]["hash"] != old_a[0]["hash"]
    assert ring.heads()["approvals"] == new_a[-1]["hash"] and ring.heads()["demotions"] == new_d[-1]["hash"]
    assert approvals.read_chain(w.data)[1] == 1                                 # without the key the keyed chain is broken
    assert add(approvals, w.data, ring, 4)["v"] == 2 and not list(w.data.glob("*.tmp"))


def test_keygen_keeps_an_unverifiable_tail_after_the_re_sealed_rows_and_warns_naming_the_row(w, tty, capsys):
    add(approvals, w.data, None, 1)
    add(approvals, w.data, None, 2)
    with open(w.data / approvals.FILE, "ab") as fh:
        fh.write(b"not json\n")
    assert cli.main(["--config", w.conf, "keygen"]) == 0
    assert "approvals: row 3 onward does not verify and will be kept as is" in capsys.readouterr().out
    lines = (w.data / approvals.FILE).read_bytes().split(b"\n")
    assert lines[2] == b"not json" and [json.loads(x)["v"] for x in lines[:2]] == [2, 2]
    ring = chains.key_of(w.cfg)
    assert approvals.read_chain(w.data, ring)[1] == 3                           # still broken until the operator truncates ...
    drop_last_line(w.data / approvals.FILE)
    assert approvals.read_chain(w.data, ring)[1] is None                        # ... and the head already matches the kept prefix


def test_rehead_repairs_the_heads_after_an_operator_truncation(w, tty, capsys):
    ring = keyed(w)
    for i in (1, 2, 3):
        add(approvals, w.data, ring, i)
    drop_last_line(w.data / approvals.FILE)
    assert approvals.read_chain(w.data, ring)[1] == "head_mismatch"
    assert cli.main(["--config", w.conf, "keygen", "--rehead"]) == 0
    out = capsys.readouterr().out.splitlines()
    rows, broken = approvals.read_chain(w.data, ring)
    assert broken is None and len(rows) == 2 and out[-2:] == [f"approvals: {rows[-1]['hash']}", f"demotions: {GENESIS}"] and "old head not found" in out[0]


def test_rehead_needs_a_usable_key_and_intact_chains(w, tty, capsys):
    assert cli.main(["--config", w.conf, "keygen", "--rehead"]) == 1 and "no usable chain key" in capsys.readouterr().err
    ring = keyed(w)
    add(approvals, w.data, ring, 1)
    with open(w.data / approvals.FILE, "ab") as fh:
        fh.write(b"junk\n")
    before = ring.heads()
    assert cli.main(["--config", w.conf, "keygen", "--rehead"]) == 1
    assert "approvals.jsonl chain broken at row 2" in capsys.readouterr().err and ring.heads() == before


# ------------------------------------------------------------------ the hook
def test_a_warn_copy_with_a_valid_keyed_chain_warns(w, run):
    p = promote(w, "warn", keyed(w))
    code, out, err = run(cs.pre_payload(RM))
    assert code == 0 and err == p["message"] + "\n" and "hookSpecificOutput" in json.loads(out)
    assert run.rows[0]["mode"] == "warn" and run.log == ""


def test_cutting_the_last_row_runs_observe_and_logs_head_mismatch_once(w, run):
    promote(w, "warn", keyed(w), observe_first=True)
    drop_last_line(w.data / approvals.FILE)                                     # what is left is a valid prefix of a keyed chain
    assert run(cs.pre_payload(RM)) == (0, "", "") and run(cs.pre_payload(RM)) == (0, "", "")
    assert [r["mode"] for r in run.rows] == ["observe", "observe"] and run.log.count("approvals:head_mismatch") == 1
    assert "chain_broken" not in run.log and "error:" not in run.log


def test_a_missing_heads_file_breaks_both_chains_and_logs_once(w, run):
    ring = keyed(w)
    promote(w, "warn", ring)
    ring.heads_path.unlink()
    assert run(cs.pre_payload(RM)) == (0, "", "") and run(cs.pre_payload(RM)) == (0, "", "")
    assert run.rows[0]["mode"] == "observe" and run.log.count("chain_heads:missing") == 1
    assert "approvals:chain_broken" in run.log and "demotions:chain_broken" in run.log


def test_the_key_file_removed_runs_observe_and_logs_chain_key_missing_once(w, run):
    promote(w, "warn", keyed(w))
    w.cfg.chain_key_path.unlink()
    assert run(cs.pre_payload(RM)) == (0, "", "") and run(cs.pre_payload(RM)) == (0, "", "")
    assert run.log.count("chain_key:missing") == 1 and run.log.count("approvals:chain_broken") == 1 and run.rows[0]["mode"] == "observe"


def test_a_key_file_with_other_group_bits_counts_as_missing(w, run):
    promote(w, "warn", keyed(w))
    w.cfg.chain_key_path.chmod(0o640)
    assert run(cs.pre_payload(RM)) == (0, "", "") and "chain_key:missing" in run.log


def test_a_chain_signed_with_another_key_is_broken_not_missing(w, run):
    p = promote(w, "observe", keyed(w))
    path = cs.approve_file(w.cfg, p, "warn")                                    # a forger rewrites the chain under its own key (it cannot sign the heads)
    forge(w.data, arow, approvals.FILE, [(2, K2)], mode="warn", proposal_id=p["id"],
          proposal_sha256=json.loads(path.read_text())["proposal_sha256"])
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows[0]["mode"] == "observe"
    assert "approvals:chain_broken" in run.log and "chain_key:missing" not in run.log


def test_the_downgrade_rewrite_as_v1_with_the_key_present_runs_observe(w, run):
    ring = keyed(w)
    p = promote(w, "observe", ring)
    path = cs.approve_file(w.cfg, p, "block")                                   # the agent edits the copy and rewrites the chain as plain v1
    forge(w.data, arow, approvals.FILE, [(1, None)], mode="block", proposal_id=p["id"],
          proposal_sha256=json.loads(path.read_text())["proposal_sha256"])
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.rows[0]["mode"] == "observe" and "approvals:chain_broken" in run.log


def test_a_pre_keygen_data_dir_behaves_exactly_as_before(w, run):
    p = w.props["rm_rf_nonscratch"]
    cs.approve_file(w.cfg, p, "warn", row=True)                                 # a v1 row, no key file, no heads
    code, out, err = run(cs.pre_payload(RM))
    assert (code, err) == (0, p["message"] + "\n") and json.loads(out)["hookSpecificOutput"]["additionalContext"] == p["message"]
    assert run.rows[0]["mode"] == "warn" and run.log == ""


def test_garbage_that_only_says_v2_without_a_key_logs_a_broken_chain_not_a_missing_key(w, run):
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "warn")
    (w.data / approvals.FILE).write_text('{"v":2}\n')
    assert run(cs.pre_payload(RM)) == (0, "", "") and "approvals:chain_broken" in run.log and "chain_key:missing" not in run.log


def test_a_keyed_demotion_caps_a_forged_one_and_a_v1_rewrite_do_not_lift_it_and_a_cut_cannot_lift_it(tmp_path, monkeypatch, capsys):
    cases = {}
    for name in ("good", "forged", "downgrade", "cut"):
        world_ = cs.real_world(tmp_path / name)
        ring = keyed(world_)
        promote(world_, "warn", ring)
        demote(world_, ring) if name != "forged" else forged_demotion(world_, K2)
        if name == "cut":                                                       # two rows, then the second is cut: the cap stays
            demote(world_, ring, ts="2026-12-31T00:00:01Z", frm="observe", ref="89abcdef")
            drop_last_line(world_.data / dm.FILE)
        if name == "downgrade":
            forge(world_.data, drow, dm.FILE, [(1, None)], proposal_id=world_.props["rm_rf_nonscratch"]["id"], to_mode="warn")
        r = Runner(world_, monkeypatch, capsys)
        assert r(cs.pre_payload(RM)) == (0, "", "")
        cases[name] = (r.rows[0]["mode"], r.log)
    assert cases["good"][0] == "observe" and "chain_broken" not in cases["good"][1] and "head_mismatch" not in cases["good"][1]
    assert cases["forged"][0] == "observe" and "demotions:chain_broken" in cases["forged"][1]
    assert cases["downgrade"][0] == "observe" and "demotions:chain_broken" in cases["downgrade"][1]
    assert cases["cut"][0] == "observe" and "demotions:head_mismatch" in cases["cut"][1]


# ------------------------------------------------------------------ the offline pass
def offline_world(tmp_path, with_key, tty):
    w = cs.offline_world(tmp_path, "block")                                     # its block row is a v1 row: keygen re-seals it
    assert cli.main(["--config", w.conf, "keygen"]) == 0
    ring = chains.key_of(w.cfg)
    add(dm, w.data, ring, 99)                                                   # an unrelated proposal's v2 row
    if not with_key:
        w.cfg.chain_key_path.unlink()
    cs.put_shadow(w, "s1", cs.RM_PROMPT_CMD, "block", "2026-10-18T10:00:00Z")
    cs.put_session(w, "s1", [{"human": "please remove /w/src"}, {"tool": "Bash", "input": {"command": cs.RM_PROMPT_CMD}}])
    return w, ring


def test_the_offline_pass_refuses_a_demotion_without_the_key_counts_an_error_and_writes_the_rest(tmp_path, capsys, tty):
    w, _ = offline_world(tmp_path, False, tty)
    capsys.readouterr()
    before = (w.data / dm.FILE).read_bytes()
    out = offline.run(w.cfg, now=cs.NOW)
    assert out["demotions"] == 0 and out["errors"] >= 1 and (w.data / dm.FILE).read_bytes() == before
    assert "restore the chain key file" in capsys.readouterr().err
    assert w.cfg.path("detector_events.jsonl").exists() and out["labels"]["auto_fp_prompt_target"] == 1


def test_the_offline_pass_writes_the_demotion_as_a_v2_row_and_moves_the_head(tmp_path, tty):
    w, ring = offline_world(tmp_path, True, tty)
    out = offline.run(w.cfg, now=cs.NOW)
    rows, broken = dm.read_chain(w.data, ring)
    assert out["demotions"] == 1 and out["errors"] == 0 and broken is None and len(rows) == 2
    assert rows[-1]["v"] == 2 and (rows[-1]["from_mode"], rows[-1]["to_mode"]) == ("block", "warn")


# ------------------------------------------------------------------ approve, unapprove, ladder
def v2_world(world):
    ring = keyed(world)
    approvals.append(world.cfg.data_dir, {"proposal_id": "P-00000099", "proposal_sha256": SHA, "mode": "observe"}, ring)
    world.cfg.chain_key_path.unlink()
    return (world.cfg.data_dir / "approvals.jsonl").read_bytes()


def test_approve_observe_refuses_without_the_key_after_a_v2_row_and_names_keygen(world, written, tty, capsys):
    before = v2_world(world)
    code, _, err = cli_run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 1 and err.count("\n") == 1 and "chain key missing" in err and "restore the chain key file" in err
    assert (world.cfg.data_dir / "approvals.jsonl").read_bytes() == before and tty.asked == []


def test_approve_observe_with_the_key_appends_a_v2_row_and_moves_the_head(world, written, tty, capsys):
    ring = keyed(world)
    assert cli_run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)[0] == 0
    rows, broken = approvals.read_chain(world.cfg.data_dir, ring)
    assert broken is None and [r["v"] for r in rows] == [2] and ring.heads()["approvals"] == rows[-1]["hash"]


def test_approve_observe_on_a_v1_chain_without_a_key_still_writes_v1(world, written, tty, capsys):
    assert cli_run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)[0] == 0
    assert [r["v"] for r in approvals.read_chain(world.cfg.data_dir)[0]] == [1]


def test_garbage_that_only_says_v2_makes_approve_report_a_broken_chain_not_a_missing_key(world, written, tty, capsys):
    (world.cfg.data_dir / "approvals.jsonl").write_text('{"v":2}\n')
    code, _, err = cli_run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 1 and "chain broken at row 1" in err and "keygen" not in err


def test_unapprove_without_the_key_still_removes_the_copy_prints_the_keygen_line_and_exits_1(world, written, tty, capsys):
    keyed(world)
    cli_run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    world.cfg.chain_key_path.unlink()
    before = (world.cfg.data_dir / "approvals.jsonl").read_bytes()
    code, out, err = cli_run(world, "unapprove", written.stem, capsys=capsys)
    assert code == 1 and "restore the chain key file" in err and err.count("chain key missing") == 1 and "warning" not in err
    assert not (approved_dir(world) / (written.stem + ".json")).exists() and (world.cfg.data_dir / "approvals.jsonl").read_bytes() == before
    assert "no longer approved" in out


def test_unapprove_with_the_key_writes_a_v2_revocation_row_and_exits_0(world, written, tty, capsys):
    ring = keyed(world)
    cli_run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert cli_run(world, "unapprove", written.stem, capsys=capsys)[0] == 0
    rows, broken = approvals.read_chain(world.cfg.data_dir, ring)
    assert broken is None and [r["mode"] for r in rows] == ["observe", "revoked"] and ring.heads()["approvals"] == rows[-1]["hash"]


def test_ladder_run_refuses_without_the_key_after_a_v2_row(world, written, tty, capsys):
    before = v2_world(world)
    args = SimpleNamespace(id=written.stem, mode="warn", opus_reviewed=None, raw_checked=False)
    assert ladder.run(world.cfg, args, lambda prompt: "y", lambda what: True) == 1
    assert "restore the chain key file" in capsys.readouterr().err and (world.cfg.data_dir / "approvals.jsonl").read_bytes() == before
