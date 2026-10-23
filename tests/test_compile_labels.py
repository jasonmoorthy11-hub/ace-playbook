"""`compile label` (docs/design/compiler.md 7.3): chained human labels, TTY only, scrubbed notes, list and rows."""

import dataclasses
import hashlib
import json
import io
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ace_playbook.canon import GENESIS, record_hash
from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import catalogue, evidence, labels

from . import compile_support as cs

ROOT = Path(__file__).resolve().parent.parent
EMAIL = "jane.doe" + "@" + "invented-corp.io"       # assembled so no address literal sits in the source
AWS = "AK" + "IA" + "B" * 16
ROW = {"ts": "2026-10-20T09:00:00Z", "session": "sid:0123456789abcdef", "proposal_id": "P-0a1b2c3d",
       "payload_sha256": "ab" * 32, "lesson_id": "L-0a1b2c3d", "cause": "detector", "would_fire": True,
       "mode": "observe", "event": "PreToolUse", "tool_name": "Bash", "tier": "live"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = cs.make_world(tmp_path, monkeypatch, legacy=True)
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(w.cfg.data_dir), "transcripts_root": str(w.cfg.transcripts_root)}))
    w.conf = str(conf)
    w.session = w.sid("later")
    return w


class FakeTty:
    def isatty(self):
        return True

    def read(self, *a):
        return ""


@pytest.fixture
def tty(monkeypatch):
    """stdin looks like a terminal; the /dev/tty prompt is answered from `ask.answer` and recorded."""
    ask = type("Ask", (), {"answer": "y", "asked": []})()
    monkeypatch.setattr(sys, "stdin", FakeTty())
    monkeypatch.setattr(cli, "_ask", lambda prompt: (ask.asked.append(prompt), ask.answer)[1])
    return ask


def run(world, *args, capsys):
    code = cli.main(["--config", world.conf, "label", *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def incident(world, capsys, *extra, session=None):
    return run(world, "--incident", "--lesson", world.lesson_id, "--session", session or world.session, *extra,
               capsys=capsys)


def rows_of(world) -> list[dict]:
    p = world.cfg.path("labels.jsonl")
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def put_shadow(world, **kw) -> str:
    row = {**ROW, **kw}
    cs.append_jsonl(world.cfg.path("shadow.jsonl"), row)
    return labels.row_id(row)


# ------------------------------------------------------------------ chain
def test_rows_chain_from_genesis_and_every_hash_verifies(world, tty, capsys):
    rid = put_shadow(world)
    assert incident(world, capsys)[0] == 0
    assert run(world, "--tp", rid, capsys=capsys)[0] == 0
    first, second = rows_of(world)
    assert first["prev_hash"] == GENESIS and second["prev_hash"] == first["hash"]
    assert all(r["hash"] == record_hash(r) for r in (first, second))
    assert (first["label"], first["reason_code"], second["label"], second["ref"]) == ("incident", "human", "tp", rid)


def test_incident_row_carries_lesson_and_hashed_session_never_the_raw_id(world, tty, capsys):
    incident(world, capsys, "--note", "  checked by hand  ")
    (row,) = rows_of(world)
    assert row["lesson_id"] == world.lesson_id and row["session"] == evidence.sid(world.session)
    assert row["note"] == "checked by hand" and row["ref"] == world.ref("later")
    assert world.session not in world.cfg.path("labels.jsonl").read_text()


def test_a_tampered_row_makes_list_print_broken_and_incident_refuse(world, tty, capsys):
    put_shadow(world)
    incident(world, capsys)
    run(world, "--tp", labels.row_id(ROW), capsys=capsys)
    path = world.cfg.path("labels.jsonl")
    first, second = path.read_text().splitlines()
    path.write_text(first + "\n" + second.replace('"tp"', '"fp"') + "\n")
    code, out, _ = run(world, "--list", capsys=capsys)
    assert code == 1 and out.splitlines()[-1] == "chain BROKEN at row 2" and "incident" in out
    code, out, err = incident(world, capsys, session=world.session)
    assert code == 1 and "labels.jsonl chain broken at row 2" in err
    assert len(path.read_text().splitlines()) == 2


# ------------------------------------------------------------------ refusals
@pytest.mark.parametrize("action", ["incident", "tp", "fp"])
def test_every_writing_action_refuses_without_a_terminal_and_creates_no_file(world, capsys, action):
    args = (["--incident", "--lesson", world.lesson_id, "--session", world.session] if action == "incident"
            else [f"--{action}", labels.row_id(ROW)])
    code, _, err = run(world, *args, capsys=capsys)
    assert code == 2 and "terminal" in err and not world.cfg.path("labels.jsonl").exists()


@pytest.mark.parametrize("secret", [EMAIL, AWS])
def test_a_note_with_a_scrub_hit_is_refused_and_never_echoed(world, tty, capsys, secret):
    code, out, err = incident(world, capsys, "--note", f"see {secret} for details")
    assert code == 1 and "note" in err and "scrubber" in err
    assert secret not in out + err and tty.asked == [] and not world.cfg.path("labels.jsonl").exists()


def test_a_note_longer_than_200_characters_is_a_usage_error(world, tty, capsys):
    assert incident(world, capsys, "--note", "x" * 201)[0] == 2
    assert incident(world, capsys, "--note", "x" * 200)[0] == 0


def test_a_symlinked_labels_file_is_refused(world, tty, capsys, tmp_path):
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("")
    world.cfg.path("labels.jsonl").symlink_to(target)
    assert incident(world, capsys)[0] == 2
    assert run(world, "--list", capsys=capsys)[0] == 2
    assert target.read_text() == ""


def test_incident_needs_lesson_and_session_and_a_wellformed_lesson_id(world, tty, capsys):
    assert run(world, "--incident", capsys=capsys)[0] == 2
    assert run(world, "--incident", "--lesson", "L-1", "--session", "s", capsys=capsys)[0] == 2
    code, _, err = run(world, "--incident", "--lesson", "L-00000000", "--session", "s", capsys=capsys)
    assert code == 1 and "unknown" in err
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", world.conf, "label", "--tp", "x", "--fp", "y"])
    assert e.value.code == 2


def test_a_lesson_with_no_mapped_detector_is_refused(world, tty, capsys, monkeypatch):
    row = catalogue.CATALOGUE["rm_rf_nonscratch"]
    monkeypatch.setitem(catalogue.CATALOGUE, "rm_rf_nonscratch", dataclasses.replace(row, lessons=()))
    code, _, err = incident(world, capsys)
    assert code == 1 and "no_mapped_detector" in err


def test_a_session_where_the_lesson_was_never_eligible_is_refused(world, tty, capsys):
    other = world.add_session("other", [{"human": "go"}, {"tool": "Bash", "input": {"command": cs.RM_CMD}}])
    code, _, err = incident(world, capsys, session=other)
    assert code == 1 and "never eligible" in err and rows_of(world) == [] and tty.asked == []


def test_an_eligible_session_without_a_mapped_fire_is_refused(world, tty, capsys):
    quiet = world.add_session("quiet", [{"human": "go"}, {"tool": "Bash", "input": {"command": "ls /w"}}])
    cs.append_jsonl(world.cfg.injections, {"session_id": quiet, "shown_ids": [world.lesson_id], "holdout_ids": []})
    code, _, err = incident(world, capsys, session=quiet)
    assert code == 1 and "no mapped detector fires" in err and rows_of(world) == []


def test_a_missing_transcript_is_refused(world, tty, capsys):
    next(Path(world.cfg.transcripts_root).rglob("*.jsonl")).unlink()
    code, _, err = incident(world, capsys)
    assert code == 1 and "transcript not found" in err


# ------------------------------------------------------------------ incident flow
def test_incident_label_end_to_end_admits_exactly_one_legacy_card(world, tty, capsys):
    assert evidence.build_cards(world.cfg, world.lesson_id, write=False)[0] == []
    code, out, _ = incident(world, capsys)
    assert code == 0 and f"ok: labelled {world.ref('later')} (row 1)" in out
    assert "rm_rf_nonscratch" in out and "Bash" in out and cs.RM_CMD in out
    assert tty.asked == [f"Label this as the incident for {world.lesson_id}? [y/N] "]
    (card,), refused = evidence.build_cards(world.cfg, world.lesson_id, write=False)
    (row,) = rows_of(world)
    assert refused == [] and card["provenance"] == "legacy" and card["id"] == world.ref("later")
    assert card["label_ref"] == "lab:" + row["hash"][:8]


def test_the_same_incident_labelled_twice_leaves_one_row(world, tty, capsys):
    incident(world, capsys)
    tty.asked.clear()
    code, out, _ = incident(world, capsys)
    assert code == 0 and "already labelled" in out and len(rows_of(world)) == 1 and tty.asked == []


@pytest.mark.parametrize("answer", ["n", "", "maybe"])
def test_anything_but_yes_writes_nothing(world, tty, capsys, answer):
    tty.answer = answer
    code, _, err = incident(world, capsys)
    assert code == 1 and "not labelled" in err and not world.cfg.path("labels.jsonl").exists()


def test_the_preview_masks_a_secret_and_cuts_to_120_characters(world, tty, capsys):
    cmd = f"{cs.RM_CMD} && echo {AWS} " + "z" * 200
    sess = world.add_session("secret", [{"human": "go"}, {"tool": "Bash", "input": {"command": cmd}}])
    cs.append_jsonl(world.cfg.injections, {"session_id": sess, "shown_ids": [world.lesson_id], "holdout_ids": []})
    tty.answer = "n"
    _, out, _ = incident(world, capsys, session=sess)
    shown = next(x for x in out.splitlines() if "rm -rf" in x)
    assert AWS not in out and "[HIT:aws_access_key]" in shown and "z" * 100 not in shown


# ------------------------------------------------------------------ tp / fp
@pytest.mark.parametrize("kind", ["tp", "fp"])
def test_tp_and_fp_write_a_row_whose_ref_is_the_shadow_row_id(world, tty, capsys, kind):
    rid = put_shadow(world)
    code, out, _ = run(world, f"--{kind}", rid, "--note", "looked at it", capsys=capsys)
    (row,) = rows_of(world)
    assert code == 0 and f"ok: labelled {rid} (row 1)" in out and ROW["proposal_id"] in out
    assert (row["ref"], row["label"], row["note"]) == (rid, kind, "looked at it") and "lesson_id" not in row
    assert tty.asked == [f"Label row {rid} as {kind}? [y/N] "]


def test_an_unknown_row_id_is_refused(world, tty, capsys):
    put_shadow(world)
    code, _, err = run(world, "--tp", "deadbeef", capsys=capsys)
    assert code == 1 and "no shadow row with id deadbeef" in err and tty.asked == []


def test_the_opposite_label_is_allowed_with_a_warning_and_the_same_label_is_not_repeated(world, tty, capsys):
    rid = put_shadow(world)
    run(world, "--tp", rid, capsys=capsys)
    code, out, _ = run(world, "--tp", rid, capsys=capsys)
    assert code == 0 and "already labelled" in out and len(rows_of(world)) == 1
    code, out, _ = run(world, "--fp", rid, capsys=capsys)
    assert code == 0 and "warning" in out and "tp" in out and [r["label"] for r in rows_of(world)] == ["tp", "fp"]


def test_a_hook_written_shadow_row_can_be_labelled(tmp_path, monkeypatch, capsys, tty):
    w = cs.real_world(tmp_path)
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", w.conf)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(cs.pre_payload(cs.RM_CMD)).encode())))
    from ace_playbook.compile import shadow
    shadow.main(["pre"])
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", FakeTty())
    (hook_row,) = cs.shadow_rows(w.data)
    code = cli.main(["--config", w.conf, "label", "--tp", labels.row_id(hook_row)])
    assert code == 0 and json.loads((w.data / "labels.jsonl").read_text())["ref"] == labels.row_id(hook_row)


def test_relabelling_back_to_the_first_label_is_a_new_row_not_already_labelled(world, tty, capsys):
    rid = put_shadow(world)
    codes = [run(world, f"--{k}", rid, capsys=capsys) for k in ("tp", "fp", "tp")]
    assert [c[0] for c in codes] == [0, 0, 0] and all("already labelled" not in c[1] for c in codes)
    assert [r["label"] for r in rows_of(world)] == ["tp", "fp", "tp"]
    assert run(world, "--tp", rid, capsys=capsys)[1].strip().endswith("already labelled")


def test_the_write_time_check_reads_the_locked_file_not_the_earlier_look(world, tty, capsys, monkeypatch):
    put_shadow(world)
    cs.write_labels(world.cfg, [{"ref": "E-00000000", "label": "fp"}])
    path = world.cfg.path("labels.jsonl")
    path.write_text(path.read_text().replace("E-00000000", "E-00000001"))
    monkeypatch.setattr(labels, "check_chain", lambda cfg: [])      # the pre-prompt look saw nothing wrong
    code, _, err = run(world, "--tp", labels.row_id(ROW), capsys=capsys)
    assert code == 1 and "chain broken at row 1" in err and len(rows_of(world)) == 1


def test_a_symlink_swapped_in_after_the_pre_prompt_check_is_refused_untouched(world, tty, capsys, monkeypatch, tmp_path):
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("")
    path = world.cfg.path("labels.jsonl")

    def swap(cfg):
        path.symlink_to(target)
        return []

    monkeypatch.setattr(labels, "check_chain", swap)
    code, _, _ = incident(world, capsys)
    assert code == 2 and target.read_text() == ""


def test_creating_the_file_fsyncs_the_data_dir_once(world, tty, capsys, monkeypatch):
    seen, real = [], os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (seen.append(stat.S_ISDIR(os.fstat(fd).st_mode)), real(fd))[1])
    incident(world, capsys)
    assert seen.count(True) == 1
    run(world, "--fp", put_shadow(world), capsys=capsys)
    assert seen.count(True) == 1


def test_a_lesson_with_standard_user_evidence_is_refused(tmp_path, monkeypatch, tty, capsys):
    w = cs.make_world(tmp_path, monkeypatch)
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(w.cfg.data_dir), "transcripts_root": str(w.cfg.transcripts_root)}))
    w.conf = str(conf)
    code, _, err = run(w, "--incident", "--lesson", w.lesson_id, "--session", w.sid("later"), capsys=capsys)
    assert code == 1 and "standard user evidence" in err and "legacy-evidence lessons only" in err
    assert rows_of(w) == [] and tty.asked == []


def test_free_text_from_the_files_is_printed_without_control_characters(world, capsys):
    cs.write_labels(world.cfg, [{"ref": "E-0000\x1b[31m0000", "label": "fp", "note": "a\x1b[2Jb\x07", "lesson_id": "L-\x1b1"}])
    cs.append_jsonl(world.cfg.path("shadow.jsonl"), {**ROW, "cause": "x\x1b[0m", "lesson_id": "L-\x9b2"})
    out = run(world, "--list", capsys=capsys)[1] + run(world, "--rows", capsys=capsys)[1]
    assert "\x1b" not in out and "\x07" not in out and "\x9b" not in out
    assert "a[2Jb" in out and "x[0m" in out


@pytest.mark.parametrize("args", [
    ["--tp", "abcd1234", "--session", "s"], ["--fp", "abcd1234", "--lesson", "L-00000000"], ["--list", "--note", "n"],
    ["--list", "--limit", "5"], ["--rows", "--kind", "tp"], ["--rows", "--session", "s"],
    ["--incident", "--lesson", "L-00000000", "--session", "s", "--kind", "tp"]])
def test_flags_that_do_not_apply_to_the_action_are_a_usage_error(world, tty, capsys, args):
    code, _, err = run(world, *args, capsys=capsys)
    assert code == 2 and "do not apply" in err and tty.asked == []


# ------------------------------------------------------------------ list / rows
def test_list_prints_one_line_per_row_and_a_chain_summary_without_a_terminal(world, tty, capsys, monkeypatch):
    rid = put_shadow(world)
    incident(world, capsys, "--note", "first")
    run(world, "--fp", rid, capsys=capsys)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))              # not a terminal
    code, out, _ = run(world, "--list", capsys=capsys)
    lines = out.splitlines()
    assert code == 0 and len(lines) == 3 and lines[-1] == "chain ok (2 rows)"
    inc = lines[0].split()
    assert inc[0] == "1" and inc[2:5] == ["incident", world.ref("later"), world.lesson_id] and inc[-1] == "first"
    assert lines[1].split()[2:] == ["fp", rid, "-", "-", "-"]


def test_list_kind_filters_and_an_empty_log_is_a_valid_chain(world, tty, capsys):
    assert run(world, "--list", capsys=capsys)[1] == "chain ok (0 rows)\n"
    rid = put_shadow(world)
    incident(world, capsys)
    run(world, "--tp", rid, capsys=capsys)
    out = run(world, "--list", "--kind", "tp", capsys=capsys)[1].splitlines()
    assert len(out) == 2 and out[0].split()[2] == "tp" and out[0].split()[0] == "2" and out[1] == "chain ok (2 rows)"


def test_rows_prints_the_computed_ids_newest_last_and_honours_limit(world, capsys):
    ids = [put_shadow(world, ts=f"2026-10-20T09:00:0{i}Z") for i in range(3)]
    code, out, _ = run(world, "--rows", capsys=capsys)
    got = [line.split()[1] for line in out.splitlines()]
    assert code == 0 and got == ids and len(set(ids)) == 3
    assert out.splitlines()[0].split() == ["2026-10-20T09:00:00Z", ids[0], ROW["proposal_id"], ROW["lesson_id"],
                                           "detector", "true", "observe"]
    assert [x.split()[1] for x in run(world, "--rows", "--limit", "2", capsys=capsys)[1].splitlines()] == ids[1:]
    assert run(world, "--rows", "--limit", "0", capsys=capsys)[0] == 2
    assert run(world, "--rows", capsys=capsys)[0] == 0


def test_row_id_is_the_first_8_hex_of_sha256_over_ts_session_proposal_and_payload_hash():
    want = hashlib.sha256("|".join([ROW["ts"], ROW["session"], ROW["proposal_id"], ROW["payload_sha256"]]).encode())
    assert labels.row_id(ROW) == want.hexdigest()[:8] == labels.row_id({**ROW, "cause": "other"})
    assert labels.row_id({**ROW, "ts": "2026-10-20T09:00:01Z"}) != labels.row_id(ROW)


def test_rows_tolerates_bad_lines_and_a_missing_file(world, capsys):
    assert run(world, "--rows", capsys=capsys)[:2] == (0, "")
    rid = put_shadow(world)
    with open(world.cfg.path("shadow.jsonl"), "a") as fh:
        fh.write("not json\n[1]\n")
    assert [x.split()[1] for x in run(world, "--rows", capsys=capsys)[1].splitlines()] == [rid]


# ------------------------------------------------------------------ files
def test_the_labels_file_is_0600_in_a_0700_data_dir(world, tty, capsys):
    os.chmod(world.cfg.data_dir, 0o755)
    incident(world, capsys)
    assert oct(world.cfg.path("labels.jsonl").stat().st_mode & 0o777) == "0o600"
    assert oct(world.cfg.data_dir.stat().st_mode & 0o777) == "0o700"


def test_an_existing_file_without_a_final_newline_is_not_merged_into_the_new_row(world, tty, capsys):
    path = world.cfg.path("labels.jsonl")
    cs.write_labels(world.cfg, [{"ref": "E-00000000", "label": "fp"}])
    path.write_text(path.read_text().rstrip("\n"))
    assert incident(world, capsys)[0] == 0
    assert len(rows_of(world)) == 2 and run(world, "--list", capsys=capsys)[1].endswith("chain ok (2 rows)\n")


# ------------------------------------------------------------------ hygiene
def test_label_module_imports_no_heavy_modules_and_main_stays_light():
    heavy = ("sqlite3", "ace_playbook.store", "ace_playbook.scrub", "ace_playbook.clean", "subprocess", "socket")
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); import ace_playbook.compile.labels; "
            f"print(','.join(sorted(m for m in sys.modules if any(m == h or m.startswith(h + '.') for h in {heavy!r}))))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "", out.stderr


def test_label_help_renders(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["label", "--help"])
    out = capsys.readouterr().out
    assert e.value.code == 0 and all(f in out for f in ("--incident", "--tp", "--fp", "--list", "--rows"))
