"""Hook conformance: the real `hooks/shadow.sh` against hostile or broken input and a hostile environment.

Only what the other suites do not already pin. Already covered, and cited rather than repeated:
  - bad payloads in-process (empty, `not json`, `\\xff\\xfe`, arrays, 100000 `[`, missing fields) exit silently, no row:
    test_compile_shadow.py::test_unparseable_or_incomplete_payloads_exit_silently
  - over the cap logs `skipped:oversize`: test_compile_shadow.py::test_an_oversize_payload_is_skipped_and_logged
  - a cut (>64 KiB) command writes a `truncated` row: test_compile_shadow.py::test_a_truncated_command_fires_when_the_proposal_says_on_truncated_fire
  - `dur_ms` is an int from the call's start: test_compile_synth.py:182-192
  - a state lock held (in this process) is skipped within 0.5 s: test_compile_shadow.py::test_a_locked_state_file_is_skipped_but_a_stateless_detector_still_records
  - a broken config exits 0: test_compile_shadow.py::test_a_broken_config_exits_0_without_writing
  - ACE_PLAYBOOK_KEYED=1 with no key logs `chain_key:missing`: test_compile_keyed_marker.py:76-78 (in process, once) and :81-87 (wrapper, once run)
  - the wrapper never execs, ends in `exit 0`, survives a crashing module: test_hooks.py (shadow.sh section)
  - modules that must stay off the hook path: test_compile_cli.py::test_importing_the_hook_path_modules_loads_no_sqlite_store_scrub_cleaner_inject_or_network
What this file adds: the same failures through the wrapper in a child process with a whole-tree "nothing else was written" check,
the exact stdin cap boundary, the exact 64 KiB command with `dur_ms`, a lock held by a separate process, a missing / read-only
data dir, the keyed-without-key note across three processes, and an allow-list of every module the hook path loads (at import, and
after one firing evaluation has run the approved-proposal path, the catalogue and the row write).
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ace_playbook.compile import shadow
from ace_playbook.compile import state as st
from ace_playbook.compile.events import MAX_CMD

from . import compile_support as cs

ROOT = Path(__file__).resolve().parent.parent
RM = "rm -rf /w/src"
FAST = 10.0   # seconds; the harness gives the hook 5 (events.py note), CI machines are slower than that budget's target


@pytest.fixture
def w(tmp_path):
    return cs.real_world(tmp_path)


def env_of(w, **extra):
    home = Path(w.conf).parent / "home"                          # the child's HOME: its `~` never reaches the operator's key dir
    home.mkdir(exist_ok=True)
    return {**os.environ, "ACE_PLAYBOOK_CONFIG": w.conf, "ACE_PLAYBOOK_HOME": str(ROOT), "ACE_PYTHON": sys.executable,
            "HOME": str(home), **extra}


def hook(w, raw, arg="pre", **extra):
    """Run hooks/shadow.sh in a child process with `raw` bytes on stdin; returns (CompletedProcess, seconds)."""
    t0 = time.perf_counter()
    p = subprocess.run([str(ROOT / "hooks" / "shadow.sh"), arg], input=raw, capture_output=True, env=env_of(w, **extra), timeout=60)
    return p, time.perf_counter() - t0


def changed(before: dict, after: dict) -> set:
    return {k for k, v in after.items() if before.get(k) != v}


def padded(command: str, total: int) -> bytes:
    """A valid payload of exactly `total` bytes: the command is untouched, an ignored field takes the padding."""
    base = {**cs.pre_payload(command), "pad": ""}
    n = total - len(json.dumps(base).encode())
    assert n >= 0
    return json.dumps({**base, "pad": "a" * n}).encode()


# ------------------------------------------------------------------ hostile stdin through the wrapper
BAD = {
    "malformed-json": b'{"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "rm -rf /w/src"',
    "not-json": b"}{ garbage",
    "empty": b"",
    "only-whitespace": b" \n\t ",
    "invalid-utf8": b"\xff\xfe\x00\xc3\x28",
    "invalid-utf8-inside-valid-json": b'{"session_id":"s","tool_name":"Bash","tool_input":{"command":"rm -rf /w/src \xff\xfe"}}',
    "nul-bytes": b"\x00" * 1000,
    "deeply-nested": b"[" * 200000,
}


@pytest.mark.parametrize("raw", BAD.values(), ids=BAD.keys())
def test_bad_stdin_exits_0_silently_and_writes_nothing_but_at_most_a_log_line(w, raw):
    before = cs.tree_state(w.data)
    p, secs = hook(w, raw)
    assert (p.returncode, p.stdout, p.stderr) == (0, b"", b"") and secs < FAST
    assert cs.shadow_rows(w.data) == []
    assert changed(before, cs.tree_state(w.data)) <= {"shadow.log"}
    log = w.data / "shadow.log"
    assert not log.exists() or all(len(line.split()) == 2 for line in log.read_text().splitlines())   # "<ts> <type-only note>"


def test_stdin_at_the_cap_is_evaluated_and_one_byte_over_is_skipped(w):
    at_cap, over = padded(RM, shadow.MAX_STDIN), padded(RM, shadow.MAX_STDIN + 1)
    assert (len(at_cap), len(over)) == (shadow.MAX_STDIN, shadow.MAX_STDIN + 1)
    p, _ = hook(w, at_cap)
    assert (p.returncode, p.stdout) == (0, b"") and len(cs.shadow_rows(w.data)) == 1
    before = cs.tree_state(w.data)
    p, secs = hook(w, over)
    assert (p.returncode, p.stdout, p.stderr) == (0, b"", b"") and secs < FAST
    assert len(cs.shadow_rows(w.data)) == 1 and "skipped:oversize" in (w.data / "shadow.log").read_text()
    assert changed(before, cs.tree_state(w.data)) <= {"shadow.log"}


@pytest.mark.parametrize("length,cause", [(MAX_CMD, "detector"), (MAX_CMD + 1, "detector")])
def test_a_64kib_command_exits_0_and_the_row_records_dur_ms(w, length, cause):
    cmd = RM + " # " + "a" * (length - len(RM) - 3)
    assert len(cmd) == length
    p, secs = hook(w, json.dumps(cs.pre_payload(cmd)).encode())
    (row,) = cs.shadow_rows(w.data)
    assert (p.returncode, p.stdout, p.stderr) == (0, b"", b"") and secs < FAST
    assert row["cause"] == cause and type(row["dur_ms"]) is int and 0 <= row["dur_ms"] <= secs * 1000 + 50
    assert "rm -rf" not in (w.data / "shadow.jsonl").read_text()


def test_a_64kib_command_that_is_cut_is_marked_and_one_that_fits_is_not(w):
    hook(w, json.dumps(cs.pre_payload("echo " + "a" * (MAX_CMD - 5))).encode())      # exactly MAX_CMD: nothing cut
    assert cs.shadow_rows(w.data) == []
    hook(w, json.dumps(cs.pre_payload("echo " + "a" * (MAX_CMD - 4))).encode())      # one over: cut, so `unparseable`
    assert [r["cause"] for r in cs.shadow_rows(w.data)] == ["truncated"]


# ------------------------------------------------------------------ hostile environment
def test_a_state_lock_held_by_another_process_is_skipped_quickly_and_the_row_is_still_written(w):
    lock = Path(str(st.state_path(w.data, "sess-other")) + ".lock")
    lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    holder = subprocess.Popen([sys.executable, "-I", "-c",
                               "import fcntl,os,sys; fd=os.open(sys.argv[1], os.O_CREAT|os.O_RDWR, 0o600); "
                               "fcntl.flock(fd, fcntl.LOCK_EX); print('held', flush=True); sys.stdin.read()", str(lock)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        p, secs = hook(w, json.dumps(cs.pre_payload(RM, sid="sess-other")).encode())
    finally:
        holder.stdin.close()
        holder.wait(timeout=10)
    assert (p.returncode, p.stdout, p.stderr) == (0, b"", b"") and secs < FAST
    assert len(cs.shadow_rows(w.data)) == 1 and "skipped:locked" in (w.data / "shadow.log").read_text()
    assert not st.state_path(w.data, "sess-other").exists()


def test_a_missing_data_dir_exits_0_and_is_not_created(tmp_path):
    conf = tmp_path / "config.json"
    (tmp_path / "home").mkdir()
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "no" / "such" / "dir"), "chain_key_path": str(tmp_path / "k")}))
    p = subprocess.run([str(ROOT / "hooks" / "shadow.sh"), "pre"], input=json.dumps(cs.pre_payload(RM)).encode(), capture_output=True,
                       env={**os.environ, "ACE_PLAYBOOK_CONFIG": str(conf), "ACE_PLAYBOOK_HOME": str(ROOT), "ACE_PYTHON": sys.executable,
                            "HOME": str(tmp_path / "home")},
                       timeout=60)
    assert (p.returncode, p.stdout, p.stderr) == (0, b"", b"")
    assert not (tmp_path / "no").exists()


def test_a_data_dir_that_is_a_file_exits_0(tmp_path):
    (tmp_path / "datafile").write_text("x")
    (tmp_path / "home").mkdir()
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "datafile"), "chain_key_path": str(tmp_path / "k")}))
    p = subprocess.run([str(ROOT / "hooks" / "shadow.sh"), "pre"], input=json.dumps(cs.pre_payload(RM)).encode(), capture_output=True,
                       env={**os.environ, "ACE_PLAYBOOK_CONFIG": str(conf), "ACE_PLAYBOOK_HOME": str(ROOT), "ACE_PYTHON": sys.executable,
                            "HOME": str(tmp_path / "home")},
                       timeout=60)
    assert (p.returncode, p.stdout, p.stderr) == (0, b"", b"") and (tmp_path / "datafile").read_text() == "x"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
def test_a_read_only_data_dir_exits_0_with_no_output_and_writes_nothing(w):
    before = cs.tree_state(w.data)
    os.chmod(w.data, 0o500)
    try:
        p, _ = hook(w, json.dumps(cs.pre_payload(RM)).encode())
    finally:
        os.chmod(w.data, 0o700)
    assert (p.returncode, p.stdout, p.stderr) == (0, b"", b"")
    assert cs.tree_state(w.data) == before


def test_keyed_flag_without_a_key_logs_chain_key_missing_once_across_processes_and_exits_0(w):
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "warn", row=True)         # a v1 row the keyed install cannot verify
    for _ in range(3):
        p, _ = hook(w, json.dumps(cs.pre_payload(RM)).encode(), ACE_PLAYBOOK_KEYED="1")
        assert (p.returncode, p.stdout, p.stderr) == (0, b"", b"")
    assert (w.data / "shadow.log").read_text().count("chain_key:missing") == 1
    assert [r["mode"] for r in cs.shadow_rows(w.data)] == ["observe"] * 3


# ------------------------------------------------------------------ the import allow-list of the hook path
ALLOWED_ACE = {"ace_playbook", "ace_playbook.config", "ace_playbook.canon", "ace_playbook.textrules", "ace_playbook.atomic",
               "ace_playbook.compile", *("ace_playbook.compile." + m for m in (
                   "shadow", "state", "evidence", "proposal", "modes", "demotions", "approvals", "chains", "catalogue",
                   "detectors", "events", "shell", "srchash"))}
ABSENT = ("seal", "dashboard", "dashboard_html", "status", "digest", "queue", "report", "synth", "metrics", "scrub", "measure",
          "watch", "fetch", "inventory", "match", "store", "summary")             # `watch` is the one network-using package (W1)
HEAVY = {"sqlite3", "subprocess", "socket", "ssl", "http", "urllib.request", "asyncio", "multiprocessing", "threading", "ctypes"}


# NOTE: `-S` drops the site / .pth imports that the real hook (`-I -P`, hooks/shadow.sh) does load, so this measures the package's own
# imports, not the interpreter's start-up. HEAVY names stdlib modules that this Python version's stdlib happens not to pull in on
# this path (3.12 imports `urllib.parse` via pathlib, hence only `urllib.request` is listed); another version may need the list revised.
def child_modules(w, run: bool) -> list[str]:
    """Module names loaded by a child interpreter: right after `import main`, or after `shadow.main(["pre"])` ran a FIRING payload."""
    head = f"import sys; sys.path.insert(0, {str(ROOT)!r}); from ace_playbook.compile import shadow; "
    if not run:
        code, flags = head + "pass", ["-X", "importtime"]
    else:
        code, flags = head + "rc = shadow.main(['pre']); print('\\n'.join(sorted(sys.modules))); sys.exit(rc)", []
    p = subprocess.run([sys.executable, "-I", "-S", *flags, "-c", code], capture_output=True, text=True, timeout=60,
                       input=json.dumps(cs.pre_payload(RM)), env=env_of(w))
    assert p.returncode == 0, p.stderr
    if run:
        assert len(cs.shadow_rows(w.data)) == 1                       # the approved proposal loaded, the detector fired, the row was written
        return p.stdout.split()
    rows = [ln.split("|", 2)[2].strip() for ln in p.stderr.splitlines() if ln.startswith("import time:") and "|" in ln]
    return [r for r in rows if r != "imported package"]


WHEN = pytest.mark.parametrize("run", [False, True], ids=["at-import", "after-one-firing-evaluation"])


@WHEN
def test_the_hook_path_imports_only_stdlib_and_the_documented_modules(w, run):
    mods = child_modules(w, run)
    assert "ace_playbook.compile.shadow" in mods
    ours = {m for m in mods if m.split(".")[0] == "ace_playbook"}
    assert ours <= ALLOWED_ACE, sorted(ours - ALLOWED_ACE)
    known = set(sys.stdlib_module_names) | set(sys.builtin_module_names)
    foreign = {m for m in mods if m.split(".")[0] not in known and not m.startswith("_frozen") and m not in ours | {"__main__"}}
    assert foreign == set(), sorted(foreign)
    assert not {m for m in mods if any(m == h or m.startswith(h + ".") for h in HEAVY)}   # urllib.parse (pathlib) is fine


@WHEN
@pytest.mark.parametrize("name", ABSENT)
def test_the_hook_path_never_loads_seal_dashboard_report_synth_metrics_or_scrub(w, run, name):
    assert [m for m in child_modules(w, run) if name in m.split(".")] == []
