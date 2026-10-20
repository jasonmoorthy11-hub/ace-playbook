"""`compile seal` refusals (docs/design/compiler.md section 8, "Seal (as built, not executed)"): the order of the checks, the anchored
heading rewrite, the filler slice, the N placeholder, the seed commitment, a file that changes under the prompt. Invented ids only."""

import argparse
import os
from pathlib import Path

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.compile import chains, seal

from .test_compile_seal import DRAFT, SEALED, SEED, ready, row, seeded, world  # noqa: F401  (fixtures)
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


def test_an_unfilled_n_refuses_before_the_chain_and_the_prompt(seeded, capsys):
    cfg, prereg = seeded
    before = prereg.read_bytes()
    assert seal.HAND.encode() in before
    code, asked = real(cfg, prereg)
    err = capsys.readouterr().err
    assert code == 1 and asked == [] and prereg.read_bytes() == before and "N per detector" in err and seal.HAND in err


def test_the_whole_n_line_deleted_refuses_too(ready, capsys):
    cfg, prereg = ready
    text = prereg.read_text()
    assert "\nN per detector: 40\n" in text
    prereg.write_text(text.replace("\nN per detector: 40\n", "\n", 1))
    before = prereg.read_bytes()
    code, asked = real(cfg, prereg)
    assert code == 1 and asked == [] and prereg.read_bytes() == before and "N per detector" in capsys.readouterr().err


def test_two_n_lines_refuse(ready):
    cfg, prereg = ready
    prereg.write_text(prereg.read_text().replace("\nN per detector: 40\n", "\nN per detector: 40\nN per detector: 50\n", 1))
    before = prereg.read_bytes()
    code, asked = real(cfg, prereg)
    assert code == 1 and asked == [] and prereg.read_bytes() == before


@pytest.mark.parametrize("value", ["-", "TBD", "n/a"])
def test_an_n_value_without_a_number_refuses(ready, value):
    cfg, prereg = ready
    prereg.write_text(prereg.read_text().replace("\nN per detector: 40\n", f"\nN per detector: {value}\n", 1))
    before = prereg.read_bytes()
    code, asked = real(cfg, prereg)
    assert code == 1 and asked == [] and prereg.read_bytes() == before


@pytest.mark.parametrize("keep", ["seed", "arm"])
def test_each_half_of_the_seed_and_arm_rate_refusal_refuses_alone(world, monkeypatch, capsys, keep):
    orig = seal.frozen_block
    fix = (lambda b: b.replace("shadow_seed_sha256 -", f"shadow_seed_sha256 {SEED}")) if keep == "seed" else (lambda b: b.replace(seal.PLANNED, "arm_rate 0.5"))
    monkeypatch.setattr(seal, "frozen_block", lambda cfg, sealed_at="-": fix(orig(cfg, sealed_at)))
    cfg, prereg = world
    prereg.write_text(prereg.read_text().replace(seal.HAND, "40", 1))
    before = prereg.read_bytes()
    code, asked = real(cfg, prereg)
    assert code == 1 and asked == [] and prereg.read_bytes() == before
    assert "shadow_seed_sha256" in capsys.readouterr().err


def test_an_indented_duplicate_heading_is_a_usage_error(ready):
    cfg, prereg = ready
    prereg.write_text(prereg.read_text().rstrip("\n") + "\n\n  ##  Shadow-mode additions (DRAFT until sealed)\n")
    before = prereg.read_bytes()
    code, asked = real(cfg, prereg)
    assert code == 2 and asked == [] and prereg.read_bytes() == before


def test_the_real_block_has_no_seed_hash_so_the_seal_refuses(world, capsys):
    cfg, prereg = world
    prereg.write_text(prereg.read_text().replace(seal.HAND, "40"))
    before = prereg.read_bytes()
    code, asked = real(cfg, prereg)
    err = capsys.readouterr().err
    assert code == 1 and asked == [] and prereg.read_bytes() == before and "shadow_seed_sha256" in err and "arm_rate" in err
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
