"""The brief's config keys (docs/design/compiler.md 8.x "Brief (as built)")."""

import json
from pathlib import Path

import pytest

from ace_playbook import config as cfgmod


def test_defaults_put_the_brief_under_data_dir_and_pin_the_models():
    c = cfgmod.from_dict({"data_dir": "/d"})
    assert c.brief_dir is None and c.brief_path == Path("/d/brief") and c.brief_exclusions is None
    assert (c.brief_model, c.brief_model_stage2) == ("claude-sonnet-5-5", "claude-sonnet-5-5")
    assert (c.brief_dedupe_hours, c.brief_max_budget_usd) == (4, 1.0)


def test_brief_paths_expand_and_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    c = cfgmod.from_dict({"brief_dir": "~/b", "brief_exclusions": "~/x.json", "brief_model": "m1", "brief_model_stage2": "m2"})
    assert c.brief_path == tmp_path / "b" and c.brief_exclusions == tmp_path / "x.json"
    assert (c.brief_model, c.brief_model_stage2) == ("m1", "m2")


def test_unknown_keys_are_still_refused_and_bad_numbers_too(tmp_path):
    with pytest.raises(ValueError, match="unknown config keys"):
        cfgmod.from_dict({"brief_dirs": "x"})
    for bad in ({"brief_dedupe_hours": -1}, {"brief_max_budget_usd": 0}, {"brief_max_budget_usd": 1000}):
        with pytest.raises(ValueError):
            cfgmod.from_dict(bad)
    conf = tmp_path / "c.json"
    conf.write_text(json.dumps({"brief_dedupe_hours": 0}))
    assert cfgmod.load(conf).brief_dedupe_hours == 0
