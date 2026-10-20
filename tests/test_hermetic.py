"""The suite must stay green whether or not the developer's machine holds a chain key (see tests/conftest.py)."""

import json
import os

from ace_playbook import config as cfgmod

from .test_hooks import env  # noqa: F401  (fixture)


def test_a_config_file_without_chain_key_path_never_resolves_to_the_real_key(tmp_path, hermetic_chain_key):
    conf = tmp_path / "c.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "data")}))
    cfg = cfgmod.load(conf)
    assert cfg.chain_key_path == hermetic_chain_key and not cfg.chain_key_path.exists()
    assert ".config" not in cfg.chain_key_path.parts


def test_child_processes_get_a_throwaway_home(env):  # noqa: F811
    e, _ = env
    assert e["HOME"] != os.environ.get("HOME") and os.path.isdir(e["HOME"])
    assert not os.path.exists(os.path.join(e["HOME"], ".config"))
