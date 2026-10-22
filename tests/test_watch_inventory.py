"""`compile/watch/inventory.py`: the snapshot keeps matching lines only. Files live under tmp_path; nothing reads the real HOME."""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from ace_playbook.compile.watch import inventory

ROOT = Path(__file__).resolve().parent.parent
NOW = "2026-10-21T12:00:00Z"


def mapping(tmp_path, **targets):
    return {"targets": {k: {"path": str(tmp_path / p), "pattern": rx} for k, (p, rx) in targets.items()}, "docs": {}, "lead_keywords": []}


def snap(m, version=None):
    fake_run = lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=f"{version}\n" if version else "", stderr="")  # noqa: E731
    return inventory.build(m, NOW, which=lambda n: "/x/claude" if version else None, run=fake_run)


def test_only_matching_lines_with_numbers_and_the_file_hash_are_kept(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text('{\n  "theme": "dark",\n  "model": "claude-old-sonnet-20250101",\n  "secret": "TOPSECRETVALUE"\n}\n')
    inv = snap(mapping(tmp_path, **{"settings.model": ("settings.json", '"model"')}))
    entry = inv["targets"]["settings.model"]["files"][str(f)]
    assert entry["lines"] == [[3, '  "model": "claude-old-sonnet-20250101",']] and len(entry["sha256"]) == 64
    assert "TOPSECRETVALUE" not in json.dumps(inv) and "theme" not in json.dumps(inv)
    assert inv["generated_at"] == NOW and inv["claude_version"] is None


def test_a_missing_file_and_an_unmatched_glob_are_recorded_as_missing(tmp_path):
    inv = snap(mapping(tmp_path, a=("nope.json", "x"), b=("agents/*.md", "^model:")))
    assert inv["targets"]["a"]["files"] == {str(tmp_path / "nope.json"): {"missing": True}}
    assert inv["targets"]["b"]["files"] == {str(tmp_path / "agents/*.md"): {"missing": True}}


def test_globs_expand_and_each_file_gets_its_own_entry(tmp_path):
    (tmp_path / "agents").mkdir()
    for n, body in (("a", "model: m1\nname: a\n"), ("b", "name: b\nmodel: m2\n"), ("c", "name: c\n")):
        (tmp_path / "agents" / f"{n}.md").write_text(body)
    files = snap(mapping(tmp_path, k=("agents/*.md", "^model:")))["targets"]["k"]["files"]
    assert sorted(Path(p).name for p in files) == ["a.md", "b.md", "c.md"]
    assert [v["lines"] for _, v in sorted(files.items())] == [[[1, "model: m1"]], [[2, "model: m2"]], []]


def test_home_is_shown_as_a_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "s.json").write_text('"model": 1\n')
    m = {"targets": {"k": {"path": "~/.claude/s.json", "pattern": "model"}}, "docs": {}, "lead_keywords": []}
    assert list(snap(m)["targets"]["k"]["files"]) == ["~/.claude/s.json"]


def test_a_large_file_is_not_read_and_line_count_and_length_are_capped(tmp_path):
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * (inventory.MAX_BYTES + 1))
    many = tmp_path / "many.txt"
    many.write_text("hit " + "y" * 1000 + "\n" + "hit\n" * 500)
    files = snap(mapping(tmp_path, a=("big.txt", "x"), b=("many.txt", "hit")))["targets"]
    assert files["a"]["files"][str(big)] == {"too_large": True}
    lines = files["b"]["files"][str(many)]["lines"]
    assert len(lines) == inventory.MAX_LINES and len(lines[0][1]) == inventory.MAX_LINE


def test_claude_version_runs_only_when_claude_is_on_path_without_a_shell():
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout="2.1.5 (Claude Code)\x1b[0m\n", stderr="")

    assert inventory.claude_version(which=lambda n: None, run=run) is None and calls == []
    assert inventory.claude_version(which=lambda n: "/opt/claude", run=run) == "2.1.5 (Claude Code)[0m"
    cmd, kw = calls[0]
    assert cmd == ["/opt/claude", "--version"] and kw["timeout"] == 10 and not kw.get("shell")
    boom = lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("claude", 10))  # noqa: E731
    assert inventory.claude_version(which=lambda n: "/opt/claude", run=boom) is None
    fail = lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="oops", stderr="")  # noqa: E731
    assert inventory.claude_version(which=lambda n: "/opt/claude", run=fail) is None


def test_write_is_0600_atomic_json(tmp_path):
    inventory.write(tmp_path, {"a": 1})
    p = tmp_path / "inventory.json"
    assert json.loads(p.read_text()) == {"a": 1} and stat.S_IMODE(p.stat().st_mode) == 0o600


def test_load_map_validates(tmp_path):
    p = tmp_path / "m.json"
    good = {"targets": {"k": {"path": "p", "pattern": "x"}}, "docs": {"https://d.test/a": ["k"]}, "lead_keywords": ["a"]}
    p.write_text(json.dumps(good))
    assert inventory.load_map(p) == good
    p.write_text(json.dumps({"targets": {"k": {"path": "p", "pattern": "x"}}}))
    assert inventory.load_map(p)["docs"] == {} and inventory.load_map(p)["lead_keywords"] == []
    bad = [[], {}, {"targets": []}, {"targets": {"k": {"path": 1, "pattern": "x"}}}, {"targets": {"k k": {"path": "p", "pattern": "x"}}},
           {"targets": {"k": {"path": "p", "pattern": "("}}}, {**good, "docs": {"u": ["nope"]}}, {**good, "lead_keywords": [1]},
           {**good, "docs": []}]
    for b in bad:
        p.write_text(json.dumps(b))
        with pytest.raises(ValueError):
            inventory.load_map(p)
    p.write_text("{nope")
    with pytest.raises(ValueError):
        inventory.load_map(p)


def test_the_example_map_is_valid_and_has_the_five_generic_keys():
    m = inventory.load_map(ROOT / "docs" / "watch" / "inventory_map.example.json")
    assert sorted(m["targets"]) == ["ace.pyproject", "agents.model", "hooks.model_flags", "lineup.reference", "settings.model"]
    assert os.path.sep in m["targets"]["agents.model"]["path"]


# ------------------------------------------------------------------ wave 4: files per target are capped
def test_a_glob_that_matches_thousands_of_files_keeps_fifty_and_marks_the_target_truncated(tmp_path):
    d = tmp_path / "many"
    d.mkdir()
    for n in range(120):
        (d / f"f{n:03d}.json").write_text('{"model": "claude-old-1"}\n')
    inv = snap(mapping(tmp_path, k=("many/*", "claude-")))
    target = inv["targets"]["k"]
    assert len(target["files"]) == inventory.MAX_FILES == 50 and target["truncated"] is True
    assert sorted(target["files"]) == sorted(str(d / f"f{n:03d}.json") for n in range(50))
    assert len(json.dumps(inv)) < 20_000


def test_a_target_within_the_cap_is_not_marked_truncated(tmp_path):
    (tmp_path / "a.json").write_text("claude-x\n")
    assert "truncated" not in snap(mapping(tmp_path, k=("a.json", "claude-")))["targets"]["k"]
