"""The nightly offline pass (compile/offline.py): discovery, detector events, idempotency, dry run, cleanup, CLI, sweep script."""

import dataclasses
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

from ace_playbook.atomic import write_private
from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import catalogue, evidence, labels, offline, shadow
from ace_playbook.compile import state as st

from . import compile_support as cs

CMD = cs.RM_PROMPT_CMD
RM = {"tool": "Bash", "input": {"command": CMD}}
HOOKS = Path(__file__).resolve().parent.parent / "hooks"


@pytest.fixture
def w(tmp_path):
    return cs.offline_world(tmp_path)


def jsonl(w, name):
    p = w.cfg.path(name)
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


# ------------------------------------------------------------------ discovery
def test_discovery_skips_subagents_symlinks_fresh_old_oversize_and_bad_session_ids(w, tmp_path, monkeypatch):
    steps = [{"human": "x"}, RM]
    cs.put_session(w, "good", steps)
    cs.put_session(w, "fresh", steps, age=60)
    cs.put_session(w, "old", steps, age=8 * 86400)
    cs.put_session(w, "bad id", steps)
    (w.cfg.transcripts_root / "proj" / "subagents").mkdir()
    (w.cfg.transcripts_root / "proj" / "subagents" / "agent.jsonl").write_text("{}\n")
    (tmp_path / "elsewhere.jsonl").write_text("{}\n")
    (w.cfg.transcripts_root / "proj" / "linked.jsonl").symlink_to(tmp_path / "elsewhere.jsonl")
    todo, skipped = offline.discover(w.cfg, cs.NOW, 60.0, 7, {})
    assert [sh for _, sh, _, _ in todo] == [st.session_hash("good")] and skipped == 5
    monkeypatch.setattr(evidence, "MAX_TRANSCRIPT", 10)
    assert offline.discover(w.cfg, cs.NOW, 60.0, 7, {})[0] == []


def test_discovery_without_a_transcripts_root_finds_nothing(w):
    w.cfg.transcripts_root = None
    assert offline.discover(w.cfg, cs.NOW, 60.0, 7, {}) == ([], 0)
    assert offline.run(w.cfg, now=cs.NOW)["transcripts"] == 0


# ------------------------------------------------------------------ detector events
def test_detector_events_carry_ids_hashes_counts_and_no_text(w):
    cs.put_session(w, "s1", [{"human": "tidy"}, RM, {"tool": "Bash", "input": {"command": "git commit -m done"}},
                             {"human": "why do you keep doing that"}])
    out = offline.run(w.cfg, now=cs.NOW)
    rows = {r["detector"]: r for r in jsonl(w, "detector_events.jsonl")}
    assert out["detector_events"] + out["sessions"] == len(jsonl(w, "detector_events.jsonl")) and out["errors"] == 0
    assert out["sessions"] == 1 and out["detector_events"] == len(jsonl(w, "detector_events.jsonl")) - 1      # fires and summary rows apart
    assert set(rows) == {"rm_rf_nonscratch", "rm_rf", "commit_without_push", "user_correction", "_session"}      # + the per-session summary
    rm = rows["rm_rf_nonscratch"]
    assert set(rm) == {"ts", "v", "id", "session", "detector", "tier", "count", "key", "payload_sha256", "tool_name"}
    assert (rm["v"], rm["tier"], rm["count"], rm["tool_name"], rm["session"]) == (1, "offline", 1, "Bash", st.session_hash("s1"))
    assert rm["key"] == offline.hash16("/w/src")
    assert rm["payload_sha256"] == evidence.payload_hash({"tool_name": "Bash", "tool_input": {"command": CMD}})
    assert re.fullmatch(r"[0-9a-f]{16}", rm["id"])
    assert (rows["commit_without_push"]["payload_sha256"], rows["commit_without_push"]["tool_name"]) == ("", "")
    assert rows["user_correction"]["key"] == "" and rows["user_correction"]["tool_name"] == ""
    text = w.cfg.path("detector_events.jsonl").read_text()
    assert "rm -rf" not in text and "/w/src" not in text and "s1" not in text.replace("sha", "")
    assert oct(w.cfg.path("detector_events.jsonl").stat().st_mode & 0o777) == "0o600"


def test_one_failing_transcript_is_one_error_and_the_pass_goes_on(w, monkeypatch):
    def boom(event, state):
        if any("BOOM" in tu.input.get("command", "") for tu in event.tool_uses):
            raise RuntimeError("detector bug")
        return None
    monkeypatch.setitem(catalogue.CATALOGUE, "rm_rf", dataclasses.replace(catalogue.CATALOGUE["rm_rf"], fire=boom))
    cs.put_session(w, "bad", [{"human": "x"}, {"tool": "Bash", "input": {"command": "echo BOOM"}}])
    cs.put_session(w, "good", [{"human": "x"}, RM])
    out = offline.run(w.cfg, now=cs.NOW)
    assert (out["errors"], out["transcripts"]) == (1, 1)
    assert {r["session"] for r in jsonl(w, "detector_events.jsonl")} == {st.session_hash("good")}
    assert set(json.loads(w.cfg.path("offline_state.json").read_text())) == {st.session_hash("good")}   # the bad one is retried


# ------------------------------------------------------------------ idempotency
def test_a_second_run_appends_nothing_and_skips_unchanged_sessions(w):
    cs.put_session(w, "s1", [{"human": "please remove /w/src"}, RM])
    cs.put_shadow(w, "s1", CMD)
    first = offline.run(w.cfg, now=cs.NOW)
    before = cs.tree_state(w.data)
    second = offline.run(w.cfg, now=cs.NOW)
    assert first["transcripts"] == 1 and first["detector_events"] > 0 and sum(first["labels"].values()) == 1
    assert (second["transcripts"], second["skipped"], second["detector_events"], sum(second["labels"].values())) == (0, 1, 0, 0)
    assert cs.tree_state(w.data) == before and oct(w.cfg.path("offline_state.json").stat().st_mode & 0o777) == "0o600"
    assert json.loads(w.cfg.path("offline_state.json").read_text()) == {
        st.session_hash("s1"): {"size": os.stat(w.cfg.transcripts_root / "proj" / "s1.jsonl").st_size,
                                "mtime": int(cs.NOW - 7200)}}


def test_a_changed_session_is_scanned_again_and_adds_only_new_ids(w):
    path = cs.put_session(w, "s1", [{"human": "x"}, RM])
    offline.run(w.cfg, now=cs.NOW)
    first_ids = [r["id"] for r in jsonl(w, "detector_events.jsonl")]
    more = cs.records([{"human": "x"}, RM, {"tool": "Bash", "input": {"command": "rm -rf /w/other"}}], "s1")
    path.write_text("".join(json.dumps(r) + "\n" for r in more))
    os.utime(path, (cs.NOW - 7000, cs.NOW - 7000))
    out = offline.run(w.cfg, now=cs.NOW)
    ids = [r["id"] for r in jsonl(w, "detector_events.jsonl")]
    assert out["transcripts"] == 1 and out["detector_events"] + out["sessions"] == len(ids) - len(first_ids) > 0
    assert len(ids) == len(set(ids)) and ids[:len(first_ids)] == first_ids


# ------------------------------------------------------------------ dry run
def test_dry_run_computes_everything_and_writes_nothing(w):
    w.approve("block")
    cs.put_session(w, "s1", [{"human": "please remove /w/src"}, RM, {"tool": "Bash", "input": {"command": "rm -rf /w/src/"}}])
    cs.put_shadow(w, "s1", CMD, mode="block")
    old = w.data / "shadow_state" / ("0" * 16 + ".json")
    old.parent.mkdir()
    old.write_text("{}")
    os.utime(old, (cs.NOW - 9 * 86400,) * 2)
    before = cs.tree_state(w.data)
    out = offline.run(w.cfg, dry_run=True, now=cs.NOW)
    assert cs.tree_state(w.data) == before and old.exists()
    assert out["detector_events"] > 0 and out["evasion_candidates"] == 1 and out["state_removed"] == 1
    assert out["labels"]["auto_fp_prompt_target"] == 1 and out["demotions"] == 1
    real = offline.run(w.cfg, now=cs.NOW)
    assert {k: real[k] for k in ("detector_events", "evasion_candidates", "state_removed", "demotions", "labels")} == {
        k: out[k] for k in ("detector_events", "evasion_candidates", "state_removed", "demotions", "labels")}


# ------------------------------------------------------------------ state cleanup
def test_stale_shadow_state_files_are_removed_fresh_ones_and_symlinks_are_not(w, tmp_path):
    base = w.data / "shadow_state"
    base.mkdir()
    names = {"old": "a" * 16 + ".json", "old_lock": "a" * 16 + ".json.lock", "fresh": "b" * 16 + ".json",
             "link": "c" * 16 + ".json", "other": "notes.txt"}
    for key in ("old", "old_lock", "fresh", "other"):
        (base / names[key]).write_text("{}")
    target = tmp_path / "target.json"
    target.write_text("{}")
    (base / names["link"]).symlink_to(target)
    for key in ("old", "old_lock", "other"):
        os.utime(base / names[key], (cs.NOW - 8 * 86400,) * 2)
    os.utime(base / names["link"], (cs.NOW - 9 * 86400,) * 2, follow_symlinks=False)
    os.utime(target, (cs.NOW - 9 * 86400,) * 2)
    out = offline.run(w.cfg, now=cs.NOW)
    left = sorted(p.name for p in base.iterdir())
    assert out["state_removed"] == 2 and left == sorted([names["fresh"], names["link"], names["other"]])
    assert target.exists()


def test_a_symlinked_shadow_state_directory_is_left_alone(w, tmp_path):
    real = tmp_path / "real_state"
    real.mkdir()
    (real / ("d" * 16 + ".json")).write_text("{}")
    os.utime(real / ("d" * 16 + ".json"), (cs.NOW - 9 * 86400,) * 2)
    (w.data / "shadow_state").symlink_to(real)
    assert offline.run(w.cfg, now=cs.NOW)["state_removed"] == 0 and len(list(real.iterdir())) == 1


# ------------------------------------------------------------------ CLI
def cli_run(w, *args, capsys):
    code = cli.main(["--config", w.conf, "offline", *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_prints_one_ascii_count_line_each_and_the_p95_rule_every_run(w, capsys, tmp_path):
    conf = tmp_path / "c.json"
    conf.write_text(json.dumps({"data_dir": str(w.data), "transcripts_root": str(w.cfg.transcripts_root)}))
    w.conf = str(conf)
    cs.put_session(w, "s1", [{"human": "please remove /w/src"}, RM])
    cs.put_shadow(w, "s1", CMD)
    # the fixture rows carry fixed dates (cs.NOW); the default --since 7 is measured from the real clock, so widen it
    code, out, err = cli_run(w, "--idle-minutes", "0", "--since", "3660", capsys=capsys)
    assert code == 0 and err == "" and all(ch == "\n" or " " <= ch <= "~" for ch in out)
    names = [ln.split(":")[0] for ln in out.splitlines()]
    assert names == ["transcripts", "skipped", "detector_events", "sessions", "labels", *(f"labels.{k}" for k in labels.AUTO),
                     "demotions", "evasion_candidates", "state_removed", "errors", "lesson_check", "error_rate", "p95_rule"]
    assert all(re.fullmatch(r"[a-z0-9_.]+: [0-9a-z_ :.()=,]+", ln) for ln in out.splitlines())
    assert "p95_rule: not implemented" in out and "labels: 1" in out
    assert str(w.data) not in out and st.session_hash("s1") not in out
    code, out, _ = cli_run(w, "--dry-run", capsys=capsys)
    assert code == 0 and "p95_rule: not implemented" in out


@pytest.mark.parametrize("args", [("--since", "0"), ("--since", "3661"), ("--since", "-5"), ("--idle-minutes", "-1")])
def test_cli_bounds_since_and_idle_minutes(w, capsys, args):
    code, out, err = cli_run(w, *args, capsys=capsys)
    assert (code, out) == (2, "") and "--since must be 1..3660" in err


def test_cli_exits_2_when_the_config_cannot_load(tmp_path, capsys):
    bad = tmp_path / "c.json"
    bad.write_text("{not json")
    assert cli.main(["--config", str(bad), "offline"]) == 2
    assert "error" in capsys.readouterr().err


def test_p95_is_reported_not_implemented_in_the_counts(w):
    out = offline.run(w.cfg, now=cs.NOW)
    assert out["p95_rule"] == "not implemented" and not hasattr(offline, "lines")


# ------------------------------------------------------------------ hooks/nightly_sweep.py
@pytest.fixture
def sweep(w, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("nightly_sweep_under_test", HOOKS / "nightly_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = {"sweep": [], "offline": []}
    monkeypatch.setattr(mod, "run_sweep", lambda cfg, **kw: calls["sweep"].append(kw) or [{"status": "would_reflect"}])
    monkeypatch.setattr(offline, "run", lambda cfg, **kw: calls["offline"].append(kw) or {"transcripts": 3})

    def go(*args):
        monkeypatch.setattr(sys, "argv", ["nightly_sweep.py", "--config", w.conf, *args])
        assert mod.main() == 0
        return json.loads(capsys.readouterr().out), calls
    return go


def test_nightly_sweep_runs_the_offline_pass_even_under_dry_run_and_lists_its_counts(sweep):
    out, calls = sweep("--dry-run")
    assert out == {"offline": {"transcripts": 3}, "digest": {"queue": 0, "notes": []}, "sweep": [{"status": "would_reflect"}]}
    assert calls["offline"][0]["dry_run"] is False and calls["sweep"][0]["dry_run"] is True


def test_nightly_sweep_passes_offline_dry_run_to_the_pass_and_no_offline_skips_the_stage(sweep):
    _, calls = sweep("--offline-dry-run")
    assert calls["offline"][0]["dry_run"] is True and calls["sweep"][0]["dry_run"] is False
    out, calls = sweep("--no-offline")
    assert "offline" not in out and len(calls["offline"]) == 1 and len(calls["sweep"]) == 2


def test_an_offline_exception_is_reported_by_type_and_reflection_still_runs(sweep, monkeypatch):
    def fail(cfg, **kw):
        raise KeyError("secret detail /private/path")
    monkeypatch.setattr(offline, "run", fail)
    out, calls = sweep("--dry-run")
    # the digest runs after the sweep's health record, so tonight's failure is already in it
    assert out == {"offline": {"error": "KeyError"}, "digest": {"queue": 0, "notes": ["nightly:failed"]}, "sweep": [{"status": "would_reflect"}]}
    assert len(calls["sweep"]) == 1


def test_nightly_sweep_with_offline_dry_run_really_leaves_the_data_directory_untouched(w, tmp_path, monkeypatch, capsys):
    import time
    conf = tmp_path / "c.json"
    conf.write_text(json.dumps({"data_dir": str(w.data), "transcripts_root": str(w.cfg.transcripts_root)}))
    path = cs.put_session(w, "s1", [{"human": "please remove /w/src"}, RM])
    os.utime(path, (time.time() - 7200,) * 2)
    cs.put_shadow(w, "s1", CMD)
    stale = w.data / "shadow_state" / ("0" * 16 + ".json")
    stale.parent.mkdir()
    stale.write_text("{}")
    os.utime(stale, (time.time() - 9 * 86400,) * 2)
    spec = importlib.util.spec_from_file_location("nightly_sweep_dry", HOOKS / "nightly_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "run_sweep", lambda cfg, **kw: [])
    monkeypatch.setattr(sys, "argv", ["nightly_sweep.py", "--config", str(conf), "--offline-dry-run"])
    before = cs.tree_state(w.data)
    assert mod.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert cs.tree_state(w.data) == before and stale.exists()
    assert out["offline"]["detector_events"] > 0 and out["offline"]["state_removed"] == 1


def test_tree_state_sees_a_new_directory_and_a_same_size_rewrite(tmp_path):
    (tmp_path / "a").write_text("xx")
    first = cs.tree_state(tmp_path)
    (tmp_path / "d").mkdir()
    second = cs.tree_state(tmp_path)
    assert second != first and second["d"][0] is True
    (tmp_path / "a").write_text("yy")
    os.utime(tmp_path / "a", (1.0, 1.0))
    assert cs.tree_state(tmp_path)["a"] != second["a"]


# ------------------------------------------------------------------ marking sessions done
def test_a_session_with_shadow_rows_stays_unmarked_while_the_label_stage_fails(w):
    cs.put_session(w, "s1", [{"human": "please remove /w/src"}, RM])
    cs.put_session(w, "s2", [{"human": "x"}, RM])
    cs.put_shadow(w, "s1", CMD)
    cs.write_labels(w.cfg, [{"ref": "aaaaaaaa", "label": "tp"}])
    path = w.cfg.path("labels.jsonl")
    path.write_text(path.read_text().replace('"tp"', '"fp"'))                # a broken chain
    assert offline.run(w.cfg, now=cs.NOW)["errors"] == 1
    assert set(json.loads(w.cfg.path("offline_state.json").read_text())) == {st.session_hash("s2")}
    path.unlink()                                                           # repaired: the session is scanned again
    out = offline.run(w.cfg, now=cs.NOW)
    assert out["transcripts"] == 1 and sum(out["labels"].values()) == 1
    assert set(json.loads(w.cfg.path("offline_state.json").read_text())) == {st.session_hash("s1"), st.session_hash("s2")}


def test_a_write_failure_marks_no_session_done(w, monkeypatch):
    cs.put_session(w, "s1", [{"human": "x"}, RM])

    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(shadow, "_append", boom)
    out = offline.run(w.cfg, now=cs.NOW)
    assert out["errors"] == 1 and not w.cfg.path("offline_state.json").exists()
    monkeypatch.undo()
    assert offline.run(w.cfg, now=cs.NOW)["transcripts"] == 1 and w.cfg.path("offline_state.json").exists()


def test_a_resumed_session_adds_no_second_session_end_row(w):
    path = cs.put_session(w, "s1", [{"human": "x"}, {"tool": "Bash", "input": {"command": "git commit -m done"}}])
    ends = lambda: [r for r in jsonl(w, "detector_events.jsonl") if r["detector"] == "commit_without_push"]  # noqa: E731
    offline.run(w.cfg, now=cs.NOW)
    assert len(ends()) == 1
    with open(path, "a") as fh:
        fh.write(json.dumps({"type": "user", "timestamp": "2026-10-18T11:00:00Z",
                             "message": {"role": "user", "content": "hello again"}}) + "\n")
    os.utime(path, (cs.NOW - 7000,) * 2)
    assert offline.run(w.cfg, now=cs.NOW)["transcripts"] == 1 and len(ends()) == 1


# ------------------------------------------------------------------ the demotion stage's inputs
def test_an_unreadable_corpus_skips_the_demotion_stage_instead_of_reading_as_all_errors(w):
    w.data.joinpath("shadow.log").write_text("2026-10-18T10:00:00Z error:rm_rf:RuntimeError\n" * 5)
    w.data.joinpath("elsewhere.jsonl").write_text("")
    w.cfg.path("shadow.jsonl").symlink_to(w.data / "elsewhere.jsonl")
    out = offline.run(w.cfg, now=cs.NOW)
    assert (out["errors"], out["demotions"], out["error_rate"]) == (1, 0, "not run") and not w.cfg.path("demotions.jsonl").exists()


def test_a_small_sample_does_not_apply_the_error_rate_rule_and_says_so(w):
    w.data.joinpath("shadow.log").write_text("2026-10-18T10:00:00Z error:rm_rf:RuntimeError\n" * 3)
    for n in range(7):
        cs.put_shadow(w, f"s{n}", f"echo {n}")
    out = offline.run(w.cfg, now=cs.NOW)
    assert out["error_rate"] == "not computable (n=10, need 100)" and out["demotions"] == 0
    assert out["lesson_check"] == "skipped (no store)"


def test_a_malformed_approved_copy_is_one_error_and_the_others_are_still_reviewed(w):
    w.approve("block")
    bad = {**w.props["rm_rf_nonscratch"], "id": "P-11111111", "provenance": {}}      # no source_sha256: KeyError
    write_private(w.data / "proposals" / "approved" / "P-11111111.json",
                  json.dumps(shadow.approved_record(bad, "block", "2026-10-01T00:00:00Z")) + "\n")
    labels.append_row(w.cfg, "fp", labels.row_id(cs.put_shadow(w, "s1", CMD, "block")))
    out = offline.run(w.cfg, now=cs.NOW)
    assert (out["errors"], out["demotions"]) == (1, 1)


def test_cli_prints_a_type_only_error_and_exits_1_when_the_pass_raises(w, monkeypatch, capsys):
    monkeypatch.setattr(offline, "run", lambda cfg, **kw: {}["secret/path"])
    code, out, err = cli_run(w, capsys=capsys)
    assert (code, out, err) == (1, "", "error: KeyError\n")


def test_a_deltas_chain_that_fails_verification_disables_nothing_and_is_one_error(w):
    from ace_playbook.store import DeltaLog
    from .helpers import GLOBAL, add_draft
    gone = add_draft("A retired global lesson about deletes", GLOBAL, status="retired")
    DeltaLog(w.cfg.deltas).append(gone, ts="2026-10-01T00:00:00Z")
    with open(w.cfg.deltas, "a") as fh:
        fh.write(json.dumps({"malformed": True}) + "\n")
    copy = {**w.props["rm_rf_nonscratch"], "lesson_id": gone["lesson_id"]}
    write_private(w.data / "proposals" / "approved" / (w.pid + ".json"),
                  json.dumps(shadow.approved_record(copy, "warn", "2026-10-01T00:00:00Z")) + "\n")
    out = offline.run(w.cfg, now=cs.NOW)
    assert (out["lesson_check"], out["errors"], out["demotions"]) == ("chain invalid", 1, 0)


def test_offline_failures_under_a_dry_run_accumulate_and_a_clean_real_pass_resets_the_streak(sweep, w, monkeypatch):
    from ace_playbook import health as healthmod
    monkeypatch.setattr(offline, "run", lambda cfg, **kw: {"errors": 2})
    sweep("--dry-run")
    rec = healthmod.read(w.cfg)
    assert rec["ok"] is False and rec["consecutive_failures"] == 1 and "offline" in rec["last_error"]
    sweep("--dry-run", "--offline-dry-run")                                 # a dry offline pass records nothing
    assert healthmod.read(w.cfg)["consecutive_failures"] == 1
    monkeypatch.setattr(offline, "run", lambda cfg, **kw: {}["x"])
    sweep("--dry-run")
    rec = healthmod.read(w.cfg)
    assert rec["consecutive_failures"] == 2 and "KeyError" in rec["last_error"]
    monkeypatch.setattr(offline, "run", lambda cfg, **kw: {"errors": 0})
    sweep("--dry-run")
    rec = healthmod.read(w.cfg)                                             # a clean real pass is a nightly that ran: it resets
    assert rec["ok"] is True and rec["consecutive_failures"] == 0           # the streak (the heartbeat tile reads this record)


def test_nightly_dry_run_with_a_real_clean_offline_pass_records_health_ok(sweep, w):
    # the reflection dry run itself writes no record (`run_sweep` rule); the nightly still ran, so the heartbeat must not say never
    out, _ = sweep("--dry-run")
    rec = json.loads((w.data / "health.json").read_text())
    assert rec["ok"] is True and rec["consecutive_failures"] == 0 and rec["last_error"] == ""
    assert out["digest"]["notes"] == []
    from ace_playbook.compile import status as stmod                          # the symptom: the heartbeat tile no longer says never
    assert stmod.build(w.cfg)["heartbeat"]["last_nightly_ok"] == rec["last_run"]


def test_nightly_dry_run_that_crashes_after_a_clean_offline_pass_records_the_failure(w, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("nightly_sweep_crash", HOOKS / "nightly_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(offline, "run", lambda cfg, **kw: {"errors": 0})

    def boom(cfg, **kw):
        raise RuntimeError("queue unreadable /private/detail")
    monkeypatch.setattr(mod, "run_sweep", boom)
    monkeypatch.setattr(sys, "argv", ["nightly_sweep.py", "--config", w.conf, "--dry-run"])
    with pytest.raises(RuntimeError):
        mod.main()
    rec = json.loads((w.data / "health.json").read_text())
    assert rec["ok"] is False and rec["consecutive_failures"] == 1 and rec["last_error"] == "sweep raised RuntimeError"
    monkeypatch.setattr(sys, "argv", ["nightly_sweep.py", "--config", w.conf, "--dry-run", "--offline-dry-run"])
    with pytest.raises(RuntimeError):                                     # an offline dry run records nothing, crash or not
        mod.main()
    assert json.loads((w.data / "health.json").read_text())["consecutive_failures"] == 1
    (w.data / "health.json").unlink()
    monkeypatch.setattr(sys, "argv", ["nightly_sweep.py", "--config", w.conf, "--dry-run", "--no-offline"])
    with pytest.raises(RuntimeError):                                     # nothing real ran: no record, so nothing a later night cannot clear
        mod.main()
    assert not (w.data / "health.json").exists()


def test_nightly_records_nothing_when_the_offline_pass_was_a_dry_run_or_skipped(sweep, w):
    sweep("--dry-run", "--offline-dry-run")
    assert not (w.data / "health.json").exists()
    sweep("--dry-run", "--no-offline")
    assert not (w.data / "health.json").exists()


def test_nightly_dry_run_with_a_failing_offline_pass_records_the_failure_not_ok(sweep, w, monkeypatch):
    monkeypatch.setattr(offline, "run", lambda cfg, **kw: {"errors": 2})
    out, _ = sweep("--dry-run")
    rec = json.loads((w.data / "health.json").read_text())
    assert rec["ok"] is False and rec["consecutive_failures"] == 1 and "2 errors" in rec["last_error"]
    assert out["digest"]["notes"] == ["nightly:failed"]
