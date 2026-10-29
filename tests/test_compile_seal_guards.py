"""`compile seal` refusals (docs/design/compiler.md section 8, "Seal (as built, not executed)"): the order of the checks, the anchored
heading rewrite, the filler slice, the N placeholder, the seed commitment, a file that changes under the prompt. Invented ids only."""

import argparse
import os
import re
from pathlib import Path

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.compile import chains, seal

from .test_compile_seal import DRAFT, PERMUT, SEALED, SEED, SHADOW, ready, row, seeded, sha, world  # noqa: F401  (fixtures)
from .test_compile_seal import go

FILLER_LINE = "\n(filled by compile seal)\n"


def real(cfg, prereg, ask=lambda prompt: "y"):
    asked = []
    code = seal.run(cfg, argparse.Namespace(dry_run=False), lambda p: asked.append(p) or ask(p), lambda what: True)
    return code, asked


def test_a_keyed_install_without_a_usable_key_is_reported_and_refused(ready, tmp_path, capsys):
    cfg, prereg = ready
    (tmp_path / chains.HEADS).write_text("{}")                  # signed heads in play, no key file
    cfg = cfgmod.Config(data_dir=cfg.data_dir, chain_key_path=tmp_path / "key")
    assert go(cfg)[0] == 0
    assert "proposals:\n  (approvals chain broken because the install is keyed and the chain key is missing" in capsys.readouterr().out
    before = prereg.read_bytes()
    code, asked = real(cfg, prereg)
    assert code == 1 and asked == [] and prereg.read_bytes() == before
    assert "chain key is missing or unusable" in capsys.readouterr().err


def test_an_unreadable_chain_is_reported_and_refused(ready, tmp_path, capsys):
    cfg, prereg = ready
    (tmp_path / "elsewhere.jsonl").write_text("")
    os.symlink(tmp_path / "elsewhere.jsonl", Path(cfg.data_dir) / "approvals.jsonl")
    assert go(cfg)[0] == 0
    assert "proposals:\n  (approvals chain unreadable)\n" in capsys.readouterr().out
    before = prereg.read_bytes()
    code, asked = real(cfg, prereg)
    assert code == 1 and asked == [] and prereg.read_bytes() == before and "unreadable" in capsys.readouterr().err


def test_already_sealed_wins_over_the_filler_still_being_present(ready, capsys):
    cfg, prereg = ready
    prereg.write_text(prereg.read_text().replace(DRAFT, SEALED))
    assert FILLER_LINE in prereg.read_text()
    before = prereg.read_bytes()
    code, asked = real(cfg, prereg)
    assert code == 1 and asked == [] and prereg.read_bytes() == before and "already sealed" in capsys.readouterr().err


def test_only_the_real_heading_is_rewritten_when_the_text_names_it_earlier(ready):
    cfg, prereg = ready
    mention = f"An earlier line quoting {DRAFT} inline.\n"
    prereg.write_text(prereg.read_text().replace("\n## Shadow-mode additions", f"\n{mention}\n## Shadow-mode additions", 1))
    assert real(cfg, prereg)[0] == 0
    text = prereg.read_text()
    assert mention in text and text.count(DRAFT) == 1 and text.count(SEALED) == 1
    assert f"\n{SEALED}\n" in text and f"\n{DRAFT}\n" not in text and FILLER_LINE not in text


def test_a_second_shadow_mode_heading_is_a_usage_error(ready, capsys):
    cfg, prereg = ready
    prereg.write_text(prereg.read_text().rstrip("\n") + f"\n\n{SEALED}\n")
    before = prereg.read_bytes()
    code, asked = real(cfg, prereg)
    assert code == 2 and asked == [] and prereg.read_bytes() == before and "exactly one" in capsys.readouterr().err


def test_the_filler_must_sit_in_the_frozen_instruments_subsection(ready):
    cfg, prereg = ready
    text = prereg.read_text()
    moved = text.replace(FILLER_LINE, "\n", 1).rstrip("\n") + f"\n{FILLER_LINE}"       # now under the top-level Amendments heading
    assert moved.index(FILLER_LINE) > moved.index("\n## Amendments\n")
    prereg.write_text(moved)
    assert real(cfg, prereg)[0] == 2 and prereg.read_text() == moved


def test_a_duplicated_filler_is_a_usage_error(ready):
    cfg, prereg = ready
    twice = prereg.read_text().replace(FILLER_LINE, FILLER_LINE + FILLER_LINE, 1)
    prereg.write_text(twice)
    code, asked = real(cfg, prereg)
    assert code == 2 and asked == [] and prereg.read_text() == twice


GATE = "at least 30 human-labelled sessions-with-fire"
HORIZON = "Horizon (randomized phase): 12 calendar weeks from the detector's arm start or 400 eligible"


def edited(prereg, old, new):
    text = prereg.read_text()
    assert old in text, old
    prereg.write_text(text.replace(old, new, 1))                    # the first hit is in the shadow-mode section (the Amendments quote it later)
    return prereg.read_bytes()


def refused(cfg, prereg, before, capsys, needle):
    code, asked = real(cfg, prereg)
    err = capsys.readouterr().err
    assert code == 1 and asked == [] and prereg.read_bytes() == before and needle in err
    return err


@pytest.mark.parametrize("wrong", ["at least 30 human-labelled fires", "at least 29 human-labelled sessions-with-fire",
                                   "at least 15 human-labelled sessions-with-fire", "30 sessions"])
def test_the_entry_gate_must_be_stated_over_sessions_and_at_least_30(ready, capsys, wrong):
    cfg, prereg = ready
    refused(cfg, prereg, edited(prereg, GATE, wrong), capsys, "entry gate")


@pytest.mark.parametrize("drop", ["0 FP", "0 unlabelled"])
def test_the_gate_needs_zero_fp_and_zero_unlabelled(ready, capsys, drop):
    cfg, prereg = ready
    refused(cfg, prereg, edited(prereg, f", {drop}", ""), capsys, "entry gate")


def test_the_gate_deleted_refuses_and_a_second_gate_refuses(ready, capsys):
    cfg, prereg = ready
    before = prereg.read_bytes()
    refused(cfg, prereg, edited(prereg, GATE, "a gate"), capsys, "entry gate")
    prereg.write_bytes(before)
    refused(cfg, prereg, edited(prereg, "Horizon (", f"Also {GATE}, 0 FP, 0 unlabelled.\nHorizon ("), capsys, "entry gate")


@pytest.mark.parametrize("wrong", ["Horizon (randomized phase): 12 weeks from the detector's arm start or 400 eligible",
                                   "Horizon (randomized phase): 12 calendar weeks from the detector's arm start or 400 sessions",
                                   "Time limit (randomized phase): 12 calendar weeks from the detector's arm start or 400 eligible"])
def test_the_horizon_line_must_state_weeks_and_eligible_sessions(ready, capsys, wrong):
    cfg, prereg = ready
    refused(cfg, prereg, edited(prereg, HORIZON, wrong), capsys, "Horizon")


def test_the_old_hand_fill_placeholder_still_refuses(ready, capsys):
    cfg, prereg = ready
    refused(cfg, prereg, edited(prereg, "### Null handling\n", f"### Null handling\n\n{seal.HAND}\n"), capsys, seal.HAND)


@pytest.mark.parametrize("field", ["shadow_seed", "permutation_seed", "arm_rate"])
def test_each_unset_arm_field_refuses_alone_and_is_named(ready, capsys, field):
    cfg, prereg = ready
    cfg = cfgmod.Config(data_dir=cfg.data_dir, **{**dict(shadow_seed=SHADOW, permutation_seed=PERMUT, arm_rate=0.5), field: None})
    before = prereg.read_bytes()
    assert refused(cfg, prereg, before, capsys, field) and seal.unset(cfg) == [field]
    block = seal.frozen_block(cfg)
    assert {"shadow_seed": f"shadow_seed_sha256 -\n", "permutation_seed": "permutation_seed_sha256 -\n", "arm_rate": "arm_rate -\n"}[field] in block


@pytest.mark.parametrize("bad", [{"shadow_seed": "short"}, {"permutation_seed": "G" * 64}, {"shadow_seed": SHADOW.upper()},
                                 {"arm_rate": 0.0}, {"arm_rate": 1.0}, {"arm_rate": True}, {"arm_rate": "0.5"}])
def test_a_set_but_unusable_arm_field_is_a_config_error_that_never_prints_the_value(bad):
    with pytest.raises(ValueError) as err:
        cfgmod.from_dict({"data_dir": "/x", **bad})
    key, value = next(iter(bad.items()))
    assert key in str(err.value) and (not isinstance(value, str) or value not in str(err.value))


def test_the_seed_values_never_appear_in_any_output_or_in_the_sealed_file(ready, capsys):
    cfg, prereg = ready
    row(cfg, "P-00000001", "observe", "2026-10-19T00:00:00Z")
    assert go(cfg)[0] == 0                                                   # dry run
    assert go(cfg, dry=False)[0] == 0                                        # the real seal
    captured = capsys.readouterr()
    text = prereg.read_text()
    for seed in (SHADOW, PERMUT):
        assert seed not in captured.out + captured.err + text and seed not in repr(cfg) and seed not in str(cfg)
        assert sha(seed) in text
    block = re.search(r"```text\n(.*?)```", text, re.S).group(1)
    assert f"shadow_seed_sha256 {sha(SHADOW)}\npermutation_seed_sha256 {sha(PERMUT)}\narm_rate 0.5\n" in block


def test_every_check_before_the_chain_and_the_prompt_leaves_the_file_alone(seeded, capsys):
    cfg, prereg = seeded
    before = edited(prereg, GATE, "at least 30 human-labelled fires")
    assert refused(cfg, prereg, before, capsys, "entry gate")


def test_an_indented_duplicate_heading_is_a_usage_error(ready):
    cfg, prereg = ready
    prereg.write_text(prereg.read_text().rstrip("\n") + "\n\n  ##  Shadow-mode additions (DRAFT until sealed)\n")
    before = prereg.read_bytes()
    code, asked = real(cfg, prereg)
    assert code == 2 and asked == [] and prereg.read_bytes() == before


def test_the_real_block_has_no_seed_hash_so_the_seal_refuses(world, capsys):
    cfg, prereg = world
    before = prereg.read_bytes()
    code, asked = real(cfg, prereg)
    err = capsys.readouterr().err
    assert code == 1 and asked == [] and prereg.read_bytes() == before and "shadow_seed, permutation_seed, arm_rate" in err
    assert go(cfg)[0] == 0 and "shadow_seed_sha256 -\n" in capsys.readouterr().out          # the dry run still prints the placeholder


def test_a_file_changed_under_the_prompt_is_not_overwritten(ready, capsys):
    cfg, prereg = ready
    edited = prereg.read_text() + "\nedited by hand while the prompt was open\n"
    code, asked = real(cfg, prereg, ask=lambda p: (prereg.write_text(edited), "y")[1])
    assert code == 1 and len(asked) == 1 and prereg.read_text() == edited
    assert "changed while the prompt was open" in capsys.readouterr().err


def test_a_failed_write_leaves_no_temp_file_and_the_original(ready, monkeypatch):
    cfg, prereg = ready
    before = prereg.read_bytes()
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        real(cfg, prereg)
    assert prereg.read_bytes() == before and sorted(p.name for p in prereg.parent.iterdir() if p.is_file()) == [prereg.name]
