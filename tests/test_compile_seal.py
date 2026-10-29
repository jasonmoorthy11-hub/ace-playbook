"""`compile seal` (docs/design/compiler.md section 8, "Seal (as built, executed 2026-10-29)"): the Frozen instruments block, the
dry run, and the TTY-only rewrite of the pre-registration's shadow-mode heading. Invented ids and hashes only."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import approvals, catalogue, report, seal

from .compile_support import tree_state
from .test_compile_keyed_heads import keyed

ROOT = Path(__file__).resolve().parent.parent
DRAFT, SEALED = "## Shadow-mode additions (DRAFT until sealed)", "## Shadow-mode additions (SEALED 2026-11-01 prereg-v1)"
_LIVE_HEAD = re.compile(r"^## Shadow-mode additions \(SEALED [^)\n]*\)$", re.M)
_LIVE_BLOCK = re.compile(r"^```text\ndetectors:\n.*?^sealed_at \S+\n```$", re.M | re.S)


def unsealed(text: str) -> str:
    """The live pre-registration as it was before `compile seal` ran (2026-10-29): the DRAFT heading and the filler line."""
    text = _LIVE_HEAD.sub(DRAFT, text, count=1)
    return _LIVE_BLOCK.sub("(filled by compile seal)", text, count=1)
FAKE = {"zeta_detector": "2" * 64, "alpha_detector": "1" * 64}
STRENGTH = {"zeta_detector": "weak", "alpha_detector": "strong"}
SHADOW, PERMUT = "c" * 64, "d" * 64                  # private seed values (never printed); the block holds only their sha256


def sha(seed):
    return hashlib.sha256(seed.encode()).hexdigest()


SEED = sha(SHADOW)
STAMP = "2026-11-01T12:00:00Z"


@pytest.fixture
def world(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    prereg = tmp_path / "PREREGISTRATION.md"
    prereg.write_text(unsealed((ROOT / "PREREGISTRATION.md").read_text()))
    monkeypatch.setattr(seal, "PREREG", prereg)
    monkeypatch.setattr(seal, "now_iso", lambda: STAMP)
    monkeypatch.setattr(catalogue, "source_table", lambda: dict(FAKE))
    monkeypatch.setattr(catalogue, "CATALOGUE", {n: SimpleNamespace(strength=s) for n, s in STRENGTH.items()})
    return cfgmod.Config(data_dir=data, chain_key_path=None), prereg


@pytest.fixture
def seeded(world):
    """`world` with both seeds and the arm rate set in the config, so the block carries two sha256 lines and `arm_rate 0.5`."""
    cfg, prereg = world
    return cfgmod.Config(data_dir=cfg.data_dir, chain_key_path=None, shadow_seed=SHADOW, permutation_seed=PERMUT, arm_rate=0.5), prereg


@pytest.fixture
def ready(seeded):
    """`seeded`: the committed text already holds the entry gate and the horizon line, so nothing is filled by hand any more."""
    return seeded


def row(cfg, pid, mode, ts, ring=None):
    approvals.append(cfg.data_dir, {"proposal_id": pid, "proposal_sha256": "-" if mode == "revoked" else "a" * 64, "mode": mode,
                                    "ts": ts}, ring)


def go(cfg, dry=True, answer="y", tty=True):
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return answer
    return seal.run(cfg, argparse.Namespace(dry_run=dry), ask, lambda what: tty), asked


def break_chain(cfg):
    with open(Path(cfg.data_dir) / approvals.FILE, "a") as fh:
        fh.write("not json\n")


def test_the_dry_run_prints_the_block_in_the_documented_format(world, capsys):
    # proposals: the newest row per id that is not revoked; measurement_start: the greatest newest-observe ts over those ids (measure.start_of)
    cfg, _ = world
    row(cfg, "P-00000002", "observe", "2026-10-18T00:00:00Z")
    row(cfg, "P-00000002", "warn", "2026-10-20T00:00:00Z")
    row(cfg, "P-00000001", "observe", "2026-10-19T00:00:00Z")
    assert go(cfg)[0] == 0
    assert capsys.readouterr().out == (
        f"detectors:\n  alpha_detector strong {'1' * 64}\n  zeta_detector weak {'2' * 64}\nreport_sha256 {report.source_sha256()}\n"
        f"proposals:\n  P-00000001 observe {'a' * 64}\n  P-00000002 warn {'a' * 64}\nshadow_seed_sha256 -\npermutation_seed_sha256 -\narm_rate -\n"
        "measurement_start 2026-10-19T00:00:00Z\nsealed_at -\n")


def test_the_dry_run_writes_nothing(world):
    cfg, prereg = world
    row(cfg, "P-00000001", "observe", "2026-10-19T00:00:00Z")
    before, text = tree_state(cfg.data_dir), prereg.read_bytes()
    assert go(cfg, tty=False)[0] == 0                       # no terminal needed, nothing asked
    assert tree_state(cfg.data_dir) == before and prereg.read_bytes() == text


def test_the_block_lists_every_catalogue_detector_with_its_real_hash(world, monkeypatch, capsys):
    cfg, _ = world
    monkeypatch.undo()                                      # the real source_table and the real catalogue
    assert go(cfg)[0] == 0
    lines = capsys.readouterr().out.splitlines()
    names = sorted(catalogue.CATALOGUE)
    assert lines[0] == "detectors:" and lines[1:1 + len(names)] == [f"  {n} {catalogue.CATALOGUE[n].strength} {catalogue.CATALOGUE[n].source_sha256}" for n in names]
    assert all(re.fullmatch(r"  [a-z_0-9]+ (strong|weak) [0-9a-f]{64}", ln) for ln in lines[1:1 + len(names)])


def test_measurement_start_is_the_newest_observe_row_and_a_revoked_proposal_is_skipped(world, capsys):
    cfg, _ = world
    row(cfg, "P-00000001", "observe", "2026-10-19T00:00:00Z")
    row(cfg, "P-00000002", "observe", "2026-10-20T00:00:00Z")
    row(cfg, "P-00000002", "revoked", "2026-10-21T00:00:00Z")
    row(cfg, "P-00000001", "warn", "2026-10-22T00:00:00Z")
    assert go(cfg)[0] == 0
    out = capsys.readouterr().out
    assert f"  P-00000001 warn {'a' * 64}\n" in out and "P-00000002" not in out and "measurement_start 2026-10-19T00:00:00Z" in out
    row(cfg, "P-00000001", "revoked", "2026-10-23T00:00:00Z")
    go(cfg)
    out = capsys.readouterr().out
    assert "P-0000" not in out and "measurement_start -\n" in out and "proposals:\n  (none)\n" in out


def test_a_keyed_chain_is_read_with_the_key(world, tmp_path, capsys):
    cfg, _ = world
    key = tmp_path / "chain_key"
    cfg = cfgmod.Config(data_dir=cfg.data_dir, chain_key_path=key)
    ring = keyed(SimpleNamespace(cfg=cfg))                  # key file and empty signed heads, as `keygen` leaves them
    row(cfg, "P-00000001", "observe", "2026-10-19T00:00:00Z", ring)
    assert go(cfg)[0] == 0
    assert f"  P-00000001 observe {'a' * 64}\n" in capsys.readouterr().out


def test_a_broken_chain_is_reported_and_the_dry_run_still_exits_0(world, capsys):
    cfg, _ = world
    row(cfg, "P-00000001", "observe", "2026-10-19T00:00:00Z")
    break_chain(cfg)
    assert go(cfg)[0] == 0
    out = capsys.readouterr().out
    assert f"proposals:\n  (approvals chain broken at row 2)\n  P-00000001 observe {'a' * 64}\n" in out and out.endswith("sealed_at -\n")


def test_seal_rewrites_the_heading_once_and_refuses_the_second_time(ready, capsys):
    cfg, prereg = ready
    row(cfg, "P-00000001", "observe", "2026-10-19T00:00:00Z")
    code, asked = go(cfg, dry=False)
    text = prereg.read_text()
    assert code == 0 and len(asked) == 1 and asked[0].endswith("[y/N] ")
    assert SEALED in text and DRAFT not in text and text.count("## Shadow-mode additions") == 1
    assert "(filled by compile seal)" not in text
    block = re.search(r"### Frozen instruments\n.*?```text\n(.*?)```", text, re.S).group(1)
    assert block.startswith("detectors:\n  alpha_detector strong ") and f"sealed_at {STAMP}\n" in block and "sealed_at -" not in block
    assert f"shadow_seed_sha256 {SEED}\npermutation_seed_sha256 {sha(PERMUT)}\narm_rate 0.5\n" in block
    assert block.replace(f"sealed_at {STAMP}", "sealed_at -") == seal.frozen_block(cfg)
    capsys.readouterr()
    again, asked = go(cfg, dry=False)
    assert again == 1 and asked == [] and prereg.read_text() == text
    assert "already sealed" in capsys.readouterr().err


def test_seal_prints_exactly_the_two_git_commands_and_never_runs_git(ready, capsys, monkeypatch):
    cfg, _ = ready
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("seal must not run git"))
    assert go(cfg, dry=False)[0] == 0
    repo = Path(seal.__file__).resolve().parents[2]
    tail = capsys.readouterr().out.splitlines()[-2:]
    assert tail == [f'git -C {repo} tag -a prereg-v1 -m "pre-registration sealed 2026-11-01"', f"git -C {repo} push origin prereg-v1"]


def test_seal_without_a_terminal_refuses_and_writes_nothing(world):
    cfg, prereg = world
    before = prereg.read_bytes()
    code, asked = go(cfg, dry=False, tty=False)
    assert code == 2 and asked == [] and prereg.read_bytes() == before


@pytest.mark.parametrize("answer", ["n", "", "no", "x"])
def test_anything_but_yes_writes_nothing(ready, answer):
    cfg, prereg = ready
    before = prereg.read_bytes()
    code, asked = go(cfg, dry=False, answer=answer)
    assert code == 1 and len(asked) == 1 and prereg.read_bytes() == before


def test_a_broken_chain_is_not_sealed(ready, capsys):
    cfg, prereg = ready
    row(cfg, "P-00000001", "observe", "2026-10-19T00:00:00Z")
    break_chain(cfg)
    before = prereg.read_bytes()
    code, asked = go(cfg, dry=False)
    assert code == 1 and asked == [] and prereg.read_bytes() == before and "chain broken" in capsys.readouterr().err


def test_a_prereg_without_the_section_is_a_usage_error(world):
    cfg, prereg = world
    prereg.write_text("# nothing\n")
    assert go(cfg, dry=False)[0] == 2


def test_the_section_text_has_its_eight_subsections_and_the_filler_line():
    text = unsealed((ROOT / "PREREGISTRATION.md").read_text())
    assert text.count(DRAFT) == 1 and text.index(DRAFT) < text.index("\n## Amendments\n")
    section = text[text.index(DRAFT):text.index("\n## Amendments\n")]
    heads = re.findall(r"^### (.*)$", section, re.M)
    assert heads == ["Effect estimate (compiled path, descriptive)", "Eligibility", "Arm assignment", "Spillover", "Timing and stopping",
                     "Null handling", "Frozen instruments", "Amendments"]
    assert section.count("\n(filled by compile seal)\n") == 1
    assert "10,000 Bernoulli(`arm_rate`) re-draws" in section and "metrics.wilson" in section and "arm_rate" in section
    assert "N per detector" not in section and seal.HAND not in section and "One primary per detector" in section
    assert "`name strength sha256`" in section and "`P-id mode proposal_sha256`" in section and "`ACE P-`" in section
    assert "Automated sessions are not eligible" in section and "Interactive and automated sessions alike" not in section
    assert "a Bash tool can read any file the operator can" in section and "intention to treat" in section
    tm = section.index("### Timing and stopping")
    assert seal._timing(section, tm, section.index("### Null handling")) is None         # the committed text states the gate and the horizon


def test_the_cli_wires_seal_dry_run(tmp_path, capsys, monkeypatch):
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "chain_key_path": None}))
    (tmp_path / "data").mkdir()
    monkeypatch.delenv("ACE_PLAYBOOK_KEYED", raising=False)
    assert cli.main(["--config", str(conf), "seal", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("detectors:\n") and "report_sha256 " in out and "proposals:\n  (none)\n" in out
