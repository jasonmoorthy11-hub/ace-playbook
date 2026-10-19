"""Per-session rolling state (compile/state.py): hashed names, 0600, caps, atomic, LOCK_NB."""

import fcntl
import json
import os
import re
import stat

import pytest

from ace_playbook.compile import state as st

HOSTILE = ["../../etc/passwd", "a/b", "..", "/abs/path", "x\x00y", "ünï", "a" * 5000, "sid with spaces",
           "..\\..\\win", "~/x", "$(id)", "normal-session-id"]


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_session_hash_is_16_hex_and_stable():
    assert re.fullmatch(r"[0-9a-f]{16}", st.session_hash("abc")) and st.session_hash("abc") == st.session_hash("abc")
    assert st.session_hash("abc") != st.session_hash("abd")


@pytest.mark.parametrize("bad", ["", None, 5, b"x", ["a"]])
def test_session_id_must_be_a_non_empty_string(bad, tmp_path):
    with pytest.raises(st.StateError):
        st.state_path(tmp_path, bad)


@pytest.mark.parametrize("sid", HOSTILE)
def test_path_containment_for_hostile_session_ids(tmp_path, sid):
    p = st.state_path(tmp_path, sid)
    assert p.parent == (tmp_path / "sessions") and p.name == st.session_hash(sid) + ".json"
    state, status = st.update(tmp_path, sid, lambda s: {**s, "events": 1})
    assert status == "ok" and state["events"] == 1
    assert p.resolve().parent == (tmp_path / "sessions").resolve()
    assert sorted(x.name for x in tmp_path.iterdir()) == ["sessions"]
    assert all(".." not in x.name and "/" not in x.name for x in (tmp_path / "sessions").iterdir())


def test_symlinked_sessions_dir_is_refused(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (data / "sessions").symlink_to(target)
    with pytest.raises(st.StateError):
        st.state_path(data, "s1")
    state, status = st.update(data, "s1", lambda s: s)
    assert state is None and status == "skipped:error" and list(target.iterdir()) == []


def test_files_are_0600_and_dir_is_0700(tmp_path):
    st.update(tmp_path, "s1", lambda s: s)
    p = st.state_path(tmp_path, "s1")
    assert mode(p) == 0o600 and mode(str(p) + ".lock") == 0o600 and mode(p.parent) == 0o700


def test_missing_file_loads_a_fresh_state_with_the_documented_shape(tmp_path):
    s = st.load(tmp_path, "s1", now="2026-10-20T09:01:12Z")
    assert s == {"v": 1, "session_hash": st.session_hash("s1"), "created": "2026-10-20T09:01:12Z",
                 "seen_paths": [], "turn_paths": [], "git": {"commit": 0, "push": 0}, "events": 0,
                 "truncated": False, "pending": {}}


def test_save_then_load_roundtrip_and_sorted_keys(tmp_path):
    s = st.new_state("s1")
    s["seen_paths"] = ["0123456789abcdef"]
    s["git"]["commit"] = 2
    assert st.save(tmp_path, "s1", s) is True
    assert st.load(tmp_path, "s1") == s
    text = st.state_path(tmp_path, "s1").read_text()
    assert json.loads(text)["seen_paths"] == ["0123456789abcdef"] and "s1" not in text


def test_oversize_state_is_capped_on_save(tmp_path):
    s = st.new_state("s1")
    s["seen_paths"] = [format(i, "016x") for i in range(6000)]
    s["turn_paths"] = list(s["seen_paths"])
    assert st.save(tmp_path, "s1", s) is True
    p = st.state_path(tmp_path, "s1")
    assert p.stat().st_size <= st.MAX_BYTES
    back = st.load(tmp_path, "s1")
    assert back["truncated"] is True and len(back["seen_paths"]) <= st.MAX_PATHS


def test_oversize_file_on_disk_loads_as_truncated_without_reading_it(tmp_path):
    p = st.state_path(tmp_path, "s1")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"v": 1, "pad": "x" * (st.MAX_BYTES + 10)}))
    s = st.load(tmp_path, "s1")
    assert s["truncated"] is True and s["seen_paths"] == []


@pytest.mark.parametrize("content", ["not json", "[]", '{"v": 2}', '{"v": 1, "seen_paths": "oops"}',
                                     '{"v": 1, "seen_paths": [5]}', '{"v": 1, "git": {"commit": "many"}}', ""])
def test_corrupt_state_fails_toward_silence(tmp_path, content):
    p = st.state_path(tmp_path, "s1")
    p.parent.mkdir(parents=True)
    p.write_text(content)
    assert st.load(tmp_path, "s1")["truncated"] is True


def test_symlinked_state_file_is_not_followed(tmp_path):
    secret = tmp_path / "other.json"
    secret.write_text(json.dumps({**st.new_state("s1"), "seen_paths": ["0123456789abcdef"], "events": 99}))
    p = st.state_path(tmp_path, "s1")
    p.parent.mkdir(parents=True)
    p.symlink_to(secret)
    s = st.load(tmp_path, "s1")
    assert s["truncated"] is True and s["events"] == 0 and s["seen_paths"] == []


def test_write_is_atomic_when_the_replace_fails(tmp_path, monkeypatch):
    st.update(tmp_path, "s1", lambda s: {**s, "events": 7})
    p = st.state_path(tmp_path, "s1")
    before = p.read_text()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(st.os, "replace", boom)
    state, status = st.update(tmp_path, "s1", lambda s: {**s, "events": 8})
    assert state is None and status == "skipped:error"
    assert p.read_text() == before
    assert [x.name for x in p.parent.iterdir() if x.name.endswith(".tmp")] == []


def test_write_goes_through_a_temp_file_in_the_same_dir(tmp_path, monkeypatch):
    seen = []
    real = os.replace
    monkeypatch.setattr(st.os, "replace", lambda a, b: (seen.append((str(a), str(b))), real(a, b))[1])
    st.save(tmp_path, "s1", st.new_state("s1"))
    (src, dst), = seen
    assert os.path.dirname(src) == os.path.dirname(dst) and src != dst and src.endswith(".tmp")


def test_update_applies_the_function_under_the_lock(tmp_path):
    for i in range(3):
        state, status = st.update(tmp_path, "s1", lambda s: {**s, "events": s["events"] + 1})
        assert status == "ok"
    assert st.load(tmp_path, "s1")["events"] == 3


def hold_lock(tmp_path, sid):
    path = st.state_path(tmp_path, sid)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def test_lock_contention_skips_the_update_after_three_tries(tmp_path, monkeypatch):
    st.update(tmp_path, "s1", lambda s: {**s, "events": 1})
    sleeps = []
    monkeypatch.setattr(st.time, "sleep", lambda t: sleeps.append(t))
    fd = hold_lock(tmp_path, "s1")
    try:
        state, status = st.update(tmp_path, "s1", lambda s: {**s, "events": 99})
    finally:
        os.close(fd)
    assert (state, status) == (None, "skipped:locked")
    assert sleeps == [0.01, 0.01], "three tries, 10 ms apart"
    assert st.load(tmp_path, "s1")["events"] == 1


def test_lock_released_between_tries_lets_the_update_through(tmp_path, monkeypatch):
    fd = hold_lock(tmp_path, "s1")

    def release(_t):
        os.close(fd)

    monkeypatch.setattr(st.time, "sleep", release)
    state, status = st.update(tmp_path, "s1", lambda s: {**s, "events": 5})
    assert status == "ok" and state["events"] == 5


def test_a_blocked_update_returns_quickly_instead_of_hanging(tmp_path):
    import time
    fd = hold_lock(tmp_path, "s1")
    try:
        t0 = time.monotonic()
        _, status = st.update(tmp_path, "s1", lambda s: s)
        elapsed = time.monotonic() - t0
    finally:
        os.close(fd)
    assert status == "skipped:locked" and elapsed < 0.5


def test_lock_is_released_after_the_update(tmp_path):
    st.update(tmp_path, "s1", lambda s: s)
    fd = os.open(str(st.state_path(tmp_path, "s1")) + ".lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


def test_update_function_errors_release_the_lock_and_propagate(tmp_path):
    def bad(s):
        raise RuntimeError("bug in caller")

    with pytest.raises(RuntimeError):
        st.update(tmp_path, "s1", bad)
    assert st.update(tmp_path, "s1", lambda s: s)[1] == "ok"


def test_hook_path_modules_import_without_subprocess_sqlite_or_network():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    # pathlib pulls in urllib.parse (pure string code), so only the network parts of urllib are banned
    heavy = ("subprocess", "sqlite3", "socket", "ssl", "http", "http.client", "urllib.request",
             "ace_playbook.store",
             "ace_playbook.inject", "ace_playbook.scrub", "ace_playbook.keys")
    code = (f"import sys; sys.path.insert(0, {str(root)!r}); import ace_playbook.compile.state, "
            f"ace_playbook.compile.detectors, ace_playbook.compile.events; "
            f"print(','.join(m for m in {heavy!r} if m in sys.modules))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""


def test_state_module_imports_only_stdlib_and_no_store():
    import ast
    import ace_playbook.compile.state as mod
    tree = ast.parse(open(mod.__file__).read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(("." * node.level) + (node.module or ""))
    allowed = {"__future__", "fcntl", "hashlib", "json", "os", "re", "time", "pathlib", "typing", ".detectors"}
    assert names <= allowed, names - allowed
