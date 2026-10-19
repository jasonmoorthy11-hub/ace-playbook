"""The keyed-install marker (key file, heads file or ACE_PLAYBOOK_KEYED=1), the confirmed `keygen --rehead`, the never-keyed-only `keygen` with its backups
and the temp files of the keyed chains."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ace_playbook.canon import chain_key
from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import approvals, chains
from ace_playbook.compile import demotions as dm
from ace_playbook.compile.chains import Keyring

from . import compile_support as cs
from .test_compile_cli import run as cli_run, tty, world, written  # noqa: F401  (fixtures)
from .test_compile_keyed import K1, K2, arow, forge, write_key
from .test_compile_keyed_heads import add, drop_last_line, keyed, promote, run, w  # noqa: F401  (fixtures and helpers)
from .test_compile_shadow import RM

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ the marker
@pytest.mark.parametrize("what", ["none", "junk_key", "dangling_symlink", "directory", "heads_only", "env_flag"])
def test_key_of_is_none_only_for_an_unkeyed_install(tmp_path, monkeypatch, what):
    key = tmp_path / "chain_key"
    if what == "junk_key":
        key.write_text("junk")
    elif what == "dangling_symlink":
        key.symlink_to(tmp_path / "nowhere")
    elif what == "directory":
        key.mkdir()
    elif what == "heads_only":
        (tmp_path / chains.HEADS).write_text("{}")
    elif what == "env_flag":
        monkeypatch.setenv("ACE_PLAYBOOK_KEYED", "1")
    ring = chains.key_of(SimpleCfg(key))
    assert (ring is None) == (what == "none") and (ring is None or ring.key is None)


class SimpleCfg:
    def __init__(self, key):
        self.chain_key_path, self.data_dir = key, key.parent


@pytest.mark.parametrize("value", ["0", "yes", "true", ""])
def test_only_the_value_1_of_the_flag_marks_the_install(tmp_path, monkeypatch, value):
    monkeypatch.setenv("ACE_PLAYBOOK_KEYED", value)
    assert chains.key_of(SimpleCfg(tmp_path / "chain_key")) is None


def test_key_deleted_heads_kept_and_the_chain_rewritten_as_v1_runs_observe_and_logs_chain_key_missing_once_and_keygen_refuses(w, run, tty, capsys, monkeypatch):
    ring = keyed(w)
    p = promote(w, "observe", ring)
    path = cs.approve_file(w.cfg, p, "block")
    forge(w.data, arow, approvals.FILE, [(1, None)], mode="block", proposal_id=p["id"],
          proposal_sha256=json.loads(path.read_text())["proposal_sha256"])
    w.cfg.chain_key_path.unlink()                                               # the agent moved the key away; the heads file stays
    assert run(cs.pre_payload(RM)) == (0, "", "") and run(cs.pre_payload(RM)) == (0, "", "")
    assert [r["mode"] for r in run.rows] == ["observe", "observe"] and run.log.count("chain_key:missing") == 1
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    forged = (w.data / approvals.FILE).read_bytes()                              # keygen must not seal the forgery under a fresh key
    assert cli.main(["--config", w.conf, "keygen"]) == 1 and "restore the chain key file" in capsys.readouterr().err
    assert not w.cfg.chain_key_path.exists() and (w.data / approvals.FILE).read_bytes() == forged and tty.asked == []


def test_the_env_flag_with_neither_file_breaks_a_v1_chain_and_is_logged(w, run, monkeypatch):
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "warn", row=True)       # a v1 row: fine on an unkeyed install
    assert run(cs.pre_payload(RM))[1] != "" and run.rows[0]["mode"] == "warn"
    monkeypatch.setenv("ACE_PLAYBOOK_KEYED", "1")
    assert run(cs.pre_payload(RM)) == (0, "", "") and run(cs.pre_payload(RM)) == (0, "", "")
    assert run.rows[-1]["mode"] == "observe" and run.log.count("chain_key:missing") == 1 and "approvals:chain_broken" in run.log


def test_the_wrapper_script_passes_the_flag_through_to_python(w):
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "warn", row=True)
    env = {**os.environ, "ACE_PLAYBOOK_CONFIG": w.conf, "ACE_PYTHON": sys.executable, "ACE_PLAYBOOK_KEYED": "1"}
    p = subprocess.run(["bash", str(ROOT / "hooks" / "shadow.sh"), "pre"], input=json.dumps(cs.pre_payload(RM)), capture_output=True,
                       text=True, env=env, timeout=60)
    assert (p.returncode, p.stdout) == (0, "") and cs.shadow_rows(w.data)[0]["mode"] == "observe"
    assert "chain_key:missing" in (w.data / "shadow.log").read_text()


def test_a_key_file_with_the_wrong_mode_and_an_empty_chain_makes_approve_refuse_instead_of_writing_a_v1_row(world, written, tty, capsys):
    write_key(world.cfg.chain_key_path, mode=0o644)
    code, _, err = cli_run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 1 and err.count("\n") == 1 and "chain key missing" in err and "restore the chain key file" in err
    assert not (world.cfg.data_dir / "approvals.jsonl").exists() and tty.asked == []


def test_the_env_flag_makes_every_writer_refuse_on_an_install_with_no_files(world, written, tty, capsys, monkeypatch):
    monkeypatch.setenv("ACE_PLAYBOOK_KEYED", "1")
    assert cli_run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)[0] == 1
    assert not (world.cfg.data_dir / "approvals.jsonl").exists()
    monkeypatch.delenv("ACE_PLAYBOOK_KEYED")
    assert cli_run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)[0] == 0      # unkeyed: the v1 rule, unchanged


# ------------------------------------------------------------------ keygen --rehead needs a confirmation
def revoked_chain(w):
    ring = keyed(w)
    p = promote(w, "warn", ring)
    approvals.append(w.data, {"proposal_id": p["id"], "proposal_sha256": "-", "mode": "revoked"}, ring)
    return ring


def test_rehead_lists_a_cut_revoked_row_as_old_head_not_found_and_refuses_on_n_and_writes_on_y(w, tty, capsys):
    ring = revoked_chain(w)
    before = ring.heads()
    drop_last_line(w.data / approvals.FILE)                                     # the agent cut the revocation
    tty.answer = "n"
    assert cli.main(["--config", w.conf, "keygen", "--rehead"]) == 1
    out, err = capsys.readouterr()
    assert "approvals: old head not found: rows were removed or replaced after row 1" in out and "heads not written" in err
    assert ring.heads() == before and len(tty.asked) == 1 and "Inspect the listed rows" in tty.asked[0]
    tty.answer = "y"
    assert cli.main(["--config", w.conf, "keygen", "--rehead"]) == 0 and ring.heads() != before
    assert approvals.read_chain(w.data, ring)[1] is None


def test_rehead_lists_the_rows_added_after_the_old_head_when_an_append_was_interrupted(w, tty, capsys):
    ring = keyed(w)
    add(approvals, w.data, ring, 1)
    old = ring.heads_path.read_bytes()
    add(approvals, w.data, ring, 2)
    ring.heads_path.write_bytes(old)                                            # a crash between the row and its head
    assert cli.main(["--config", w.conf, "keygen", "--rehead"]) == 0
    out = capsys.readouterr().out
    assert "approvals: rows added after the old head:" in out and "row 2  " in out and "row 1  " not in out
    assert approvals.read_chain(w.data, ring)[1] is None


def test_rehead_with_the_heads_file_gone_says_so_and_still_asks(w, tty, capsys):
    ring = keyed(w)
    add(approvals, w.data, ring, 1)
    ring.heads_path.unlink()
    tty.answer = "n"
    assert cli.main(["--config", w.conf, "keygen", "--rehead"]) == 1
    assert "the old heads file is missing or invalid" in capsys.readouterr().out and len(tty.asked) == 1 and not ring.heads_path.exists()


def test_rehead_has_no_yes_option(w, tty):
    with pytest.raises(SystemExit):
        cli.main(["--config", w.conf, "keygen", "--rehead", "--yes"])


# ------------------------------------------------------------------ keygen: never-keyed installs only, with backups
def v1_chains(w):
    for i in (1, 2):
        add(approvals, w.data, None, i)
    add(dm, w.data, None, 1)
    return {m.FILE: (w.data / m.FILE).read_bytes() for m in (approvals, dm)}


def fail_replace_on(monkeypatch, n):
    real, calls = os.replace, []

    def replace(a, b):
        calls.append(a)
        if len(calls) == n:
            raise OSError("simulated crash")
        return real(a, b)
    monkeypatch.setattr(os, "replace", replace)
    return lambda: monkeypatch.setattr(os, "replace", real)


def test_keygen_on_a_never_keyed_install_lists_the_rows_asks_then_seals_and_keeps_0600_backups_in_the_key_dir(w, tty, capsys):
    original = v1_chains(w)
    tty.answer = "n"
    assert cli.main(["--config", w.conf, "keygen"]) == 1
    out = capsys.readouterr().out
    assert "approvals: 2 row(s) to re-seal" in out and "demotions: 1 row(s) to re-seal" in out and "row 2  " in out and "P-00000002" in out
    assert not w.cfg.chain_key_path.exists() and not list(w.cfg.chain_key_path.parent.glob("*.bak"))
    tty.answer = "y"
    assert cli.main(["--config", w.conf, "keygen"]) == 0
    ring = chains.key_of(w.cfg)
    assert [r["v"] for r in approvals.read_chain(w.data, ring)[0] + dm.read_chain(w.data, ring)[0]] == [2, 2, 2] and ring.heads() is not None
    for m in (approvals, dm):
        bak = w.cfg.chain_key_path.parent / (m.FILE + ".bak")
        assert bak.read_bytes() == original[m.FILE] and stat.S_IMODE(os.stat(bak).st_mode) == 0o600
    assert cli.main(["--config", w.conf, "keygen"]) == 1                        # keyed now: never again


def test_keygen_refuses_when_the_heads_were_deleted_after_a_v1_rewrite(w, tty, capsys):
    ring = keyed(w)
    p = promote(w, "observe", ring)
    path = cs.approve_file(w.cfg, p, "block")
    forge(w.data, arow, approvals.FILE, [(1, None)], mode="block", proposal_id=p["id"], proposal_sha256=json.loads(path.read_text())["proposal_sha256"])
    ring.heads_path.unlink()
    forged, key = (w.data / approvals.FILE).read_bytes(), w.cfg.chain_key_path.read_bytes()
    assert cli.main(["--config", w.conf, "keygen"]) == 1 and "chain_heads.json is missing" in capsys.readouterr().err
    assert (w.data / approvals.FILE).read_bytes() == forged and w.cfg.chain_key_path.read_bytes() == key and not ring.heads_path.exists()


def test_keygen_refuses_after_the_heads_were_deleted_and_a_revoked_row_cut_and_rehead_says_the_old_heads_are_gone(w, tty, capsys):
    ring = revoked_chain(w)
    drop_last_line(w.data / approvals.FILE)
    ring.heads_path.unlink()
    assert cli.main(["--config", w.conf, "keygen"]) == 1 and "chain_heads.json is missing" in capsys.readouterr().err
    tty.answer = "n"
    assert cli.main(["--config", w.conf, "keygen", "--rehead"]) == 1
    assert "the old heads file is missing or invalid" in capsys.readouterr().out and not ring.heads_path.exists()


def test_keygen_refuses_a_key_file_with_the_wrong_mode_and_the_env_flag_alone_without_writing(w, tty, capsys, monkeypatch):
    write_key(w.cfg.chain_key_path, K2, mode=0o644)
    assert cli.main(["--config", w.conf, "keygen"]) == 1
    assert "restore the chain key file" in capsys.readouterr().err and w.cfg.chain_key_path.read_text() == K2.hex()
    w.cfg.chain_key_path.unlink()
    monkeypatch.setenv("ACE_PLAYBOOK_KEYED", "1")
    assert cli.main(["--config", w.conf, "keygen"]) == 1 and "unset ACE_PLAYBOOK_KEYED in this shell" in capsys.readouterr().err
    assert not w.cfg.chain_key_path.exists()


def test_following_the_restore_message_for_an_unusable_key_file_lets_keygen_succeed(w, tty, capsys, monkeypatch):
    write_key(w.cfg.chain_key_path, mode=0o644)
    add(approvals, w.data, None, 1)
    assert cli.main(["--config", w.conf, "keygen"]) == 1
    assert "move the unusable key file away" in capsys.readouterr().err
    w.cfg.chain_key_path.rename(w.cfg.chain_key_path.with_name("old_key"))      # the steps of the message
    (w.data / approvals.FILE).unlink()
    monkeypatch.delenv("ACE_PLAYBOOK_KEYED", raising=False)
    assert cli.main(["--config", w.conf, "keygen"]) == 0 and chain_key(w.cfg.chain_key_path) is not None


def test_the_repair_message_depends_on_whether_both_chains_still_verify_under_the_key(w, tty, capsys):
    ring = keyed(w)
    add(approvals, w.data, ring, 1)
    add(dm, w.data, ring, 1)
    ring.heads_path.unlink()                                                    # only the heads are gone: re-head, do not roll back
    assert cli.main(["--config", w.conf, "keygen"]) == 1
    err = capsys.readouterr().err
    assert "keygen --rehead (it lists what changed and asks)" in err and ".bak" not in err
    (w.data / dm.FILE).unlink()
    add(dm, w.data, None, 1)                                                    # a chain still at v1 ...
    bak = w.cfg.chain_key_path.parent / "demotions.jsonl.bak"
    bak.write_bytes((w.data / dm.FILE).read_bytes())                            # ... byte-equal to the backup keygen left: it was killed between the replaces
    assert cli.main(["--config", w.conf, "keygen"]) == 1
    err = capsys.readouterr().err
    assert "killed between the replaces" in err and "interrupted keygen" in err and "--rehead" not in err.split("interrupted")[0]
    bak.write_bytes(bak.read_bytes() + b"x")                                    # not the backup (altered after keygen): the other repair
    assert cli.main(["--config", w.conf, "keygen"]) == 1
    err = capsys.readouterr().err
    assert "altered after keygen" in err and "interrupted keygen" not in err and "restore the key file" in err
    assert not ring.heads_path.exists() and tty.asked == []


def test_an_oversize_chain_is_reported_as_a_broken_chain_by_keygen_not_a_bare_error(w, tty, capsys):
    ring = keyed(w)
    ring.heads_path.unlink()
    (w.data / approvals.FILE).write_bytes(b"x" * (approvals.MAX_BYTES + 1))
    assert cli.main(["--config", w.conf, "keygen"]) == 1
    err = capsys.readouterr().err
    assert "approvals.jsonl chain broken" in err and "over 1 MiB" in err and tty.asked == []


def test_a_chain_file_rewritten_while_the_prompt_is_open_does_not_change_what_is_sealed(w, tty, capsys, monkeypatch):
    original = v1_chains(w)
    path = w.data / approvals.FILE

    def ask(prompt):                                                            # the agent swaps in a forged block row during the prompt
        forge(w.data, arow, approvals.FILE, [(1, None)], mode="block", proposal_id="P-00000099")
        return "y"
    monkeypatch.setattr(cli, "_ask", ask)
    assert cli.main(["--config", w.conf, "keygen"]) == 0
    ring = chains.key_of(w.cfg)
    rows, broken = approvals.read_chain(w.data, ring)
    assert broken is None and [r["proposal_id"] for r in rows] == ["P-00000001", "P-00000002"] and {r["mode"] for r in rows} == {"observe"}
    assert (w.cfg.chain_key_path.parent / "approvals.jsonl.bak").read_bytes() == original[approvals.FILE] != path.read_bytes()


@pytest.mark.parametrize("nth", [1, 2])
def test_an_interrupted_keygen_restores_the_chains_and_leaves_no_key_backup_or_temp_file(w, tty, capsys, monkeypatch, nth):
    original = v1_chains(w)
    restore = fail_replace_on(monkeypatch, nth)
    assert cli.main(["--config", w.conf, "keygen"]) == 2
    restore()
    assert {m.FILE: (w.data / m.FILE).read_bytes() for m in (approvals, dm)} == original
    assert not w.cfg.chain_key_path.exists() and not (w.cfg.chain_key_path.parent / chains.HEADS).exists()
    assert not [p for d in (w.data, w.cfg.chain_key_path.parent) for p in d.iterdir() if p.name.startswith(".") or p.name.endswith((".bak", ".tmp"))]
    capsys.readouterr()
    assert cli.main(["--config", w.conf, "keygen"]) == 0                        # nothing was left behind: it can simply run again


# ------------------------------------------------------------------ temp files and signatures
def test_keygen_and_head_updates_never_write_through_a_predictable_or_hardlinked_temp_name(w, tty, capsys):
    victim = w.data.parent / "victim"
    victim.write_text("precious")
    try:
        for name in ("approvals.jsonl.tmp", ".approvals.jsonl.tmp", "demotions.jsonl.tmp"):
            os.link(victim, w.data / name)
    except OSError:
        pytest.skip("hard links are not available here")
    try:
        os.link(victim, w.cfg.chain_key_path.parent / ".chain_heads.json.tmp")
    except OSError:
        pytest.skip("hard links are not available here")
    add(approvals, w.data, None, 1)
    assert cli.main(["--config", w.conf, "keygen"]) == 0
    ring = chains.key_of(w.cfg)
    add(approvals, w.data, ring, 2)
    assert victim.read_text() == "precious"
    left = sorted(p.name for p in list(w.data.iterdir()) + list(w.cfg.chain_key_path.parent.iterdir()) if p.name.startswith("."))
    assert all(n.endswith(".tmp") for n in left)                                # only the planted links: our temp files are gone


def test_demotions_append_takes_the_keyring_by_keyword_only(tmp_path):
    fields = {"proposal_id": "P-00000001", "from_mode": "block", "to_mode": "warn", "cause": "label_fp", "ref": "0123abcd", "review_required": False}
    with pytest.raises(TypeError):
        dm.append(tmp_path, fields, None, None, Keyring(K1))
    assert dm.append(tmp_path, fields, ring=None)["v"] == 1
