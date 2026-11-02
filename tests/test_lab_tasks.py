"""`lab/tasks.py`: the synthetic task specs are data, `build` writes a reproducible tiny git repo."""

import subprocess

import pytest

from ace_playbook.lab import tasks


def by_lesson(lesson):
    return [t for t in tasks.TASKS if t.lesson == lesson]


def test_each_lesson_has_at_least_six_variants_and_ids_are_unique():
    assert len(by_lesson("preclaim")) >= 6 and len(by_lesson("edit_unread")) >= 6
    ids = [t.id for t in tasks.TASKS]
    assert len(ids) == len(set(ids)) and {t.lesson for t in tasks.TASKS} == {"preclaim", "edit_unread"}
    assert set(tasks.LESSON_IDS) == {"preclaim", "edit_unread"}


def test_turn_counts_and_permission_mode():
    for t in by_lesson("preclaim"):
        assert len(t.turns) == 1 and "done" in t.turns[0].lower()          # the prompt invites a completion message
    for t in by_lesson("edit_unread"):
        assert len(t.turns) >= 2
    assert {t.permission_mode for t in tasks.TASKS} == {"acceptEdits"}


def test_specs_are_tiny_clean_data():
    for t in tasks.TASKS:
        assert t.files and all(len(c) < 1200 and not n.startswith("/") and ".." not in n for n, c in t.files.items())
        assert all("/Users/" not in c for c in t.files.values()) and all("/Users/" not in p for p in t.turns)
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
    assert git(work, "log", "--format=%an <%ae>").splitlines() == ["lab <lab@example.invalid>"]
    assert git(work, "status", "--short") == ""


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
