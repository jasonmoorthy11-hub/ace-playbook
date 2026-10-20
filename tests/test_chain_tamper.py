"""The chain-tamper matrix: every way found to damage `approvals.jsonl`, `demotions.jsonl`, `chain_heads.json` or `labels.jsonl`, in one place.

The keyed-chain design is docs/design/compiler.md 7.3 ("Keyed chains (as built)") and section 9 (Security). The cases ALREADY pinned by
the three keyed test files are listed here by test name so a reviewer sees the whole matrix without opening them:

tests/test_compile_keyed.py
  - downgrade to v1 / v1 after v2 / v1 before v2 / row signed with another key / v2 with a plain hash:
    `test_the_verifier_rule` (both chains, 10 cases each)
  - `v` that is not the int 2, hash that is not an ASCII string:
    `test_a_version_that_is_not_the_int_2_never_verifies_under_a_key`, `test_a_hash_that_is_not_an_ascii_string_never_verifies`
  - writers refuse a chain they cannot verify:
    `test_append_with_a_key_refuses_a_v1_chain_it_cannot_verify`, `test_append_refuses_without_the_key_once_the_chain_has_a_v2_row`,
    `test_garbage_that_only_says_v2_is_a_broken_chain_not_a_missing_key`
tests/test_compile_keyed_heads.py
  - heads file edited / unsigned / signed with another key / symlink:
    `test_a_heads_file_that_is_edited_unsigned_signed_with_another_key_or_a_symlink_is_invalid`
  - last row cut, file deleted after a cut, missing heads file, rollback of the chain alone:
    `test_a_chain_cut_by_one_row_is_a_head_mismatch_and_append_will_not_extend_it`,
    `test_without_the_heads_file_a_keyed_chain_is_broken_even_when_empty`,
    `test_a_rollback_to_an_earlier_state_without_its_heads_is_a_head_mismatch`
  - the hook at observe: `test_cutting_the_last_row_runs_observe_and_logs_head_mismatch_once`,
    `test_a_missing_heads_file_breaks_both_chains_and_logs_once`, `test_the_key_file_removed_runs_observe_and_logs_chain_key_missing_once`,
    `test_a_key_file_with_other_group_bits_counts_as_missing`, `test_a_chain_signed_with_another_key_is_broken_not_missing`,
    `test_the_downgrade_rewrite_as_v1_with_the_key_present_runs_observe`,
    `test_a_keyed_demotion_caps_a_forged_one_and_a_v1_rewrite_do_not_lift_it_and_a_cut_cannot_lift_it`
  - repair: `test_keygen_keeps_an_unverifiable_tail_after_the_re_sealed_rows_and_warns_naming_the_row`,
    `test_rehead_repairs_the_heads_after_an_operator_truncation`, `test_rehead_needs_a_usable_key_and_intact_chains`
tests/test_compile_keyed_marker.py
  - the install stays keyed when files go missing: `test_key_of_is_none_only_for_an_unkeyed_install`,
    `test_key_deleted_heads_kept_and_the_chain_rewritten_as_v1_runs_observe_and_logs_chain_key_missing_once_and_keygen_refuses`,
    `test_keygen_refuses_when_the_heads_were_deleted_after_a_v1_rewrite`,
    `test_keygen_refuses_after_the_heads_were_deleted_and_a_revoked_row_cut_and_rehead_says_the_old_heads_are_gone`,
    `test_an_oversize_chain_is_reported_as_a_broken_chain_by_keygen_not_a_bare_error`

ADDED here, all on a keyed tmp world with a key made in the test (never the operator's), read through the public readers
(`modes.read_approvals`, `modes.read_demotions`, `labels.read_chain`), `modes.chain_notice` and `report.run` (JSON and text):
  1. splice between chains (a valid row of the other chain, also re-linked and re-signed under the real key)
  2. a cut last line (crash during append) versus a modified middle row, and the operator wording of each
  3. both chain files deleted while the signed heads file remains
  4. heads file deleted; an older valid heads file replayed (rollback); heads signed under another key
  5. a middle row re-signed under a different key
  6. the accepted residual of section 9: chain AND heads restored together

`report.run(cfg, days, as_json, now)` is the report entry point (there is no `collect`); the report prints the demotions and labels chains,
the approvals chain only through the mode a proposal resolves to.
"""

import json

import pytest

from ace_playbook.canon import GENESIS, keyed_hash
from ace_playbook.compile import approvals, chains, labels, modes, report
from ace_playbook.compile import demotions as dm
from ace_playbook.compile.chains import Keyring

from . import compile_support as cs
from .test_compile_keyed import K1, K2, add
from .test_compile_keyed_heads import drop_last_line, keyed

NOW = "2026-12-31T12:00:00Z"
READ = {approvals: modes.read_approvals, dm: modes.read_demotions}
MODS = [pytest.param(approvals, id="approvals"), pytest.param(dm, id="demotions")]
EDIT = {approvals: ("review_sha", "abcdef0"), dm: ("ref", "deadbeef")}         # a field change that keeps the row shape-valid: only the hash breaks


@pytest.fixture
def w(tmp_path):
    return cs.real_world(tmp_path)


def fill(w, ring, n=3):
    """`n` rows in each chain, heads current."""
    for i in range(1, n + 1):
        add(approvals, w.data, ring, i)
        add(dm, w.data, ring, i)


def lines(w, mod):
    return (w.data / mod.FILE).read_text().splitlines()


def number(mod, broken):
    """The 1-based row number of a break, whichever base the module's `verify` counts in."""
    return broken + mod.BASE


def report_json(w, capsys):
    capsys.readouterr()
    assert report.run(w.cfg, 30, as_json=True, now=NOW) == 0
    return json.loads(capsys.readouterr().out)


def report_text(w, capsys):
    capsys.readouterr()
    assert report.run(w.cfg, 30, now=NOW) == 0
    return capsys.readouterr().out


def refusal(mod, w, ring):
    with pytest.raises(ValueError) as exc:
        add(mod, w.data, ring, 9)
    return str(exc.value)


# ------------------------------------------------------------------ 1. splice between chains
@pytest.mark.parametrize("relinked", [False, True], ids=["as_is", "relinked_and_resigned_under_the_real_key"])
@pytest.mark.parametrize("mod", MODS)
def test_a_valid_row_of_the_other_chain_spliced_in_is_broken_at_that_row(w, mod, relinked):
    ring = keyed(w)
    fill(w, ring, 2)
    donor = dm if mod is approvals else approvals
    row = json.loads(lines(w, donor)[-1])
    assert donor.read_chain(w.data, ring)[1] is None                              # the row is valid where it came from
    if relinked:                                                                  # an attacker WITH the key: right prev_hash, right MAC
        row["prev_hash"] = json.loads(lines(w, mod)[-1])["hash"]
        row["hash"] = keyed_hash(K1, row)
    with open(w.data / mod.FILE, "a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    rows, broken = READ[mod](w.data, ring)
    # the two chains share one key and the MAC carries no chain label: the disjoint row KEYS (the shape check) are the separation
    assert len(rows) == 2 and number(mod, broken) == 3
    assert "chain broken at row 3" in refusal(mod, w, ring)
    assert READ[donor](w.data, ring)[1] is None                                   # the donor chain is untouched


def test_a_spliced_demotions_chain_is_named_by_the_report_and_the_notice(w, capsys):
    ring = keyed(w)
    fill(w, ring, 2)
    with open(w.data / dm.FILE, "a") as fh:
        fh.write(lines(w, approvals)[-1] + "\n")
    rep = report_json(w, capsys)["demotions"]
    assert (rep["chain_ok"], rep["broken_at"]) == (False, 2)
    assert modes.chain_notice(2).startswith("demotions.jsonl is broken at row 3: ")
    assert "demotions: chain BROKEN at row 3, rows after it ignored" in report_text(w, capsys)


# ------------------------------------------------------------------ 2. a cut last line versus a modified middle row
CUT = 40                                                                          # bytes lost from the end: the newline and half of the last row


@pytest.mark.parametrize("mod", MODS)
def test_a_cut_last_line_is_broken_at_that_line_and_repaired_by_truncating_to_the_last_complete_line(w, mod):
    ring = keyed(w)
    fill(w, ring, 2)
    heads = ring.heads_path.read_bytes()                                          # what a crash leaves: the head of the last COMPLETE row
    add(mod, w.data, ring, 3)
    path = w.data / mod.FILE
    path.write_bytes(path.read_bytes()[:-CUT])                                    # crash during the append of row 3
    ring.heads_path.write_bytes(heads)
    rows, broken = READ[mod](w.data, ring)
    assert len(rows) == 2 and number(mod, broken) == 3                            # broken AT the cut line (not head_mismatch): the rows before it verify
    drop_last_line(path)                                                          # the documented repair: truncate to the last complete line
    assert READ[mod](w.data, ring) == (rows, None)
    assert add(mod, w.data, ring, 3)["prev_hash"] == rows[-1]["hash"]             # the chain takes a new row again, no `keygen --rehead` needed


@pytest.mark.parametrize("mod", MODS)
def test_a_modified_middle_row_is_broken_at_that_row_and_truncating_the_end_does_not_mend_it(w, mod):
    ring = keyed(w)
    fill(w, ring, 3)                                                              # heads current: this is not a crash artefact
    path = w.data / mod.FILE
    field, value = EDIT[mod]
    recs = [json.loads(x) for x in lines(w, mod)]
    recs[1][field] = value
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs))
    rows, broken = READ[mod](w.data, ring)
    assert len(rows) == 1 and number(mod, broken) == 2
    drop_last_line(path)
    assert number(mod, READ[mod](w.data, ring)[1]) == 2                           # still broken at row 2: the repair for a cut line does not apply


def test_the_wording_tells_a_cut_line_a_modified_row_and_a_cut_tail_apart(w, capsys):
    ring = keyed(w)
    fill(w, ring, 3)
    path, good = w.data / dm.FILE, (w.data / dm.FILE).read_bytes()
    cases = {}
    path.write_bytes(good[:-CUT])
    cases["cut_line"] = modes.read_demotions(w.data, ring)[1]
    rows = [json.loads(x) for x in good.decode().splitlines()]
    rows[1]["ref"] = "deadbeef"
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    cases["modified_row"] = modes.read_demotions(w.data, ring)[1]
    path.write_bytes(good)
    drop_last_line(path)
    cases["cut_tail"] = modes.read_demotions(w.data, ring)[1]
    assert cases == {"cut_line": 2, "modified_row": 1, "cut_tail": "head_mismatch"}
    notes = {k: modes.chain_notice(v) for k, v in cases.items()}
    assert notes["cut_line"].startswith("demotions.jsonl is broken at row 3: ")
    assert notes["modified_row"].startswith("demotions.jsonl is broken at row 2: ")
    assert notes["cut_tail"].startswith("demotions.jsonl is broken by head mismatch (rows were cut or rolled back; ")
    path.write_bytes(good[:-CUT])
    assert "demotions: chain BROKEN at row 3, rows after it ignored" in report_text(w, capsys)
    path.write_bytes(good)
    drop_last_line(path)
    assert "demotions: chain BROKEN by head mismatch (rows were cut or rolled back" in report_text(w, capsys)


def test_the_approvals_wording_names_the_row_for_a_cut_line_and_the_head_for_a_cut_tail(w):
    ring = keyed(w)
    fill(w, ring, 3)
    path, good = w.data / approvals.FILE, (w.data / approvals.FILE).read_bytes()
    path.write_bytes(good[:-CUT])
    assert "approvals.jsonl chain broken at row 3; repair by hand: truncate the file to its last complete line" in refusal(approvals, w, ring)
    path.write_bytes(good)
    drop_last_line(path)
    assert "approvals.jsonl chain broken by head mismatch (rows were cut or rolled back" in refusal(approvals, w, ring)


def test_labels_read_a_cut_last_line_as_intact_but_a_modified_row_is_broken(w, capsys):
    # documented behaviour: `labels._parse` is tolerant JSONL (a cut last line is skipped) and the next append starts a fresh line
    for ref in ("aaaaaaaa", "bbbbbbbb", "cccccccc"):
        labels.append_row(w.cfg, "fp", ref)
    path = w.data / "labels.jsonl"
    full = path.read_bytes()
    path.write_bytes(full[:-CUT])
    rows, broken = labels.read_chain(w.cfg)
    assert (len(rows), broken) == (2, None)
    assert report_json(w, capsys)["labels"] == {"broken_at": None, "chain_ok": True}
    labels.append_row(w.cfg, "tp", "dddddddd")
    rows, broken = labels.read_chain(w.cfg)
    assert [r["ref"] for r in rows] == ["aaaaaaaa", "bbbbbbbb", "dddddddd"] and broken is None
    recs = [json.loads(x) for x in full.decode().splitlines()]
    recs[1]["label"] = "tp"                                                       # a modified middle row
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs))
    rows, broken = labels.read_chain(w.cfg)
    assert (len(rows), broken) == (1, 2)
    assert report_json(w, capsys)["labels"] == {"broken_at": 2, "chain_ok": False}
    assert "labels: chain BROKEN at row 2, labels ignored" in report_text(w, capsys)
    with pytest.raises(labels.Refusal, match="labels.jsonl chain broken at row 2"):
        labels.check_chain(w.cfg)


# ------------------------------------------------------------------ 3. both chain files deleted, the signed heads file remains
def test_deleting_both_chain_files_reads_as_empty_rows_with_a_head_mismatch_and_every_writer_refuses(w, capsys):
    # The documented behaviour of chains.head_state (not a choice made here): a missing file is "empty" ([]), and an empty chain whose signed
    # head is not GENESIS is a head mismatch. So deleting the files does not hide the rows: it reads as a broken chain, the resolver caps
    # at observe, and no writer extends it. Only `keygen --rehead` (TTY, lists what is gone, asks) moves the heads.
    ring = keyed(w)
    fill(w, ring, 2)
    for mod in (approvals, dm):
        (w.data / mod.FILE).unlink()
    assert modes.read_approvals(w.data, ring) == ([], "head_mismatch")
    assert modes.read_demotions(w.data, ring) == ([], "head_mismatch")
    assert modes.chain_notice("head_mismatch").startswith("demotions.jsonl is broken by head mismatch (")
    rep = report_json(w, capsys)["demotions"]
    assert (rep["chain_ok"], rep["broken_at"], rep["rows"]) == (False, "head_mismatch", [])
    res = modes.resolve("P-00000001", {"id": "P-00000001", "mode": "block"}, [], "head_mismatch", [], "head_mismatch", NOW)
    assert (res.mode, res.cap) == ("observe", "observe") and set(res.problems) == {"approvals:head_mismatch", "demotions:head_mismatch"}
    for mod in (approvals, dm):
        assert "head mismatch" in refusal(mod, w, ring)
        assert not (w.data / mod.FILE).stat().st_size                              # the refused writer left no row (the open creates an empty file)


def test_deleting_the_chain_files_of_a_chain_that_never_had_a_row_is_not_a_break(w):
    ring = keyed(w)                                                               # keygen's state: both heads GENESIS, no chain files
    assert ring.heads()["approvals"] == ring.heads()["demotions"] == GENESIS
    assert modes.read_approvals(w.data, ring) == ([], None) and modes.read_demotions(w.data, ring) == ([], None)
    add(approvals, w.data, ring, 1)
    (w.data / approvals.FILE).unlink()
    assert modes.read_approvals(w.data, ring) == ([], "head_mismatch")            # only the chain that had rows
    assert modes.read_demotions(w.data, ring) == ([], None)


# ------------------------------------------------------------------ 4. heads file deleted, replayed or re-signed
def test_a_deleted_heads_file_breaks_both_chains_as_heads_missing_and_no_writer_extends_them(w, capsys):
    ring = keyed(w)
    fill(w, ring, 2)
    ring.heads_path.unlink()
    assert modes.read_approvals(w.data, ring)[1] == modes.read_demotions(w.data, ring)[1] == "heads_missing"
    assert len(modes.read_approvals(w.data, ring)[0]) == 2                        # the rows themselves verify: only the pin is gone
    assert modes.chain_notice("heads_missing").startswith("demotions.jsonl is broken by a missing or invalid chain_heads.json (")
    assert report_json(w, capsys)["demotions"]["broken_at"] == "heads_missing"
    for mod in (approvals, dm):
        assert "chain_heads.json" in refusal(mod, w, ring)


def test_an_older_valid_heads_file_replayed_over_longer_chains_is_a_head_mismatch_per_chain(w, capsys):
    ring = keyed(w)
    add(approvals, w.data, ring, 1)
    add(dm, w.data, ring, 1)
    old = ring.heads_path.read_bytes()                                            # a genuine, MAC-valid heads file from the past
    add(approvals, w.data, ring, 2)
    ring.heads_path.write_bytes(old)
    assert ring.heads() is not None                                               # the replay is a valid file: only the chain tells it apart
    rows, broken = modes.read_approvals(w.data, ring)
    assert (len(rows), broken) == (2, "head_mismatch")                            # approvals moved on: its head is stale
    assert modes.read_demotions(w.data, ring) == (dm.read_chain(w.data, ring)[0], None) and broken != modes.read_demotions(w.data, ring)[1]
    assert "head mismatch" in refusal(approvals, w, ring)
    assert report_json(w, capsys)["demotions"]["chain_ok"] is True


def test_heads_signed_under_another_key_are_heads_missing_even_when_they_name_the_real_tails(w):
    ring = keyed(w)
    fill(w, ring, 2)
    tails = {"approvals": json.loads(lines(w, approvals)[-1])["hash"], "demotions": json.loads(lines(w, dm)[-1])["hash"]}
    assert ring.heads()["approvals"] == tails["approvals"]
    Keyring(K2, ring.heads_path).update(**tails)                                  # the forger re-signs the heads with its own key
    assert ring.heads() is None
    assert modes.read_approvals(w.data, ring)[1] == modes.read_demotions(w.data, ring)[1] == "heads_missing"


# ------------------------------------------------------------------ 5. a row re-signed under a different key
@pytest.mark.parametrize("mod", MODS)
def test_a_middle_row_re_signed_under_a_different_key_is_broken_at_that_row(w, mod):
    ring = keyed(w)
    for i in (1, 2, 3):
        add(mod, w.data, ring, i)
    rows = [json.loads(x) for x in lines(w, mod)]
    rows[1] = {**rows[1], "hash": keyed_hash(K2, rows[1])}                        # same content, the forger's MAC; later links no longer match
    (w.data / mod.FILE).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    got, broken = READ[mod](w.data, ring)
    assert len(got) == 1 and number(mod, broken) == 2
    assert chains.hash_ok(rows[1], Keyring(K2)) and not chains.hash_ok(rows[1], ring)
    assert "chain broken at row 2" in refusal(mod, w, ring)


@pytest.mark.parametrize("mod", MODS)
def test_a_whole_chain_re_signed_under_another_key_is_broken_at_row_one_even_with_matching_forged_heads(w, mod):
    ring = keyed(w)
    for i in (1, 2):
        add(mod, w.data, ring, i)
    prev, out = GENESIS, []
    for r in (json.loads(x) for x in lines(w, mod)):
        r = {**r, "prev_hash": prev}
        r["hash"] = prev = keyed_hash(K2, r)
        out.append(r)
    (w.data / mod.FILE).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in out))
    Keyring(K2, ring.heads_path).update(**{mod.FILE.split(".")[0]: prev})         # the forger cannot make the heads verify under the real key
    got, broken = READ[mod](w.data, ring)
    assert got == [] and number(mod, broken) == 1


# ------------------------------------------------------------------ 6. the accepted residual
@pytest.mark.parametrize("mod", MODS)
def test_restoring_an_old_chain_together_with_its_old_heads_file_is_accepted_the_residual_of_section_9(w, mod):
    # compiler.md section 9: heads close tail truncation and rollback of the chain ALONE; a same-user process that restores an old chain
    # TOGETHER with its old (MAC-valid) heads file is not detected. This test pins that limit so a change to it is deliberate.
    ring = keyed(w)
    add(mod, w.data, ring, 1)
    old_chain, old_heads = (w.data / mod.FILE).read_bytes(), ring.heads_path.read_bytes()
    add(mod, w.data, ring, 2)
    (w.data / mod.FILE).write_bytes(old_chain)
    ring.heads_path.write_bytes(old_heads)
    rows, broken = READ[mod](w.data, ring)
    assert (len(rows), broken) == (1, None)
