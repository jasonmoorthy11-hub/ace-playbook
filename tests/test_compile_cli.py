"""`python -m ace_playbook.compile cards|propose|validate`: exit codes, dry run, containment, import hygiene."""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import approvals, evidence, proposal

from . import compile_support as cs

ROOT = Path(__file__).resolve().parent.parent
COMPILE = ROOT / "ace_playbook" / "compile"


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = cs.make_world(tmp_path, monkeypatch)
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(w.cfg.data_dir), "transcripts_root": str(w.cfg.transcripts_root),
                                "chain_key_path": str(w.cfg.chain_key_path)}))
    w.conf = str(conf)
    return w


def run(world, *args, capsys):
    code = cli.main(["--config", world.conf, *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def tree(root):
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(Path(root).rglob("*"))}


# ------------------------------------------------------------------ cards
def test_cards_prints_a_table_and_writes_the_card(world, capsys):
    code, out, err = run(world, "cards", capsys=capsys)
    (card,) = evidence.load_cards(world.cfg)
    assert code == 0 and err == ""
    assert out.splitlines()[0].split() == ["id", "lesson", "provenance", "replayable"]
    assert out.splitlines()[1].split() == [card["id"], world.lesson_id, "standard", "true"]


def test_cards_for_one_lesson_and_for_a_refused_one(world, capsys):
    assert run(world, "cards", "--lesson", world.lesson_id, capsys=capsys)[0] == 0
    code, out, _ = run(world, "cards", "--lesson", "L-00000000", capsys=capsys)
    assert code == 0 and "refused    L-00000000" in out and "lesson_unknown" in out


def test_cards_reports_the_label_ref_a_legacy_lesson_waits_for(tmp_path, monkeypatch, capsys):
    w = cs.make_world(tmp_path, monkeypatch, legacy=True)
    conf = tmp_path / "c.json"
    conf.write_text(json.dumps({"data_dir": str(w.cfg.data_dir), "transcripts_root": str(w.cfg.transcripts_root)}))
    w.conf = str(conf)
    code, out, _ = run(w, "cards", capsys=capsys)
    assert code == 0 and f"legacy_unlabelled (label ref {w.ref('later')})" in out
    cs.write_labels(w.cfg, [{"ref": w.ref("later"), "label": "incident"}])
    out = run(w, "cards", capsys=capsys)[1]
    assert f"{w.ref('later')} {w.lesson_id} legacy      true".split() == out.splitlines()[1].split()


@pytest.mark.parametrize("bad", ["../../x", "L-xyz", "l-00000000"])
def test_cards_with_a_crafted_lesson_id_exits_2_and_writes_nothing(world, capsys, bad):
    before = tree(world.cfg.data_dir)
    code, _, err = run(world, "cards", "--lesson", bad, capsys=capsys)
    assert code == 2 and "lesson id" in err and tree(world.cfg.data_dir) == before


def test_a_missing_config_or_a_bad_delta_chain_exits_2(world, tmp_path, capsys):
    assert cli.main(["--config", str(tmp_path / "nope.json"), "cards"]) == 2
    capsys.readouterr()
    world.cfg.deltas.write_text(world.cfg.deltas.read_text().replace("Confirm", "Confirmed"))
    code, _, err = run(world, "cards", capsys=capsys)
    assert code == 2 and str(tmp_path) not in err


# ------------------------------------------------------------------ propose
def test_propose_dry_run_prints_the_proposal_and_writes_nothing(world, capsys):
    before = tree(world.cfg.data_dir)
    code, out, _ = run(world, "propose", "--lesson", world.lesson_id, "--detector", "rm_rf_nonscratch", "--dry-run", capsys=capsys)
    assert code == 0 and tree(world.cfg.data_dir) == before                        # no cards/ and no proposals/ either
    assert not (world.cfg.data_dir / "cards").exists() and not (world.cfg.data_dir / "proposals").exists()
    printed = json.loads(out[out.index("{"):])
    assert printed["mode"] == "observe" and printed["lesson_id"] == world.lesson_id
    assert out.count("[ok]") == 6


def test_propose_writes_a_0600_proposal_and_is_idempotent(world, capsys):
    args = ("propose", "--lesson", world.lesson_id, "--detector", "rm_rf_nonscratch")
    code, out, _ = run(world, *args, capsys=capsys)
    path = Path(out.splitlines()[-1])
    assert code == 0 and path.parent == world.cfg.data_dir / "proposals" and oct(path.stat().st_mode & 0o777) == "0o600"
    first = path.read_bytes()
    code2, out2, _ = run(world, *args, capsys=capsys)
    assert code2 == 0 and Path(out2.splitlines()[-1]) == path
    assert json.loads(path.read_text())["check"]["detector"] == "rm_rf_nonscratch" and first
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


@pytest.mark.parametrize("detector", ["no_such_detector", "user_correction", "rm_rf"])
def test_propose_exits_1_for_a_detector_the_lesson_does_not_map_to(world, capsys, detector):
    code, _, err = run(world, "propose", "--lesson", world.lesson_id, "--detector", detector, capsys=capsys)
    assert code == 1 and err.startswith("error: schema:")
    assert not (world.cfg.data_dir / "proposals").exists()


def test_propose_without_an_admitted_card_exits_1(tmp_path, monkeypatch, capsys):
    w = cs.make_world(tmp_path, monkeypatch, origin="origin_quiet", sessions=("later_clean",), shown=("later_clean",))
    w.conf = str(tmp_path / "c.json")
    Path(w.conf).write_text(json.dumps({"data_dir": str(w.cfg.data_dir), "transcripts_root": str(w.cfg.transcripts_root)}))
    code, _, err = run(w, "propose", "--lesson", w.lesson_id, "--detector", "rm_rf_nonscratch", capsys=capsys)
    assert code == 1 and "no admitted evidence card" in err


def test_propose_requires_both_flags(world, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", world.conf, "propose", "--lesson", world.lesson_id])
    assert e.value.code == 2


# ------------------------------------------------------------------ validate
@pytest.fixture
def written(world, capsys):
    code, out, _ = run(world, "propose", "--lesson", world.lesson_id, "--detector", "rm_rf_nonscratch", capsys=capsys)
    assert code == 0
    return Path(out.splitlines()[-1])


def test_validate_exits_0_and_prints_every_step(world, written, capsys):
    code, out, _ = run(world, "validate", str(written), capsys=capsys)
    assert code == 0 and out.count("[ok]") == 6 and "6 of 6" in out


def test_validate_exits_1_and_names_the_failing_step(world, written, capsys):
    p = json.loads(written.read_text())
    p["message"] = "ACE nope"
    written.write_text(json.dumps(p))
    code, out, err = run(world, "validate", str(written), capsys=capsys)
    assert code == 1 and "step 'message'" in err and "[FAIL] message" in out and "[--] replay: skipped" in out


def test_validate_exits_1_for_a_file_that_is_not_json_or_does_not_exist(world, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    for f in (bad, tmp_path / "gone.json"):
        code, out, err = run(world, "validate", str(f), capsys=capsys)
        assert code == 1 and "[FAIL] schema" in out and "step 'schema'" in err and str(tmp_path) not in err + out


def test_validate_exits_1_at_the_path_step_for_a_copy_outside_proposals(world, written, tmp_path, capsys):
    copy = tmp_path / written.name
    copy.write_text(written.read_text())
    code, _, err = run(world, "validate", str(copy), capsys=capsys)
    assert code == 1 and "step 'path'" in err


def test_the_whole_flow_changes_nothing_outside_the_data_dir(world, tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    outside = {k: v for k, v in tree(tmp_path).items() if not k.startswith("data")}
    run(world, "cards", capsys=capsys)
    code, out, _ = run(world, "propose", "--lesson", world.lesson_id, "--detector", "rm_rf_nonscratch", capsys=capsys)
    run(world, "validate", out.splitlines()[-1], capsys=capsys)
    assert code == 0 and list(home.iterdir()) == []
    after = {k: v for k, v in tree(tmp_path).items() if not k.startswith("data")}
    assert after == outside


# ------------------------------------------------------------------ approve / unapprove / shadow
class FakeTty:
    def isatty(self):
        return True

    def read(self, *a):
        return ""


@pytest.fixture
def tty(monkeypatch):
    """stdin looks like a terminal; the /dev/tty prompt is answered from `ask.answer` and recorded."""
    ask = type("Ask", (), {"answer": "y", "answers": [], "asked": []})()
    monkeypatch.setattr(sys, "stdin", FakeTty())
    monkeypatch.setattr(cli, "_ask", lambda prompt: (ask.asked.append(prompt), ask.answers.pop(0) if ask.answers else ask.answer)[1])
    return ask


def approved_dir(world):
    return world.cfg.data_dir / "proposals" / "approved"


def test_approve_refuses_without_a_terminal_and_writes_nothing(world, written, capsys):
    pid = written.stem
    code, _, err = run(world, "approve", pid, "--mode", "observe", capsys=capsys)
    assert code == 2 and "terminal" in err and not approved_dir(world).exists()


@pytest.mark.parametrize("mode", ["warn", "block"])
def test_approve_warn_and_block_go_through_the_ladder_and_refuse_a_proposal_never_approved(world, written, tty, capsys, mode):
    extra = ["--opus-reviewed", "abc1234"] if mode == "block" else []
    code, out, err = run(world, "approve", written.stem, "--mode", mode, *extra, capsys=capsys)
    assert code == 1 and f"not in {'observe' if mode == 'warn' else 'warn'}" in out and "not promoted" in err
    assert tty.asked == [] and not approved_dir(world).exists()


def test_approve_observe_rejects_the_promotion_only_flags(world, written, tty, capsys):
    for flag in (["--raw-checked"], ["--opus-reviewed", "abc1234"]):
        assert run(world, "approve", written.stem, "--mode", "observe", *flag, capsys=capsys)[0] == 2
    assert tty.asked == [] and not approved_dir(world).exists()


def test_approve_lists_the_provenance_asks_on_the_tty_and_writes_a_0600_copy(world, written, tty, capsys):
    pid = written.stem
    p = json.loads(written.read_text())
    code, out, _ = run(world, "approve", pid, "--mode", "observe", capsys=capsys)
    dest = approved_dir(world) / (pid + ".json")
    rec = json.loads(dest.read_text())
    assert code == 0 and tty.asked == [f"Approve {pid} in observe mode? [y/N] "]
    assert oct(dest.stat().st_mode & 0o777) == "0o600" and oct(approved_dir(world).stat().st_mode & 0o777) == "0o700"
    assert (rec["reviewed_by"], rec["mode"]) == ("operator-tty", "observe") and rec["approved_at"].endswith("Z")
    assert {k: v for k, v in rec.items() if k not in ("reviewed_by", "mode", "approved_at", "proposal_sha256")} == \
           {k: v for k, v in p.items() if k not in ("reviewed_by", "mode")}
    for needle in (*p["provenance"]["cards"], p["lesson_id"], p["check"]["detector"], p["provenance"]["source_sha256"]):
        assert needle in out
    assert "legacy" not in out.lower()                              # a standard card: no banner, one question
    assert json.loads(written.read_text()) == p                    # the pending file is left alone


@pytest.mark.parametrize("answer", ["n", "", "no", "yes please"])
def test_approve_without_a_y_writes_nothing(world, written, tty, capsys, answer):
    tty.answer = answer
    code, _, err = run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 1 and "not approved" in err and not approved_dir(world).exists()


def test_approve_is_idempotent_and_the_hook_accepts_the_copy(world, written, tty, capsys):
    from ace_playbook.compile import shadow
    pid = written.stem
    run(world, "approve", pid, "--mode", "observe", capsys=capsys)
    dest = approved_dir(world) / (pid + ".json")
    first = dest.read_bytes()
    code, out, _ = run(world, "approve", pid, "--mode", "observe", capsys=capsys)
    assert code == 0 and "already approved" in out and dest.read_bytes() == first and len(tty.asked) == 1
    assert len(approvals.read_chain(world.cfg.data_dir)[0]) == 1
    assert [(p["id"], m) for p, m in shadow.load_approved(world.cfg, lambda msg: None)] == [(pid, "observe")]


def test_approve_refuses_an_invalid_proposal_and_a_bad_id(world, written, tty, capsys):
    p = json.loads(written.read_text())
    p["message"] = "ACE nope"
    written.write_text(json.dumps(p))
    code, out, err = run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 1 and "step 'message'" in err and tty.asked == [] and not approved_dir(world).exists()
    for bad in ("../../x", "P-xyz", "p-00000000"):
        assert run(world, "approve", bad, "--mode", "observe", capsys=capsys)[0] == 2


def test_unapprove_needs_a_terminal_removes_the_copy_and_reports_a_missing_one(world, written, tty, capsys, monkeypatch):
    pid = written.stem
    run(world, "approve", pid, "--mode", "observe", capsys=capsys)
    dest = approved_dir(world) / (pid + ".json")
    monkeypatch.setattr(sys, "stdin", type("NoTty", (), {"isatty": lambda s: False})())
    assert run(world, "unapprove", pid, capsys=capsys)[0] == 2 and dest.exists()
    monkeypatch.setattr(sys, "stdin", FakeTty())
    assert run(world, "unapprove", pid, capsys=capsys)[0] == 0 and not dest.exists() and written.exists()
    code, _, err = run(world, "unapprove", pid, capsys=capsys)
    assert code == 1 and "not approved" in err


@pytest.fixture
def legacy(tmp_path, monkeypatch, capsys):
    """A migrated lesson (self-quoted `legacy:` evidence) whose card exists only through a human incident label."""
    w = cs.make_world(tmp_path, monkeypatch, legacy=True)
    w.conf = str(tmp_path / "c.json")
    Path(w.conf).write_text(json.dumps({"data_dir": str(w.cfg.data_dir), "transcripts_root": str(w.cfg.transcripts_root)}))
    cs.write_labels(w.cfg, [{"ref": w.ref("later"), "label": "incident"}])
    code, out, _ = run(w, "propose", "--lesson", w.lesson_id, "--detector", "rm_rf_nonscratch", capsys=capsys)
    assert code == 0
    w.pid = Path(out.splitlines()[-1]).stem
    return w


BANNER = "This lesson's stored evidence is self-quoted legacy text; the human incident label is the only evidence."


def test_approve_prints_the_legacy_banner_and_needs_a_second_yes(legacy, tty, capsys):
    code, out, _ = run(legacy, "approve", legacy.pid, "--mode", "observe", capsys=capsys)
    assert code == 0 and BANNER in out and len(tty.asked) == 2
    assert tty.asked[0].startswith(f"Approve {legacy.pid} in observe mode?") and tty.asked[1].endswith("[y/N] ")
    assert (approved_dir(legacy) / (legacy.pid + ".json")).exists()


@pytest.mark.parametrize("answers", [["y", "n"], ["y", ""], ["n"]])
def test_approve_of_a_legacy_lesson_writes_nothing_unless_both_answers_are_yes(legacy, tty, capsys, answers):
    tty.answers = list(answers)
    code, out, err = run(legacy, "approve", legacy.pid, "--mode", "observe", capsys=capsys)
    assert code == 1 and "not approved" in err and not approved_dir(legacy).exists()
    assert len(tty.asked) == len(answers)                           # a "no" stops the questions


def test_a_standard_proposal_gets_one_question_and_no_banner(world, written, tty, capsys):
    code, out, _ = run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 0 and BANNER not in out and len(tty.asked) == 1


def test_compile_shadow_runs_the_hook_entry_and_always_returns_0(world, tmp_path, monkeypatch, capsys):
    import io
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"garbage")))
    assert run(world, "shadow", "pre", capsys=capsys)[:2] == (0, "")
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", world.conf, "shadow", "sideways"])
    assert e.value.code == 2


# ------------------------------------------------------------------ hygiene
def test_module_runs_as_python_dash_m(world):
    out = subprocess.run([sys.executable, "-m", "ace_playbook.compile", "--config", world.conf, "cards"],
                         capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.returncode == 0 and out.stdout.startswith("id ")


def test_importing_the_hook_path_modules_loads_no_sqlite_store_scrub_cleaner_inject_or_network():
    heavy = ("sqlite3", "ace_playbook.store", "ace_playbook.scrub", "ace_playbook.clean", "ace_playbook.inject",
             "ace_playbook.keys", "ace_playbook.reflect", "subprocess", "socket", "ssl", "http.client", "urllib.request")
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); import ace_playbook.compile.shadow, "
            f"ace_playbook.compile.state, ace_playbook.compile.evidence, ace_playbook.compile.proposal, "
            f"ace_playbook.compile.labels, ace_playbook.compile.report, ace_playbook.compile.approvals, "
            f"ace_playbook.compile.ladder, ace_playbook.compile.demotions, ace_playbook.compile.offline, "
            f"ace_playbook.compile.modes, ace_playbook.compile.srchash, ace_playbook.compile.chains, ace_playbook.compile.chains_io, ace_playbook.compile.chains_cli, "
            f"ace_playbook.compile.__main__; "
            f"print(','.join(sorted(m for m in sys.modules if any(m == h or m.startswith(h + '.') for h in {heavy!r}))))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""


def test_the_shadow_hook_never_loads_the_synthetic_replay_or_the_metrics():
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); import ace_playbook.compile.shadow; "
            f"print(','.join(m for m in ('ace_playbook.compile.synth', 'ace_playbook.compile.metrics') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""
    assert not {"synth", "metrics"} & {a.name.split(".")[-1] for n in ast.walk(ast.parse((COMPILE / "shadow.py").read_text()))
                                       if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}


def test_the_offline_functions_still_load_their_heavy_parts_on_demand():
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); import ace_playbook.compile.propscrub; "
            f"print('ace_playbook.scrub' in sys.modules)")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "True"


def test_step2_and_4_sources_do_not_import_inject_keys_reflect_or_spawn_anything():
    banned = {"inject", "keys", "reflect", "subprocess", "socket", "urllib", "http", "ssl", "requests", "migrate"}
    for name in ("evidence.py", "proposal.py", "__main__.py", "shadow.py", "labels.py", "report.py", "approvals.py",
                 "ladder.py", "offline.py", "demotions.py", "modes.py", "synth.py", "metrics.py", "srchash.py", "chains.py",
                 "chains_io.py", "chains_cli.py", "seal.py",
                 "shadow_arm.py", "phase.py", "offline_demote.py"):
        for node in ast.walk(ast.parse((COMPILE / name).read_text())):
            if isinstance(node, ast.Import):
                mods = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods = {p for p in (node.module or "").split(".") if p} | {a.name for a in node.names}
                mods = mods if node.level else {(node.module or "").split(".")[0]}
            else:
                continue
            assert not (mods & banned), (name, mods & banned)


def test_step2_and_4_modules_stay_within_their_line_budgets():
    for name, cap in (("evidence.py", 300), ("proposal.py", 300), ("__main__.py", 300), ("shadow.py", 300),
                      ("labels.py", 260), ("report.py", 300), ("approvals.py", 150), ("ladder.py", 300),
                      ("offline.py", 300), ("demotions.py", 150), ("state.py", 300), ("modes.py", 150), ("events.py", 300),
                      ("synth.py", 300), ("metrics.py", 300), ("catalogue.py", 150), ("srchash.py", 120), ("chains.py", 150),
                      ("chains_io.py", 150), ("chains_cli.py", 150), ("seal.py", 150),
                      ("shadow_arm.py", 200), ("phase.py", 80), ("offline_demote.py", 200), ("arm_start.py", 200)):
        assert len((COMPILE / name).read_text().splitlines()) <= cap, name
    assert os.access(COMPILE / "__main__.py", os.R_OK) and proposal.PROPOSALS == "proposals"
