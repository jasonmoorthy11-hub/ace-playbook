"""No ambient key: `config.load` never fills in a chain key the config file did not name (docs/design/compiler.md, "Keyed chains (as
built)"; hooks/INSTALL.md section 7). A keyed-marked install whose config names no key is `chain_key:missing`, never a silent v1; `compile
keygen` on such a config names the key beside the config file, asks, and writes the line itself."""

import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.canon import chain_key
from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import approvals, chains, chains_cli, chains_io
from ace_playbook.compile import demotions as dm

from . import compile_support as cs
from .test_compile_cli import run as cli_run, tty, world, written  # noqa: F401  (fixtures)
from .test_compile_keyed import K1, write_key
from .test_compile_keyed_heads import demote, keyed, promote, w, run  # noqa: F401  (fixtures and helpers)
from .test_compile_shadow import RM


def keyless(path, **extra):
    """Rewrite the config file at `path` without a `chain_key_path` line."""
    raw = {k: v for k, v in json.loads(Path(path).read_text()).items() if k != "chain_key_path"}
    Path(path).write_text(json.dumps({**raw, **extra}))
    return cfgmod.load(path)


# ------------------------------------------------------------------ config
def test_there_is_no_default_chain_key_to_read():
    assert not hasattr(cfgmod, "DEFAULT_CHAIN_KEY") and cfgmod.Config().chain_key_path is None


def test_a_config_file_without_chain_key_path_has_none_and_the_field_still_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cfgmod.from_dict({}).chain_key_path is None
    conf = tmp_path / "c.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "data")}))
    assert cfgmod.load(conf).chain_key_path is None and cfgmod.load(conf).source == conf
    assert cfgmod.from_dict({"chain_key_path": "~/k"}).chain_key_path == tmp_path / "k"
    assert cfgmod.from_dict({"chain_key_path": None}).chain_key_path is None


def test_a_missing_default_config_file_names_no_key_either(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(cfgmod.ENV_CONFIG)
    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG", tmp_path / ".config" / "ace-playbook" / "config.json")
    cfg = cfgmod.load()
    assert cfg.chain_key_path is None and cfg.source == tmp_path / ".config" / "ace-playbook" / "config.json"


def test_the_source_is_not_a_config_key(tmp_path):
    with pytest.raises(ValueError, match="unknown config keys"):
        cfgmod.from_dict({"source": str(tmp_path)})


# ------------------------------------------------------------------ a key the config does not name
def test_no_key_field_and_no_marker_is_a_never_keyed_install_with_the_v1_rule(w, run):
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "warn", row=True)             # a v1 row
    cfg = keyless(w.conf)
    assert cfg.chain_key_path is None and chains.key_of(cfg) is None
    assert approvals.read_chain(w.data, None)[1] is None
    assert run(cs.pre_payload(RM))[1] != "" and run.rows[0]["mode"] == "warn" and "chain_key:missing" not in run.log


@pytest.mark.parametrize("marker", ["env", "heads_file"])
def test_a_keyed_marked_install_with_no_key_field_is_chain_key_missing_not_a_silent_v1(w, run, monkeypatch, marker):
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "warn", row=True)             # a v1 row: valid only on an unkeyed install
    cfg = keyless(w.conf)
    if marker == "env":
        monkeypatch.setenv(chains.ENV, "1")
    else:
        (Path(w.conf).parent / chains.HEADS).write_text("{}")                          # beside the config file; the key is not named
    ring = chains.key_of(cfg)
    assert ring is not None and ring.key is None
    assert approvals.read_chain(w.data, ring)[1] == 1 and dm.read_chain(w.data, ring)[1] == "key_missing"      # both chains broken, even the empty one
    assert run(cs.pre_payload(RM)) == (0, "", "") and run(cs.pre_payload(RM)) == (0, "", "")
    assert run.rows[-1]["mode"] == "observe" and run.log.count("chain_key:missing") == 1 and "approvals:chain_broken" in run.log


def test_a_key_file_beside_the_config_that_does_not_name_it_is_not_read(w, run):
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "warn", row=True)
    key = Path(w.conf).parent / "chain_key"
    key.write_text(bytes(range(32)).hex())
    key.chmod(0o600)
    cfg = keyless(w.conf)
    assert chains.key_of(cfg) is None and cfg.chain_key_path is None                   # ambient authority: gone
    assert run(cs.pre_payload(RM))[1] != "" and run.rows[0]["mode"] == "warn"


def test_every_writer_refuses_on_a_keyed_marked_install_whose_config_names_no_key(world, written, tty, capsys, monkeypatch):
    keyless(world.conf)
    monkeypatch.setenv(chains.ENV, "1")
    code, _, err = cli_run(world, "approve", written.stem, "--mode", "observe", capsys=capsys)
    assert code == 1 and "chain key missing" in err and tty.asked == []
    assert not (world.cfg.data_dir / approvals.FILE).exists()


# ------------------------------------------------------------------ keygen names the key
def conf_at(tmp_path, raw=None, name="config.json"):
    conf = tmp_path / "cfgdir" / name
    if raw is not None:
        conf.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        conf.write_text(json.dumps(raw, indent=2) + "\n")
    return conf


def test_keygen_on_a_config_without_the_field_writes_the_field_and_the_key(tmp_path, tty, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    conf = conf_at(tmp_path, {"data_dir": str(tmp_path / "data"), "model": "claude-haiku-5-5", "deny_terms": ["a", "b"]})
    key = conf.parent / "chain_key"
    assert cli.main(["--config", str(conf), "keygen"]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[-1] == str(key) and len(chain_key(key)) == 32 and len(tty.asked) == 1 and "~/cfgdir/chain_key" in tty.asked[0]
    text = conf.read_text()
    assert json.loads(text) == {"data_dir": str(tmp_path / "data"), "model": "claude-haiku-5-5", "deny_terms": ["a", "b"],
                                "chain_key_path": "~/cfgdir/chain_key"}
    assert list(json.loads(text))[-1] == "chain_key_path" and text == json.dumps(json.loads(text), indent=2) + "\n"      # 2-space indent, order kept
    assert stat.S_IMODE(os.stat(conf).st_mode) == 0o600 and not list(conf.parent.glob("*.tmp"))
    assert chains.key_of(cfgmod.load(conf)).key == chain_key(key) and cfgmod.load(conf).chain_key_path == key


def test_keygen_creates_the_config_file_0600_when_there_is_none(tmp_path):
    conf = conf_at(tmp_path)                                  # a default install: no config file yet (in-process: the default data dir is real)
    cfg = cfgmod.Config(data_dir=tmp_path / "data")
    cfg.source = conf
    (tmp_path / "data").mkdir()
    assert chains_cli.keygen(cfg, lambda what: True, False, lambda prompt: "y") == 0
    assert json.loads(conf.read_text()) == {"chain_key_path": str(conf.parent / "chain_key")}       # outside HOME: spelled in full
    assert stat.S_IMODE(os.stat(conf).st_mode) == 0o600 and chain_key(conf.parent / "chain_key") is not None


def test_keygen_writes_nothing_when_the_answer_is_no(tmp_path, tty, capsys):
    conf = conf_at(tmp_path, {"data_dir": str(tmp_path / "data")})
    before = conf.read_bytes()
    tty.answer = "n"
    assert cli.main(["--config", str(conf), "keygen"]) == 1
    assert conf.read_bytes() == before and not (conf.parent / "chain_key").exists() and not (conf.parent / chains.HEADS).exists()


def test_keygen_rehead_does_not_write_the_config_and_needs_a_named_key(tmp_path, tty, capsys):
    conf = conf_at(tmp_path, {"data_dir": str(tmp_path / "data")})
    before = conf.read_bytes()
    assert cli.main(["--config", str(conf), "keygen", "--rehead"]) == 2
    assert "chain_key_path is not set" in capsys.readouterr().err and conf.read_bytes() == before and tty.asked == []


def test_keygen_leaves_an_explicit_null_alone(tmp_path, tty, capsys):
    conf = conf_at(tmp_path, {"data_dir": str(tmp_path / "data"), "chain_key_path": None})
    before = conf.read_bytes()
    assert cli.main(["--config", str(conf), "keygen"]) == 2
    assert "chain_key_path" in capsys.readouterr().err and conf.read_bytes() == before and not (conf.parent / "chain_key").exists() and tty.asked == []


def test_keygen_on_a_config_that_names_the_key_is_unchanged_and_does_not_rewrite_it(w, tty, capsys):
    before = Path(w.conf).read_bytes()
    assert cli.main(["--config", w.conf, "keygen"]) == 0
    assert Path(w.conf).read_bytes() == before and "will be created" not in capsys.readouterr().out and "config" not in tty.asked[0]


def test_keygen_without_a_terminal_writes_no_config(tmp_path, monkeypatch, capsys):
    conf = conf_at(tmp_path, {"data_dir": str(tmp_path / "data")})
    before = conf.read_bytes()
    monkeypatch.setattr(sys, "stdin", open(os.devnull))
    assert cli.main(["--config", str(conf), "keygen"]) == 2 and conf.read_bytes() == before


def test_the_config_edit_refuses_a_file_that_is_not_an_object_or_already_has_the_line(tmp_path):
    conf = conf_at(tmp_path)
    conf.parent.mkdir(mode=0o700)
    conf.write_text("[]")
    with pytest.raises(ValueError, match="not a JSON object"):
        cfgmod.with_chain_key(conf, tmp_path / "k")
    conf.write_text('{"chain_key_path": null}')
    with pytest.raises(ValueError, match="already in the configuration"):
        cfgmod.with_chain_key(conf, tmp_path / "k")


def test_keygen_follows_a_symlinked_config_file_instead_of_replacing_the_link(tmp_path, tty, capsys):
    real = conf_at(tmp_path, {"data_dir": str(tmp_path / "data")}, name="real.json")
    link = real.parent / "config.json"
    link.symlink_to(real)
    assert cli.main(["--config", str(link), "keygen"]) == 0
    assert link.is_symlink() and "chain_key_path" in json.loads(real.read_text())


def test_keygen_refuses_when_an_unnamed_key_file_already_sits_at_the_suggested_path(tmp_path, tty, capsys):
    conf = conf_at(tmp_path, {"data_dir": str(tmp_path / "data")})
    key = write_key(conf.parent / "chain_key", mode=0o600)
    before, key_before = conf.read_bytes(), key.read_bytes()
    assert cli.main(["--config", str(conf), "keygen"]) == 1
    err = capsys.readouterr().err
    assert "does not name it" in err and err.count("\n") == 1
    assert conf.read_bytes() == before and key.read_bytes() == key_before and tty.asked == [] and not (conf.parent / chains.HEADS).exists()


def test_keygen_keeps_an_edit_made_to_the_config_while_the_prompt_is_open(tmp_path, tty, monkeypatch):
    conf = conf_at(tmp_path, {"data_dir": str(tmp_path / "data")})

    def edit_then_yes(prompt):
        conf.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "model": "claude-sonnet-5-5"}, indent=2) + "\n")
        return "y"
    monkeypatch.setattr(cli, "_ask", edit_then_yes)
    assert cli.main(["--config", str(conf), "keygen"]) == 0
    assert json.loads(conf.read_text())["model"] == "claude-sonnet-5-5" and "chain_key_path" in json.loads(conf.read_text())


def test_keygen_says_the_config_was_rewritten_when_the_key_file_appears_during_the_prompt(tmp_path, tty, monkeypatch, capsys):
    conf = conf_at(tmp_path, {"data_dir": str(tmp_path / "data")})
    key = conf.parent / "chain_key"

    def key_then_yes(prompt):
        write_key(key)
        return "y"
    monkeypatch.setattr(cli, "_ask", key_then_yes)
    before = key.read_bytes() if key.exists() else None
    assert cli.main(["--config", str(conf), "keygen"]) == 1
    err = capsys.readouterr().err
    assert "nothing was written" not in err and "now names it" in err and "chain_key_path" in json.loads(conf.read_text())
    assert before is None and key.read_text() == K1.hex()


# ------------------------------------------------------------------ the config is found from any directory
def test_load_makes_the_source_absolute_and_keygen_never_writes_a_relative_path(tmp_path, tty, monkeypatch, capsys):
    conf = conf_at(tmp_path, {"data_dir": str(tmp_path / "data")})
    monkeypatch.chdir(conf.parent)
    assert cfgmod.load("config.json").source == conf.absolute() and cfgmod.load("config.json").source.is_absolute()
    assert cli.main(["--config", "config.json", "keygen"]) == 0
    written_path = json.loads(conf.read_text())["chain_key_path"]
    assert os.path.isabs(written_path) or written_path.startswith("~/")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert chains.key_of(cfgmod.load(conf)).key == chain_key(conf.parent / "chain_key")
    assert cfgmod.load(conf).chain_key_path == conf.parent / "chain_key"


# ------------------------------------------------------------------ v2 rows mark a keyed install wherever the config sits
def relocate(w, tmp_path, monkeypatch):
    """The key and heads move to an 'old default' directory the config does not name; the config moves to a third directory."""
    old, new = tmp_path / "olddefault", tmp_path / "elsewhere"
    old.mkdir()
    new.mkdir()
    for name in ("chain_key", chains.HEADS):
        if (tmp_path / name).exists():
            (tmp_path / name).rename(old / name)
    conf = new / "config.json"
    conf.write_text(json.dumps({"data_dir": str(w.data)}))
    monkeypatch.delenv(chains.ENV, raising=False)
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(conf))
    return conf


@pytest.mark.parametrize("chain", ["approvals", "demotions"])
def test_v2_rows_in_either_chain_mark_the_install_keyed_when_the_config_names_no_key_and_sits_elsewhere(w, run, tmp_path, monkeypatch, chain):
    ring = keyed(w)
    promote(w, "observe", ring) if chain == "approvals" else demote(w, ring)
    conf = relocate(w, tmp_path, monkeypatch)
    cfg = cfgmod.load(conf)
    assert cfg.chain_key_path is None and not (conf.parent / chains.HEADS).exists()
    ring = chains.key_of(cfg)
    assert ring is not None and ring.key is None                                      # was keyed yesterday: fail closed, not unkeyed
    assert approvals.read_chain(w.data, ring)[1] is not None and dm.read_chain(w.data, ring)[1] is not None
    with pytest.raises(chains.KeyMissing):
        chains_io.need_key(cfg, approvals)
    assert "restore the chain key file" in run.capsys.readouterr().err                  # the writer's refusal, not the hook's
    assert run(cs.pre_payload(RM)) == (0, "", "") and run.log.count("chain_key:missing") == 1


def test_the_synthetic_replay_keeps_the_config_source_of_its_scratch_copy(tmp_path, monkeypatch):
    from ace_playbook.compile import shadow, synth
    seen = []
    monkeypatch.setattr(shadow, "_evaluate", lambda scratch, ev, sid, names, log: seen.append((scratch.source, scratch.data_dir)) or [])
    cfg = cfgmod.Config(data_dir=tmp_path)
    cfg.source = tmp_path / "config.json"
    synth.replay(cfg, [("s", [("rm_rf_nonscratch", "none", cs.pre_payload(RM))])])
    assert seen and seen[0][0] == cfg.source and seen[0][1] != tmp_path


@pytest.mark.parametrize("chain", ["approvals", "demotions"])
def test_keygen_refuses_the_upgrade_layout_instead_of_minting_a_second_key(w, tty, tmp_path, monkeypatch, capsys, chain):
    ring = keyed(w)
    promote(w, "observe", ring) if chain == "approvals" else demote(w, ring)
    conf = relocate(w, tmp_path, monkeypatch)                  # the real key stays at the old location; the config names none
    before, chains_before = conf.read_bytes(), {p.name: p.read_bytes() for p in w.data.glob("*.jsonl")}
    assert cli.main(["--config", str(conf), "keygen"]) == 1
    assert "restore the chain key file" in capsys.readouterr().err and tty.asked == []
    assert conf.read_bytes() == before and sorted(p.name for p in conf.parent.iterdir()) == ["config.json"]
    assert {p.name: p.read_bytes() for p in w.data.glob("*.jsonl")} == chains_before and not list(w.data.glob("*.bak"))


def test_v2_rows_are_found_anywhere_in_a_large_chain_file_and_in_either_spacing(tmp_path):
    from ace_playbook.compile import modes
    ring = chains.Keyring(K1)                                  # no heads file: only the rows matter here
    for _ in range(3):
        approvals.append(tmp_path, {"proposal_id": "P-0123abcd", "proposal_sha256": "a" * 64, "mode": "observe"}, ring)
    keyed_text = (tmp_path / approvals.FILE).read_text()
    assert modes.v2_rows(tmp_path)
    (tmp_path / approvals.FILE).write_text(keyed_text.replace('"v": 2', '"v":2'))
    assert modes.v2_rows(tmp_path)
    v1_first = '{"v": 1, "x": 1}\n' * 1000
    (tmp_path / approvals.FILE).write_text(v1_first + keyed_text)                       # a v1 row placed first must not hide the marker
    assert modes.v2_rows(tmp_path)
    (tmp_path / approvals.FILE).write_text(v1_first)
    assert not modes.v2_rows(tmp_path)


def test_the_v2_marker_check_costs_milliseconds_on_two_chain_files_of_1_mib_of_junk(tmp_path):
    """The hook pays this on every call of a never-keyed install: json-parsing 1 MiB of `{}` lines took 438 ms; the bytes regex that
    gates the parse takes a few ms. The bound is generous (best of 3 under 100 ms) so a loaded machine cannot flake it."""
    import time
    from ace_playbook.compile import modes
    junk = b"{}\n" * ((1 << 20) // 3)
    for name in (approvals.FILE, dm.FILE):
        (tmp_path / name).write_bytes(junk)
    best = min((lambda t0: (modes.v2_rows(tmp_path), time.perf_counter() - t0)[1])(time.perf_counter()) for _ in range(3))
    assert not modes.v2_rows(tmp_path) and best < 0.1, best


def test_v1_only_rows_and_no_marker_stay_an_unkeyed_install_wherever_the_config_sits(w, tmp_path, monkeypatch):
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "warn", row=True)             # a v1 row
    cfg = cfgmod.load(relocate(w, tmp_path, monkeypatch))
    assert chains.key_of(cfg) is None and approvals.read_chain(w.data, None)[1] is None
