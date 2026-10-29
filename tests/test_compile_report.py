"""`compile report` (docs/design/compiler.md 8, minimal first cut): counts per proposal, corpora kept apart."""

import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import labels, report, shadow
from ace_playbook.compile import state as st

from . import compile_support as cs

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "ace_playbook" / "compile" / "report.py"
METRICS = SOURCE.with_name("metrics.py")
FILES = ("arms.py", "arms_stats.py", "arms_window.py", "labels.py", "labels_queue.py", "metrics.py", "report.py")
CODE_HASH = hashlib.sha256(b"\0".join(SOURCE.with_name(n).read_bytes() for n in FILES)).hexdigest()      # name order, NUL-joined
RM = "rm -rf /w/src"
TODAY = "2026-10-20T12:00:00Z"
PRIV = "Private corpus (aggregate, counts only)"
SYNTH = "Synthetic corpus (reproducible)"
SYN = {"ts": "2026-10-19T09:00:00Z", "session": "0123456789abcdef", "proposal_id": "P-0a1b2c3d",
       "lesson_id": "L-0a1b2c3d", "payload_sha256": "ab" * 32, "mode": "observe", "event": "PreToolUse",
       "tool_name": "Bash", "would_fire": True, "cause": "detector", "tier": "live", "corpus": "synthetic"}


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = cs.real_world(tmp_path)
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", world.conf)
    monkeypatch.setattr(report, "now_iso", lambda: TODAY)
    return world


@pytest.fixture
def fire(w, monkeypatch):
    """Produce one row with the REAL hook; `day` fakes the hook clock, `cmd` is the Bash command."""
    def go(cmd: str = RM, sid: str = "sess-live-1", day: str = "2026-10-20") -> None:
        monkeypatch.setattr(shadow, "now_iso", lambda: day + "T10:00:00Z")
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(cs.pre_payload(cmd, sid)).encode())))
        assert shadow.main(["pre"], w.cfg) == 0
    return go


def run(w, *args, capsys):
    code = cli.main(["--config", w.conf, "report", *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def pid(w) -> str:
    return w.props["rm_rf_nonscratch"]["id"]


def table(out: str, title: str) -> list[list[str]]:
    """The data rows of the table under `title` (header line skipped, block ends at a blank line)."""
    lines = out.split(title + "\n", 1)[1].split("\n\n", 1)[0].splitlines()
    return [ln.split() for ln in lines if ln.split() and ln.split()[0].startswith("P-")]


def put_syn(w, *rows: dict) -> None:
    for r in rows:
        cs.append_jsonl(w.cfg.path("shadow.synthetic.jsonl"), r)


def label_rows(w, *specs: tuple[str, str]) -> None:
    cs.write_labels(w.cfg, [{"ref": ref, "label": lab} for ref, lab in specs])


def test_with_no_data_both_sections_say_no_rows_and_the_header_hashes_the_arm_labels_metrics_and_report_source(w, capsys):
    code, out, err = run(w, capsys=capsys)
    assert (code, err) == (0, "") and out.count("no rows\n") == 2 and out.startswith("ace-playbook shadow report\n")
    got = re.search(r"^report_sha256: ([0-9a-f]{64})$", out, re.M).group(1)
    assert got == CODE_HASH
    assert PRIV in out and SYNTH in out
    assert "not computed here: arms, randomization inference, spillover flag" in out


def test_real_hook_rows_are_counted_per_proposal_with_sessions_days_and_cause_split(w, fire, capsys):
    fire(sid="sess-a", day="2026-10-18")
    fire(sid="sess-a", day="2026-10-18")
    fire(sid="sess-b", day="2026-10-20")
    fire("echo " + "a" * 70000, sid="sess-b", day="2026-10-20")          # the 64 KB cap cuts it: a truncated row
    code, out, _ = run(w, capsys=capsys)
    (row,) = table(out, PRIV)
    assert code == 0
    assert row == [pid(w), cs.RM_LESSON, "rm_rf_nonscratch", "PreToolUse", "observe", "observe", "4", "3", "1", "2", "2", "0", "0", "0",
                   "4", "2026-10-18", "2026-10-20"]


def test_the_header_names_the_columns_in_the_specified_order(w, fire, capsys):
    fire()
    out = run(w, capsys=capsys)[1]
    head = out.split(PRIV + "\n", 1)[1].splitlines()[1].split()
    assert head == ["proposal", "lesson", "detector", "event", "approved_mode", "mode", "rows", "detector", "truncated",
                    "sessions", "days", "tp", "fp", "tp_cand", "unlabelled", "first", "last"]


def test_tp_and_fp_labels_are_counted_the_newest_per_ref_wins_and_the_rest_are_unlabelled(w, fire, capsys):
    for sid in ("s1", "s2", "s3", "s4"):
        fire(sid=sid)
    ids = [labels.row_id(r) for r in cs.shadow_rows(w.data)]
    label_rows(w, (ids[0], "tp"), (ids[1], "fp"), (ids[1], "tp"), (ids[2], "tp"), (ids[2], "fp"))
    (row,) = table(run(w, capsys=capsys)[1], PRIV)
    assert row[11:15] == ["2", "1", "0", "1"]                                   # tp (0, 1), fp (2), unlabelled (3)


def test_incident_labels_do_not_count(w, fire, capsys):
    fire()
    rid = labels.row_id(cs.shadow_rows(w.data)[0])
    label_rows(w, (rid, "incident"))
    (row,) = table(run(w, capsys=capsys)[1], PRIV)
    assert row[11:15] == ["0", "0", "0", "1"]
    w.cfg.path("labels.jsonl").unlink()
    label_rows(w, (rid, "tp"), (rid, "incident"))                           # a later incident row never unseats a tp
    assert table(run(w, capsys=capsys)[1], PRIV)[0][11:15] == ["1", "0", "0", "0"]


def test_a_broken_chain_ignores_every_label_and_says_so(w, fire, capsys):
    fire()
    rid = labels.row_id(cs.shadow_rows(w.data)[0])
    label_rows(w, (rid, "tp"), ("zzzzzzzz", "fp"))
    path = w.cfg.path("labels.jsonl")
    first, second = path.read_text().splitlines()
    path.write_text(first + "\n" + second.replace('"fp"', '"tp"') + "\n")
    code, out, _ = run(w, capsys=capsys)
    assert code == 0 and "labels: chain BROKEN at row 2, labels ignored" in out
    assert table(out, PRIV)[0][11:15] == ["0", "0", "0", "1"]
    assert json.loads(run(w, "--json", capsys=capsys)[1])["labels"] == {"chain_ok": False, "broken_at": 2}


def test_a_symlinked_labels_file_exits_2(w, tmp_path, capsys):
    real = tmp_path / "elsewhere.jsonl"
    real.write_text("")
    w.cfg.path("labels.jsonl").symlink_to(real)
    code, out, err = run(w, capsys=capsys)
    assert (code, out) == (2, "") and "symlink" in err


def test_synthetic_rows_get_their_own_table_and_never_change_the_private_one(w, fire, capsys):
    fire()
    before = run(w, capsys=capsys)[1]
    put_syn(w, SYN, {**SYN, "session": "fedcba9876543210"})
    out = run(w, capsys=capsys)[1]
    assert table(out, PRIV) == table(before, PRIV)
    (row,) = table(out, SYNTH)
    assert row[0] == "P-0a1b2c3d" and row[6] == "2" and row[9] == "2" and row[11:15] == ["0", "0", "0", "2"]
    assert "no rows" in before and "no rows" not in out and "not computed here" in out


@pytest.mark.parametrize("private, message", [(True, "row with a non-private corpus in shadow.jsonl"),
                                              (False, "non-synthetic row found in the synthetic corpus")])
def test_the_corpus_guard_refuses_a_mixed_file_with_exit_2_and_prints_nothing_else(w, fire, capsys, private, message):
    fire()
    if private:
        cs.append_jsonl(w.cfg.path("shadow.jsonl"), {**SYN, "ts": "2026-10-20T10:00:00Z"})
    else:
        put_syn(w, SYN, {k: v for k, v in SYN.items() if k != "corpus"})
    code, out, err = run(w, capsys=capsys)
    assert (code, out) == (2, "") and message in err
    assert run(w, "--json", capsys=capsys)[:2] == (2, "")


def test_a_private_row_may_say_corpus_private(w, fire, capsys):
    fire()
    cs.append_jsonl(w.cfg.path("shadow.jsonl"), {**SYN, "corpus": "private", "ts": "2026-10-20T10:00:00Z"})
    code, out, _ = run(w, capsys=capsys)
    assert code == 0 and sorted(r[0] for r in table(out, PRIV)) == sorted([pid(w), "P-0a1b2c3d"])


def test_days_limits_the_per_day_block_but_not_the_totals(w, fire, capsys):
    for day in ("2026-10-01", "2026-10-18", "2026-10-19", "2026-10-20"):
        fire(day=day)
    out = run(w, "--days", "2", capsys=capsys)[1]
    block = out.split("Rows per day (last 2 days)\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert [ln.split() for ln in block if ln.startswith("2026")] == [["2026-10-19", pid(w), "1"],
                                                                      ["2026-10-20", pid(w), "1"]]
    assert "2026-10-18" not in "\n".join(block) and table(out, PRIV)[0][6] == "4"
    wide = run(w, capsys=capsys)[1].split("Rows per day")[1]               # default 14 days: 1st out, 18th in
    assert "2026-10-01" not in wide and "2026-10-18" in wide and "(last 14 days)" in wide


def test_a_row_dated_after_today_is_counted_in_rows_but_not_in_the_per_day_block(w, fire, capsys):
    fire(day="2026-10-20")
    fire(day="2026-10-21")
    out = run(w, capsys=capsys)[1]
    assert table(out, PRIV)[0][6] == "2" and table(out, PRIV)[0][-1] == "2026-10-21"
    block = out.split("Rows per day", 1)[1].split("\n\n", 1)[0]
    assert "2026-10-20" in block and "2026-10-21" not in block


def test_json_has_the_specified_shape_and_the_same_numbers_as_the_text(w, fire, capsys):
    fire(sid="sess-a", day="2026-10-19")
    fire(sid="sess-b")
    put_syn(w, SYN)
    code, out, _ = run(w, "--json", "--days", "7", capsys=capsys)
    obj = json.loads(out)
    assert code == 0 and set(obj) == {"report_sha256", "days", "private", "synthetic", "rates", "demotions", "labels"} and obj["days"] == 7
    assert obj["labels"] == {"chain_ok": True, "broken_at": None}
    assert obj["report_sha256"] == CODE_HASH
    prop = obj["private"]["proposals"][pid(w)]
    assert prop == {"lesson": cs.RM_LESSON, "detector": "rm_rf_nonscratch", "event": "PreToolUse",
                    "approved_mode": "observe", "mode": "observe", "rows": 2, "cause_detector": 2, "cause_truncated": 0, "sessions": 2,
                    "days": 2, "tp": 0, "fp": 0, "tp_cand": 0, "unlabelled": 2, "first": "2026-10-19", "last": "2026-10-20"}
    assert obj["private"]["per_day"] == [{"date": "2026-10-19", "proposal_id": pid(w), "rows": 1},
                                         {"date": "2026-10-20", "proposal_id": pid(w), "rows": 1}]
    assert set(obj["synthetic"]) == {"proposals", "per_day"}
    assert obj["synthetic"]["proposals"]["P-0a1b2c3d"]["rows"] == 1
    text = run(w, "--days", "7", capsys=capsys)[1]
    for title, key in ((PRIV, "private"), (SYNTH, "synthetic")):
        rows = {r[0]: r[1:] for r in table(text, title)}
        assert rows == {p: [str(v[f]) for f in report.FIELDS] for p, v in obj[key]["proposals"].items()}
        block = text.split(title + "\n", 1)[1].split("Rows per day", 1)[1].split("\n\n", 1)[0].splitlines()
        assert [ln.split() for ln in block if ln.startswith("2026")] == [
            [d["date"], d["proposal_id"], str(d["rows"])] for d in obj[key]["per_day"]]


def test_private_output_holds_no_session_hash_row_id_payload_hash_or_data_path(w, fire, capsys):
    fire(sid="sess-secret")
    row = cs.shadow_rows(w.data)[0]
    label_rows(w, (labels.row_id(row), "tp"))
    for args in ((), ("--json",)):
        _, out, err = run(w, *args, capsys=capsys)
        for banned in (st.session_hash("sess-secret"), "sess-secret", labels.row_id(row), row["payload_sha256"],
                       str(w.data), str(w.data.parent), "/w/src"):
            assert banned not in out + err, banned


def test_unreadable_missing_or_garbled_proposal_files_show_dashes_and_exit_0(w, fire, capsys):
    fire()
    pfile = w.data / "proposals" / (pid(w) + ".json")
    afile = w.data / "proposals" / "approved" / (pid(w) + ".json")
    orig_p, orig_a = pfile.read_bytes(), afile.read_bytes()
    pfile.unlink()
    afile.write_text("not json")
    code, out, _ = run(w, capsys=capsys)
    assert code == 0 and table(out, PRIV)[0][2:5] == ["-", "-", "-"]
    pfile.write_text("[1]")
    afile.unlink()
    code, out, _ = run(w, capsys=capsys)
    assert code == 0 and table(out, PRIV)[0][2:5] == ["-", "-", "-"]
    pfile.write_bytes(orig_p)
    afile.write_bytes(orig_a)
    assert table(run(w, capsys=capsys)[1], PRIV)[0][2:5] == ["rm_rf_nonscratch", "PreToolUse", "observe"]
    pfile.chmod(0)
    code, out, _ = run(w, capsys=capsys)
    assert code == 0 and table(out, PRIV)[0][2:5] == ["-", "-", "observe"]
    pfile.chmod(0o600)


def edit_json(path: Path, fn) -> None:
    rec = json.loads(path.read_text())
    path.write_text(json.dumps(fn(rec) or rec))


def test_metadata_must_be_a_known_detector_event_and_mode_never_a_free_string(w, fire, capsys):
    fire()
    pfile = w.data / "proposals" / (pid(w) + ".json")
    edit_json(pfile, lambda p: p["check"].update(detector="0123456789abcdef"))
    assert table(run(w, capsys=capsys)[1], PRIV)[0][2:5] == ["-", "PreToolUse", "observe"]
    edit_json(pfile, lambda p: p["check"].update(detector="rm_rf_nonscratch") or p.update(event="PostToolUse"))
    assert table(run(w, capsys=capsys)[1], PRIV)[0][2:5] == ["rm_rf_nonscratch", "-", "observe"]


@pytest.mark.parametrize("edit", [lambda rec: ["not", "a", "record"], lambda rec: rec.update(mode="block"),
                                  lambda rec: rec.pop("proposal_sha256")])
def test_an_approved_copy_of_the_wrong_shape_or_with_a_mismatched_hash_shows_a_dash_mode(w, fire, capsys, edit):
    fire()
    edit_json(w.data / "proposals" / "approved" / (pid(w) + ".json"), edit)
    assert table(run(w, capsys=capsys)[1], PRIV)[0][4] == "-"


def test_a_present_proposal_and_approved_copy_fill_the_metadata_columns(w, fire, capsys):
    fire()
    assert table(run(w, capsys=capsys)[1], PRIV)[0][2:5] == ["rm_rf_nonscratch", "PreToolUse", "observe"]


@pytest.mark.parametrize("arg", ["0", "-3", "3661", "1000000"])
def test_days_below_one_is_a_usage_error(w, capsys, arg):
    code, out, err = run(w, "--days", arg, capsys=capsys)
    assert (code, out) == (2, "") and "--days must be 1..3660" in err


def test_the_largest_allowed_window_works(w, fire, capsys):
    fire()
    assert run(w, "--days", "3660", capsys=capsys)[0] == 0


def test_report_help_renders(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["report", "--help"])
    out = capsys.readouterr().out
    assert e.value.code == 0 and "--days" in out and "--json" in out


def test_bad_lines_and_rows_missing_keys_are_skipped(w, fire, capsys):
    fire()
    with open(w.cfg.path("shadow.jsonl"), "a") as fh:
        fh.write('not json\n[1]\n{"ts": "x"}\n')
    assert table(run(w, capsys=capsys)[1], PRIV)[0][6] == "1"


def test_python_dash_m_report_runs_with_no_data(w):
    out = subprocess.run([sys.executable, "-m", "ace_playbook.compile", "--config", w.conf, "report", "--json"],
                         capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.returncode == 0 and json.loads(out.stdout)["private"] == {"proposals": {}, "per_day": []}


def test_identical_rows_share_a_row_id_so_one_label_counts_once(w, fire, capsys):
    fire(sid="sess-a")
    fire(sid="sess-a")                                                     # same fake second, session and payload
    fire(sid="sess-b")
    first, second, third = cs.shadow_rows(w.data)
    assert labels.row_id(first) == labels.row_id(second) != labels.row_id(third)
    label_rows(w, (labels.row_id(first), "tp"))
    assert table(run(w, capsys=capsys)[1], PRIV)[0][6:15] == ["3", "3", "0", "2", "1", "1", "0", "0", "1"]


def test_an_unparsable_ts_counts_in_rows_only(w, fire, capsys):
    fire()
    cs.append_jsonl(w.cfg.path("shadow.jsonl"), {**SYN, "corpus": "private", "proposal_id": pid(w), "ts": "garbage"})
    out = run(w, capsys=capsys)[1]
    row = table(out, PRIV)[0]
    assert row[6] == "2" and row[10] == "1" and row[15:] == ["2026-10-20", "2026-10-20"]
    assert out.split("Rows per day", 1)[1].count(pid(w)) == 1


def test_a_label_whose_ref_is_a_synthetic_rows_id_does_not_count_in_the_synthetic_table(w, capsys):
    put_syn(w, SYN)
    label_rows(w, (labels.row_id(SYN), "tp"))
    row = table(run(w, capsys=capsys)[1], SYNTH)[0]
    assert row[6] == "1" and row[11:15] == ["0", "0", "0", "1"]


def test_a_symlinked_shadow_file_exits_2(w, tmp_path, capsys):
    real = tmp_path / "elsewhere.jsonl"
    real.write_text("")
    w.cfg.path("shadow.jsonl").symlink_to(real)
    code, out, err = run(w, capsys=capsys)
    assert (code, out) == (2, "") and "shadow.jsonl is a symlink or not a regular file" in err
    assert str(tmp_path) not in err


def test_a_directory_in_place_of_a_shadow_file_exits_2(w, capsys):
    w.cfg.path("shadow.synthetic.jsonl").mkdir()
    code, out, err = run(w, capsys=capsys)
    assert (code, out) == (2, "") and "shadow.synthetic.jsonl" in err


def test_the_corpus_guard_looks_at_raw_rows_before_the_key_filter(w, capsys):
    put_syn(w, {"corpus": "private", "ts": "x"})
    assert run(w, capsys=capsys)[:2] == (2, "")
    w.cfg.path("shadow.synthetic.jsonl").unlink()
    cs.append_jsonl(w.cfg.path("shadow.jsonl"), {"corpus": "synthetic", "ts": "x"})
    assert run(w, capsys=capsys)[:2] == (2, "")


def test_a_non_utf8_shadow_file_exits_2_without_a_traceback(w, capsys):
    w.cfg.path("shadow.jsonl").write_bytes(b"\xff\xfe not utf8\n")
    code, out, err = run(w, capsys=capsys)
    assert (code, out) == (2, "") and err.startswith("error: ")


def test_the_demotions_section_lists_every_row_with_lifted_and_flags_a_broken_chain(w, capsys):
    from ace_playbook.compile import approvals, demotions as dm
    row = {"proposal_id": pid(w), "from_mode": "block", "to_mode": "warn", "cause": "label_fp", "ref": "aaaa1111",
           "review_required": False, "ts": "2026-10-10T00:00:00Z"}
    dm.append(w.data, row)
    dm.append(w.data, {**row, "from_mode": "warn", "to_mode": "observe", "cause": "label_fp_pair", "ref": "bbbb2222",
                       "review_required": True, "ts": "2026-10-12T00:00:00Z"})
    lines = lambda: [ln.split() for ln in run(w, capsys=capsys)[1].split("Demotions", 1)[1].splitlines() if ln.startswith(pid(w))]
    assert lines() == [[pid(w), "warn", "label_fp", "2026-10-10T00:00:00Z", "false", "no"],
                       [pid(w), "observe", "label_fp_pair", "2026-10-12T00:00:00Z", "true", "no"]]
    obj = json.loads(run(w, "--json", capsys=capsys)[1])["demotions"]
    assert obj["chain_ok"] is True and obj["proposals"][pid(w)]["rows"] == 2 and obj["proposals"][pid(w)]["cap"] == "observe"
    approvals.append(w.data, {"proposal_id": pid(w), "proposal_sha256": "a" * 64, "mode": "observe", "ts": "2026-10-11T00:00:00Z"})
    assert [r[-1] for r in lines()] == ["yes", "no"]                         # the approval lifts the older row only
    obj = json.loads(run(w, "--json", capsys=capsys)[1])["demotions"]
    assert obj["proposals"][pid(w)] | {"ts": 0} == {"cap": "observe", "cause": "label_fp_pair", "ts": 0, "review_required": True,
                                                    "rows": 2, "lifted_rows": 1}
    assert [r["lifted"] for r in obj["rows"]] == [True, False]
    approvals.append(w.data, {"proposal_id": pid(w), "proposal_sha256": "b" * 64, "mode": "observe", "ts": "2026-10-13T00:00:00Z"})
    assert [r[-1] for r in lines()] == ["yes", "yes"]
    assert json.loads(run(w, "--json", capsys=capsys)[1])["demotions"]["proposals"][pid(w)]["cap"] == "-"
    path = w.data / "demotions.jsonl"
    path.write_text(path.read_text().replace("observe", "disabled"))
    out = run(w, capsys=capsys)[1]
    assert "demotions: chain BROKEN at row 2" in out                         # operator rows count from 1 ...
    assert json.loads(run(w, "--json", capsys=capsys)[1])["demotions"]["broken_at"] == 1    # ... the JSON keeps the 0-based index
    path.unlink()
    assert "no demotions" in run(w, capsys=capsys)[1]
    path.symlink_to(w.data / "elsewhere")
    assert run(w, capsys=capsys)[0] == 2


def test_run_is_collect_plus_printing_so_the_json_and_the_text_are_unchanged_by_the_split(w, fire, capsys):
    """`report.run` was split into `collect()` and `run()` (the report hash changed with it): the --json output is exactly the dumped
    `collect` dict and the text exactly `render` of its parts."""
    fire()
    fire(sid="sess-live-2", cmd="rm -rf /w/other")
    d = report.collect(w.cfg, 7, TODAY)
    assert report.run(w.cfg, 7, True, TODAY) == 0
    assert capsys.readouterr().out == json.dumps(d, indent=1, sort_keys=True) + "\n"
    assert report.run(w.cfg, 7, False, TODAY) == 0
    assert capsys.readouterr().out == report.render(d["report_sha256"], 7, d["private"], d["synthetic"], d["labels"]["broken_at"],
                                                    d["demotions"], d["rates"])
