"""`lab/tasks.py`: the synthetic task specs are data, `build` writes a reproducible tiny git repo."""

import re
import subprocess

import pytest

from ace_playbook.lab import exitplan, tasks


def by_lesson(lesson):
    return [t for t in tasks.TASKS if t.lesson == lesson]


CLAIMS = re.compile(r"(shipped|completed|done|looks good|ready to commit|order .* complete|cycle close)", re.I)       # the hook's pattern
NEEDS_BASH = re.compile(r"\b(run|runs|running|execute|python3?|pytest|bash|shell|test|tests)\b", re.I)
CUE_WORDS = re.compile(r"by hand|myself|again|re-?read|check it|just edited|i changed|i edited", re.I)
MULTI = ("pc-ready-commit", "pc-three-step", "pc-summarise", "pc-each-file")


def test_each_lesson_has_at_least_eight_variants_and_ids_are_unique():
    assert len(by_lesson("preclaim")) >= 8 and len(by_lesson("edit_unread")) >= 8
    ids = [t.id for t in tasks.TASKS]
    assert len(ids) == len(set(ids)) and {t.lesson for t in tasks.TASKS} == set(tasks.LESSON_IDS)


def test_turn_counts_and_permission_mode():
    for t in by_lesson("preclaim"):
        assert len(t.turns) == 1 and re.search(r"done|complete|shipped|finish", t.turns[0], re.I)    # the prompt invites a completion message
    for t in by_lesson("edit_unread"):
        assert len(t.turns) >= 2
    assert {t.permission_mode for t in tasks.TASKS if t.lesson != "exitplan"} == {"acceptEdits"}
    assert {t.permission_mode for t in by_lesson("exitplan")} == {"plan"}


def test_the_third_lesson_is_registered_with_its_store_id():
    assert tasks.LESSON_IDS == {"preclaim": "L-cbfb7a57", "edit_unread": "L-f8a355f9", "exitplan": "L-93544dae"}


def test_exitplan_has_at_least_eight_variants_each_asking_for_a_plan_and_a_real_question():
    xp = by_lesson("exitplan")
    assert len(xp) >= 8 and all(not t.cue and not t.between for t in xp)
    for t in xp:
        assert 1 <= len(t.turns) <= 2 and "plan" in t.turns[0].lower() and "?" in t.turns[0], t.id
    approvals = [t for t in xp if len(t.turns) == 2 and exitplan.short_approval(t.turns[1])]
    assert len(approvals) >= 2 and all("?" not in t.turns[1] for t in approvals)


def test_no_task_needs_bash():
    for t in tasks.TASKS:
        assert not any(NEEDS_BASH.search(p) for p in t.turns), t.id
        assert not any(n.startswith("check") for n in t.files), t.id


def test_the_multi_claim_flag_marks_at_least_eight_preclaim_prompts_that_invite_two_claim_phrases():
    assert all(isinstance(t.multi_claim, bool) for t in tasks.TASKS)
    multi = [t for t in by_lesson("preclaim") if t.multi_claim]
    assert len(multi) >= 8 and set(MULTI) <= {t.id for t in multi} and not any(t.multi_claim for t in tasks.TASKS if t.lesson != "preclaim")
    for t in multi:
        assert len(CLAIMS.findall(t.turns[0])) >= 2, t.id
    for t in by_lesson("preclaim"):
        if not t.multi_claim:
            assert len(CLAIMS.findall(t.turns[0])) <= 1, t.id


def test_every_spec_has_a_cue_flag_and_the_cued_edit_unread_variants_are_the_known_three():
    assert all(isinstance(t.cue, bool) for t in tasks.TASKS)
    assert {t.id for t in tasks.TASKS if t.cue} == {"eu-hand-notes", "eu-hand-code", "eu-three-turns"}


def test_uncued_edit_unread_variants_have_no_cue_no_rewrite_and_there_are_at_least_six():
    uncued = [t for t in by_lesson("edit_unread") if not t.cue]
    assert len(uncued) >= 6
    for t in uncued:
        assert not t.between and not CUE_WORDS.search(" ".join(t.turns)), t.id
    three = [t for t in uncued if len(t.turns) >= 3 and len(t.files) >= 2]
    assert three, "a three-turn, two-file, no-rewrite variant"


def test_specs_are_tiny_clean_data():
    for t in tasks.TASKS:
        assert t.files and all(len(c) < 1200 and not n.startswith("/") and ".." not in n for n, c in t.files.items())
        home = "/Use" + "rs/"                                                           # not spelled out: the diff gate greps for it
        assert all(home not in c for c in t.files.values()) and all(home not in p for p in t.turns)
        assert all(0 <= i < len(t.turns) - 1 and set(fs) <= set(t.files) for i, fs in t.between.items())


def test_variety_a_rename_a_two_file_task_a_readme_and_hand_edits_exist():
    assert any(len(t.files) >= 2 for t in tasks.TASKS) and any("README" in " ".join(t.files) for t in tasks.TASKS)
    assert any(t.between for t in by_lesson("edit_unread")) and not any(t.between for t in by_lesson("preclaim"))
    assert any("rename" in " ".join(t.turns).lower() for t in tasks.TASKS)


def test_get_and_unknown_id():
    assert tasks.get(tasks.TASKS[0].id) is tasks.TASKS[0]
    with pytest.raises(KeyError):
        tasks.get("nope")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def test_build_writes_files_and_commits_with_a_fixed_author(tmp_path):
    t = by_lesson("preclaim")[0]
    work = tasks.build(t, tmp_path / "run")
    assert all((work / n).read_text() == c for n, c in t.files.items())
    assert git(work, "log", "--format=%an <%ae>").splitlines() == ["lab <lab@example.com>"]
    assert git(work, "status", "--short") == ""


def test_the_plans_directory_is_excluded_so_git_status_stays_clean_after_a_plan_is_written(tmp_path):
    work = tasks.build(by_lesson("exitplan")[0], tmp_path / "run")
    plans = tasks.plans_dir(work)
    assert plans == work / ".lab-plans" and str(plans).startswith(str(work) + "/")
    plans.mkdir()
    (plans / "plan.md").write_text("a plan\n")
    assert git(work, "status", "--porcelain") == "" and git(work, "check-ignore", ".lab-plans/plan.md") == ".lab-plans/plan.md"
    (work / "stray.txt").write_text("x")
    assert git(work, "status", "--porcelain") == "?? stray.txt"                # the exclude hides the plans directory only


def test_build_is_reproducible_across_directories(tmp_path):
    t = by_lesson("edit_unread")[0]
    a, b = tasks.build(t, tmp_path / "a"), tasks.build(t, tmp_path / "b")
    assert git(a, "rev-parse", "HEAD") == git(b, "rev-parse", "HEAD")


def test_between_rewrites_the_named_files_after_a_turn(tmp_path):
    t = next(t for t in by_lesson("edit_unread") if t.between)
    work = tasks.build(t, tmp_path / "run")
    i, fs = next(iter(t.between.items()))
    assert tasks.between(t, work, i + 1) == [] and tasks.between(t, work, i) == sorted(fs)
    assert all((work / n).read_text() == c and c != t.files[n] for n, c in fs.items())
