"""`compile/arm_check.py` and the nightly: a started arm phase whose signed marker is missing or unverified, and holdout on during one, become
note lines of the health record (Opus round 2, finding 1). Invented stamps and ids."""

import json
from types import SimpleNamespace

import pytest

from ace_playbook import health
from ace_playbook.compile import arm_check, phase, seal, status

from .test_arm_start import DET, ROOT, sealed
from .test_compile_seal import unsealed
from .test_compile_status import build, w  # noqa: F401 - w is a fixture
from .test_nightly_notes import load_nightly

STAMP = "2026-01-01T00:00:00Z"                     # long before the wall clock, so the nightly (which reads the real clock) sees a started phase
AFTER, BEFORE = "2026-02-01T00:00:00Z", "2025-12-31T00:00:00Z"
LINE = f"arm_start {DET} {STAMP} approvals={'a' * 64}\n"


@pytest.fixture
def prereg(tmp_path, monkeypatch):
    path = tmp_path / "PREREGISTRATION.md"
    path.write_text(sealed(unsealed((ROOT / "PREREGISTRATION.md").read_text())).rstrip("\n") + "\n\n" + LINE)
    monkeypatch.setattr(seal, "PREREG", path)
    return path


def cfg_of(tmp_path, **kw):
    (tmp_path / "data").mkdir(exist_ok=True)
    return SimpleNamespace(data_dir=tmp_path / "data", **kw)


def test_no_arm_start_line_or_one_that_has_not_started_gives_no_note(tmp_path, prereg):
    cfg = cfg_of(tmp_path, holdout_enabled=True)
    assert arm_check.check(cfg, BEFORE) == []                                  # the stamp is in the future: not started, holdout is not this check's yet
    prereg.write_text(prereg.read_text().replace(LINE, "(none)\n"))
    assert arm_check.check(cfg, AFTER) == []


def test_a_started_phase_without_a_marker_is_noted_per_detector(tmp_path, prereg):
    assert arm_check.check(cfg_of(tmp_path), AFTER) == [f"arm phase missing: {DET}"]
    phase.write(cfg_of(tmp_path), {"other_detector": STAMP}, STAMP)                 # a verified marker for another detector does not cover this one
    assert arm_check.check(cfg_of(tmp_path), AFTER) == [f"arm phase missing: {DET}"]


def test_a_marker_that_is_present_but_unverified_is_noted_first(tmp_path, prereg):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / phase.FILE).write_text(json.dumps({DET: STAMP}))          # hand-written, unsigned
    assert arm_check.check(cfg_of(tmp_path), AFTER) == [f"arm phase unverified: {DET}"]               # one line per detector, not two


def test_a_verified_marker_gives_no_note(tmp_path, prereg):
    phase.write(cfg_of(tmp_path), {DET: STAMP}, STAMP)
    assert arm_check.check(cfg_of(tmp_path), AFTER) == [] and arm_check.check(cfg_of(tmp_path)) == []


def test_holdout_on_during_a_started_phase_is_noted(tmp_path, prereg):
    phase.write(cfg_of(tmp_path), {DET: STAMP}, STAMP)
    assert arm_check.check(cfg_of(tmp_path, holdout_enabled=True), AFTER) == ["holdout on during an arm phase"]
    assert arm_check.check(cfg_of(tmp_path, holdout_enabled=False), AFTER) == []


def test_every_note_fits_the_health_record_line(tmp_path, prereg):
    notes = arm_check.check(cfg_of(tmp_path, holdout_enabled=True), AFTER)
    assert len(notes) == 2 and all(len(n) <= 80 and n.isprintable() and "/" not in n for n in notes)


def test_the_nightly_adds_the_notes_to_the_health_record_and_status_renders_them(tmp_path, monkeypatch, prereg, w):  # noqa: F811
    mod, cfg = load_nightly(tmp_path, monkeypatch, {}, "--dry-run")
    assert mod.main() == 0
    rec = health.read(cfg)
    assert rec is not None and rec["notes"] == [f"arm phase missing: {DET}"]
    path = w.cfg.path("health.json")
    path.write_text(json.dumps({"last_run": "2026-10-20T03:00:00Z", "ok": True, "consecutive_failures": 0, "last_error": "", "notes": rec["notes"]}))
    s = build(w)
    assert s["heartbeat"]["notes"][-1] == f"arm phase missing: {DET}" and f"arm phase missing: {DET}" in status.render(s)


def test_a_failing_check_never_fails_the_nightly(tmp_path, monkeypatch, prereg):
    def boom(cfg):
        raise KeyError("detail")
    mod, cfg = load_nightly(tmp_path, monkeypatch, {}, "--dry-run")
    monkeypatch.setattr(arm_check, "check", boom)
    assert mod.main() == 0
    rec = health.read(cfg)
    assert rec is not None and rec["ok"] is True and rec["notes"] == ["arm check failed"]


def test_an_unreadable_pre_registration_is_the_note_arm_check_failed_and_not_a_failed_night(tmp_path, monkeypatch, prereg):
    (tmp_path / "adir").mkdir()
    monkeypatch.setattr(seal, "PREREG", tmp_path / "adir")                      # exists, but reading it raises
    mod, cfg = load_nightly(tmp_path, monkeypatch, {}, "--dry-run")
    assert mod.main() == 0
    rec = health.read(cfg)
    assert rec is not None and rec["ok"] is True and rec["notes"] == ["arm check failed"]


def test_a_verified_marker_whose_stamp_differs_from_the_line_is_noted(tmp_path, prereg):
    phase.write(cfg_of(tmp_path), {DET: "2026-01-02T00:00:00Z"}, STAMP)
    assert arm_check.check(cfg_of(tmp_path), AFTER) == [f"arm phase stamp differs: {DET}"]
    phase.write(cfg_of(tmp_path), {DET: STAMP}, STAMP)
    assert arm_check.check(cfg_of(tmp_path), AFTER) == []


def script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


def test_the_arm_notes_come_first_so_status_shows_them_among_its_four(tmp_path, monkeypatch, prereg, w):  # noqa: F811
    prereg.write_text(prereg.read_text() + f"arm_start other_detector {STAMP} approvals={'b' * 64}\n")
    extra = {"backup_check": str(script(tmp_path, "b.sh", 'echo "backup 7h"\n')), "brain_check": str(script(tmp_path, "n.sh", 'echo "brain ok"\n')),
             "holdout_enabled": True, "holdout_seed": "s" * 32}
    mod, cfg = load_nightly(tmp_path, monkeypatch, extra, "--dry-run")
    assert mod.main() == 0
    notes = health.read(cfg)["notes"]
    arm = ["arm phase missing: other_detector", f"arm phase missing: {DET}", "holdout on during an arm phase"]
    assert notes == [*arm, "backup 7h", "brain ok"]                              # five candidate notes, the arm ones first
    w.cfg.path("health.json").write_text(json.dumps({"last_run": "2026-10-20T03:00:00Z", "ok": True, "consecutive_failures": 0,
                                                     "last_error": "", "notes": notes}))
    shown = build(w)["heartbeat"]["notes"]                                       # status keeps the first four of the record
    assert all(n in shown for n in arm) and "brain ok" not in shown


def test_the_nightly_script_stays_within_80_lines():
    assert len((ROOT / "hooks" / "nightly_sweep.py").read_text().splitlines()) <= 80
    assert "arm_check" in (ROOT / "hooks" / "nightly_sweep.py").read_text()
