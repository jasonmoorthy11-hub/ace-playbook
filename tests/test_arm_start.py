"""`python -m ace_playbook.compile.arm_start` (sealed Pre-arm-start conditions (2) and (6), the `arm_start` line of Arm assignment, and the
Amendments entry that names TEN files): the refusals, the entry, the signed marker, the operator lines. A tmp world: a data dir with a valid
approvals chain, a PREREGISTRATION.md copy made SEALED again, a tmp git repo holding copies of the ten files. Invented ids, seeds and stamps."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.compile import approvals, arm_start, arms_window, catalogue, chains, demotions, phase, seal

from . import compile_support as cs
from .test_compile_keyed_heads import keyed
from .test_compile_seal import unsealed

ROOT = Path(__file__).resolve().parent.parent
DET, SHADOW, PERMUT = "rm_rf_nonscratch", "ab" * 32, "cd" * 32
SEALED_AT, APPROVED, STAMP, NOW = "2026-10-30T00:00:00Z", "2026-10-31T00:00:00Z", "2026-11-01T00:00:00Z", "2026-11-01T00:00:05Z"
DEMOTION = {"from_mode": "warn", "to_mode": "observe", "cause": "label_fp", "ref": "0123abcd", "review_required": False}
GIT = ["git", "-c", "user.name=t", "-c", "user.email=" + "t" + "@" + "t.invalid", "-c", "commit.gpgsign=false"]      # assembled: no address literal


def sealed(text: str) -> str:
    """Invert `unsealed`: the SEALED heading and a frozen block that holds `sealed_at`."""
    block = f"```text\nshadow_seed_sha256 {seal._sha(SHADOW)}\npermutation_seed_sha256 {seal._sha(PERMUT)}\narm_rate 0.5\nsealed_at {SEALED_AT}\n```"
    text = text.replace("## Shadow-mode additions (DRAFT until sealed)", "## Shadow-mode additions (SEALED 2026-10-30 prereg-v1)", 1)
    return text.replace("(filled by compile seal)", block, 1)


def git(repo, *args):
    return subprocess.run([*GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.w = cs.real_world(tmp_path)
        self.cfg, self.data, self.pid = self.w.cfg, self.w.data, self.w.props[DET]["id"]
        self.cfg.shadow_seed, self.cfg.permutation_seed, self.cfg.arm_rate = SHADOW, PERMUT, 0.5
        self.prereg = tmp_path / "PREREGISTRATION.md"
        self.prereg.write_text(sealed(unsealed((ROOT / "PREREGISTRATION.md").read_text())))
        self.repo = tmp_path / "repo"
        shutil.copytree(ROOT / "ace_playbook" / "compile", self.repo / arm_start.SUBDIR, ignore=shutil.ignore_patterns("__pycache__", "watch"))
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "init")
        monkeypatch.setattr(seal, "PREREG", self.prereg)
        monkeypatch.setattr(arm_start, "REPO", self.repo)
        monkeypatch.setattr(arm_start, "now_iso", lambda: NOW)
        cs.approve_file(self.cfg, self.w.props[DET], "warn", row=True, ts=APPROVED)
        self.answers, self.prompts = ["y"], []

    def ask(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0)

    def go(self, *argv, tty=True, cfg=None):
        ns = arm_start.parser().parse_args(list(argv))
        return arm_start.run(cfg or self.cfg, ns, self.ask, lambda what: tty)

    @property
    def head(self):
        return approvals.read_chain(self.data, chains.key_of(self.cfg))[0][-1]["hash"]

    @property
    def line(self):
        return f"arm_start {DET} {STAMP} approvals={self.head}"


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def refused(world, capsys, needle, *argv, code=1, **kw):
    before = world.prereg.read_text()
    assert world.go(DET, "--at", STAMP, *argv, **kw) == code
    err = capsys.readouterr().err
    assert err.startswith("error: ") and needle in err and err.count("\n") == 1
    assert world.prereg.read_text() == before and not (world.data / phase.FILE).exists()
    assert SHADOW not in err and PERMUT not in err
    return err


# ------------------------------------------------------------------ the entry
def test_the_dry_run_prints_the_entry_and_writes_nothing(world, capsys):
    before, tree = world.prereg.read_text(), cs.tree_state(world.data)
    assert world.go(DET, "--at", STAMP, "--dry-run", tty=False) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == f"### 2026-11-01 - arm start: {DET}" and world.line in lines and world.prompts == []
    assert world.prereg.read_text() == before and cs.tree_state(world.data) == tree
    head = git(world.repo, "rev-parse", "HEAD").strip()
    assert f"tree {head}" in lines and any(ln.startswith("phase marker ") and STAMP in ln for ln in lines)
    assert [ln.split()[1] for ln in lines if ln.startswith("sha256 ")] == list(arm_start.FILES) and len(arm_start.FILES) == 10
    assert f"family {world.pid}" in lines and all(len(ln) <= 160 for ln in lines)
    text = before.rstrip("\n") + "\n\n" + out.rstrip("\n") + "\n"                 # as it would be appended: the analysis finds exactly this line
    assert arms_window.arm_starts(text) == {DET: STAMP} and arms_window.arm_heads(text) == {DET: world.head}


def test_the_sha256_lines_match_the_bytes_of_the_ten_files(world, capsys):
    world.go(DET, "--at", STAMP, "--dry-run", tty=False)
    got = {ln.split()[1]: ln.split()[2] for ln in capsys.readouterr().out.splitlines() if ln.startswith("sha256 ")}
    assert got == {f: hashlib.sha256((world.repo / arm_start.SUBDIR / f).read_bytes()).hexdigest() for f in arm_start.FILES}
    assert set(arm_start.FILES) == {"shadow.py", "offline.py", "events.py", "demotions.py", "modes.py", "approvals.py", "shadow_arm.py",
                                    "shadow_guard.py", "phase.py", "offline_demote.py"}


def test_y_appends_exactly_one_entry_writes_the_marker_and_the_one_line_file(world, capsys):
    before = world.prereg.read_text()
    assert world.go(DET, "--at", STAMP) == 0
    out, new = capsys.readouterr().out, world.prereg.read_text()
    assert len(world.prompts) == 1 and new.startswith(before.rstrip("\n")) and new.count("### 2026-11-01 - arm start") == 1
    assert arms_window.arm_starts(new) == {DET: STAMP} and arms_window.arm_heads(new) == {DET: world.head}
    assert new.split("## Amendments", 1)[0] == before.split("## Amendments", 1)[0] and new.endswith("\n") and not new.endswith("\n\n")
    assert phase.load(world.cfg) == ({DET: STAMP}, False) and json.loads((world.data / phase.FILE).read_text())["ts"] == NOW
    one = world.data / f"arm_start_{DET}.txt"
    assert one.read_text() == world.line and (one.stat().st_mode & 0o777) == 0o600 and (world.data / phase.FILE).stat().st_mode & 0o777 == 0o600
    sha = hashlib.sha256(world.line.encode()).hexdigest()
    ops = out.splitlines()[-6:-1]
    repo = str(world.repo)
    assert ops[0] == f'git -C {repo} add PREREGISTRATION.md && git -C {repo} commit -m "arm start: {DET} {STAMP}"'
    assert ops[1] == f'git -C {repo} tag -s arm-{DET} -m "arm start {DET} {STAMP}"' and ops[2] == f"git -C {repo} push origin main arm-{DET}"
    assert ops[3] == f"printf '%s' '{world.line}' | shasum -a 256" and ops[4] == f"ots stamp {one}"
    assert subprocess.run(["shasum", "-a", "256", str(one)], capture_output=True, text=True).stdout.startswith(sha)


def test_the_last_printed_line_is_the_arms_report_command(world, capsys):
    world.go(DET, "--at", STAMP)
    assert capsys.readouterr().out.splitlines()[-1] == "python3 -m ace_playbook.compile.arms report"


def test_a_second_run_refuses_already_started(world, capsys):
    assert world.go(DET, "--at", STAMP) == 0
    capsys.readouterr()
    world.answers = ["y"]
    before, marker = world.prereg.read_text(), (world.data / phase.FILE).read_bytes()
    assert world.go(DET, "--at", "2026-11-02T00:00:00Z") == 1 and "already started" in capsys.readouterr().err
    assert world.prereg.read_text() == before and (world.data / phase.FILE).read_bytes() == marker


def test_the_default_stamp_is_now(world, capsys):
    assert world.go(DET, "--dry-run", tty=False) == 0 and f"arm_start {DET} {NOW} approvals=" in capsys.readouterr().out


def test_an_n_answer_writes_nothing(world, capsys):
    world.answers = ["n"]
    refused(world, capsys, "not started")
    assert not (world.data / f"arm_start_{DET}.txt").exists() and len(world.prompts) == 1


def test_a_file_that_changed_under_the_prompt_is_refused(world, capsys):
    def ask(prompt):
        world.prereg.write_text(world.prereg.read_text() + "\nextra\n")
        return "y"
    world.ask = ask
    assert world.go(DET, "--at", STAMP) == 1 and "changed while the prompt was open" in capsys.readouterr().err
    assert arms_window.arm_starts(world.prereg.read_text()) == {} and not (world.data / phase.FILE).exists()


def test_a_marker_is_merged_not_replaced(world, capsys):
    phase.write(world.cfg, {"other_detector": "2026-10-01T00:00:00Z"}, NOW)
    assert world.go(DET, "--at", STAMP) == 0
    assert phase.started(world.cfg) == {"other_detector": "2026-10-01T00:00:00Z", DET: STAMP}


def test_on_a_keyed_install_the_marker_is_signed_with_the_chain_key(tmp_path, monkeypatch, capsys):
    world = World(tmp_path, monkeypatch)
    (world.data / "approvals.jsonl").unlink()
    ring = keyed(world.w)
    rec = json.loads(cs.approve_file(world.cfg, world.w.props[DET], "warn").read_text())
    approvals.append(world.data, {"proposal_id": world.pid, "proposal_sha256": rec["proposal_sha256"], "mode": "warn", "ts": APPROVED}, ring)
    assert world.go(DET, "--at", STAMP) == 0
    assert json.loads((world.data / phase.FILE).read_text())["v"] == 2 and phase.load(world.cfg) == ({DET: STAMP}, False)
    assert arms_window.arm_heads(world.prereg.read_text())[DET] == world.head


# ------------------------------------------------------------------ the refusals (exit 1, one line, no value)
def test_refuses_an_unknown_detector(world, capsys):
    assert world.go("no_such_detector", "--at", STAMP) == 1
    assert "unknown detector" in capsys.readouterr().err and not (world.data / phase.FILE).exists()


def test_refuses_a_pre_registration_that_is_not_sealed(world, capsys):
    world.prereg.write_text(unsealed(world.prereg.read_text()))
    refused(world, capsys, "not sealed")


def test_refuses_a_stamp_earlier_than_sealed_at(world, capsys):
    refused_at = world.go(DET, "--at", "2026-10-29T23:59:59Z")
    assert refused_at == 1 and "earlier than sealed_at" in capsys.readouterr().err
    assert world.go(DET, "--at", "2026-11-01", tty=False) == 1 and "UTC stamp" in capsys.readouterr().err


def test_refuses_a_detector_that_already_has_an_arm_start_line(world, capsys):
    world.prereg.write_text(world.prereg.read_text().rstrip("\n") + f"\n\n{world.line}\n")
    refused(world, capsys, "already started")


def test_refuses_an_unreadable_or_broken_approvals_chain(world, capsys):
    with open(world.data / "approvals.jsonl", "a") as fh:
        fh.write("not json\n")
    refused(world, capsys, "approvals chain is broken at row 2")
    (world.data / "approvals.jsonl").unlink()
    (world.data / "approvals.jsonl").symlink_to(world.prereg)
    refused(world, capsys, "approvals chain is unreadable")


def test_refuses_when_no_proposal_of_the_detector_is_approved_at_warn_or_block(world, capsys):
    row = {"proposal_id": world.pid, "proposal_sha256": "-", "mode": "revoked", "ts": "2026-10-31T12:00:00Z"}
    approvals.append(world.data, row)
    refused(world, capsys, "no proposal of rm_rf_nonscratch is approved at warn or block")
    (world.data / "approvals.jsonl").unlink()
    refused(world, capsys, "approvals chain is empty")


def test_refuses_a_proposal_approved_only_at_observe(world, capsys):
    (world.data / "approvals.jsonl").unlink()
    cs.approve_file(world.cfg, world.w.props[DET], "observe", row=True, ts=APPROVED)
    refused(world, capsys, "approved at observe")


def test_refuses_a_lowering_demotion_row_in_force_and_ignores_one_that_lowers_nothing(world, capsys):
    evasion = {**DEMOTION, "to_mode": "warn", "cause": "evasion_pair", "ref": "4567cdef", "review_required": True}
    demotions.append(world.data, {"proposal_id": world.pid, **evasion, "ts": "2026-10-31T12:00:00Z"})
    assert world.go(DET, "--at", STAMP, "--dry-run", tty=False) == 0 and "arm_start " in capsys.readouterr().out
    demotions.append(world.data, {"proposal_id": world.pid, **DEMOTION, "ts": "2026-10-31T13:00:00Z"})
    refused(world, capsys, "1 demotion(s) in force")
    assert world.go(DET, "--at", "2026-10-31T12:30:00Z", "--dry-run", tty=False) == 0       # the lowering row is stamped after this start
    (world.data / "demotions.jsonl").write_text("not json\n")
    capsys.readouterr()
    refused(world, capsys, "demotions chain is broken")


def test_refuses_a_working_tree_with_uncommitted_changes_to_one_of_the_ten_files(world, capsys):
    target = world.repo / arm_start.SUBDIR / "phase.py"
    target.write_text(target.read_text() + "\n# edited\n")
    err = refused(world, capsys, "uncommitted changes")
    assert "phase.py" in err
    git(world.repo, "commit", "-qam", "edit")                                   # committed: clean again
    assert world.go(DET, "--at", STAMP, "--dry-run", tty=False) == 0
    other = world.repo / arm_start.SUBDIR / "report.py"                         # a file outside the ten does not matter
    other.write_text(other.read_text() + "\n# edited\n")
    assert world.go(DET, "--at", STAMP, "--dry-run", tty=False) == 0
    (world.repo / arm_start.SUBDIR / "modes.py").unlink()                       # a missing one is a change too
    refused(world, capsys, "uncommitted changes")


def test_refuses_a_repo_that_git_cannot_read(world, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(arm_start, "REPO", tmp_path / "nowhere")
    refused(world, capsys, "git")


def test_refuses_while_the_config_seeds_are_unset(world, capsys):
    world.cfg.shadow_seed = None
    err = refused(world, capsys, "shadow_seed")
    assert "config" in err


def test_a_prompt_needs_a_terminal_but_a_dry_run_does_not(world, capsys):
    assert world.go(DET, "--at", STAMP, tty=False) == 2 and world.prompts == [] and not (world.data / phase.FILE).exists()


def test_the_amendment_names_the_same_ten_files_the_verb_hashes():
    text = (ROOT / "PREREGISTRATION.md").read_text()
    start = text.index("### 2026-10-29 - arm_start hash list")
    amendment = text[start:text.find("\n### ", start + 1) if text.find("\n### ", start + 1) > 0 else len(text)]
    assert "TEN files" in amendment and all(f"`{f}`" in amendment for f in arm_start.FILES)
    assert "8b9d55c625f53c4e9474cdaaf2a16f0605779b0c0722fd5d3a8e0e43ac93330d" in amendment
    assert arms_window.arm_starts("## Amendments\n\n" + amendment) == {} and all(len(ln) <= 160 for ln in amendment.splitlines())


def test_help_works_and_the_module_stays_under_its_line_cap():
    out = subprocess.run([sys.executable, "-m", "ace_playbook.compile.arm_start", "--help"], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0 and "--dry-run" in out.stdout and "--at" in out.stdout and "detector" in out.stdout
    src = (ROOT / "ace_playbook" / "compile" / "arm_start.py").read_text().splitlines()
    assert len(src) <= 200 and max(map(len, src)) <= 160
