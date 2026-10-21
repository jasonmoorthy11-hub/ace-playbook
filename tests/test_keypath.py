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
from ace_playbook.compile import approvals, chains, chains_cli
from ace_playbook.compile import demotions as dm

from . import compile_support as cs
from .test_compile_cli import run as cli_run, tty, world, written  # noqa: F401  (fixtures)
from .test_compile_keyed_heads import w, run  # noqa: F401  (fixtures)
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
