"""A model_pin the rules produce always has a title that parses to its model id, so `records.check` (verify = a grep for that id) accepts it, however long the ids are."""

import hashlib

import pytest

from ace_playbook.compile.watch import match, pin, reconcile, records

NOW = "2026-10-21T12:00:00Z"
FIT = 120 - len("Replace  with  in ")                  # what the id and the successor of a title may share: 102


def pinned(mid, succ):
    row = f"| {mid} | {succ} |"
    res = {"id": "dep", "url": "https://example.test/deprecations.md", "kind": "deprecations", "tier": 0, "sha256": "9" * 64, "prev_sha256": None,
           "data": [{"model_id": mid, "row": row, "deprecated": "2026-01-01", "retires": "2027-03-01", "successor": succ, "state": "Deprecated"}],
           "quote": row, "heading": "", "body": row}
    inv = {"generated_at": NOW, "claude_version": None, "targets": {"settings.model": {"files": {"~/a.json": {"sha256": "1" * 64, "lines": [[1, f' "{mid}",']]}}}}}
    return match.candidates(inv, [res], {}, {}, NOW)


def test_a_long_id_and_a_long_successor_still_make_a_checked_model_pin():
    mid, succ = "claude-" + "x" * 50, "claude-" + "y" * 50                      # on main `check` accepted this record; the 120-character cut made it fail `verify`
    got = pinned(mid, succ)
    assert [r["class"] for r in got] == ["model_pin"]                           # kills "shown() removed from pin.model_pin": the record was dropped
    r = got[0]
    assert records.check(r) is None and records.title_model(r["title"])[0] == mid and f'grep -n "{mid}" -- ' in r["verify"]
    assert succ in r["change"]["diff"] and len(r["title"]) <= 120              # the diff keeps the full successor


@pytest.mark.parametrize("total", [FIT - 1, FIT, FIT + 1, FIT + 2, 120])    # a successor is at most 80 characters (`pin.SAFE`)
def test_the_boundary_around_the_title_cut(total):
    mid = "claude-" + "x" * 33
    succ = "claude-" + "y" * (total - len(mid) - 7)
    got = pinned(mid, succ)
    assert len(got) == 1 and records.check(got[0]) is None
    shown = records.title_model(got[0]["title"])
    assert shown[0] == mid and succ in got[0]["change"]["diff"]
    if total <= FIT:
        assert shown[1] == succ and got[0]["title"] == f"Replace {mid} with {succ} in settings.model"[:120]          # no cut: the title is the one main wrote
    else:
        assert shown[1] == pin.shown(mid, succ) and len(shown[1]) == FIT - len(mid) and "…" + hashlib.sha256(succ.encode()).hexdigest()[:6] in shown[1]
        assert succ.startswith(shown[1].split("…")[0]) and shown[1].endswith(hashlib.sha256(succ.encode()).hexdigest()[:6])


def test_two_long_successors_with_a_common_prefix_keep_two_titles_and_two_reconcile_keys():
    mid = "claude-" + "x" * 73                                                   # 80 characters: 22 are left for the successor, so only 15 of it show
    prefix = "claude-" + "y" * 30                                                # 37 characters in common
    a, b = pinned(mid, prefix + "a" * 40)[0], pinned(mid, prefix + "b" * 40)[0]
    assert a["title"] != b["title"] and reconcile.key(a) != reconcile.key(b) and a["id"] != b["id"]       # kills "the hash suffix removed"
    assert records.check(a) is None and records.check(b) is None and records.title_model(a["title"])[0] == mid


@pytest.mark.parametrize("n", range(10, 81, 7))
def test_the_title_names_the_full_id_for_every_id_length(n):
    mid = "claude-" + "x" * (n - 7) if n > 7 else "claude-x"
    got = pinned(mid, "claude-" + "y" * 73)
    assert len(got) == 1 and records.check(got[0]) is None and records.title_model(got[0]["title"])[0] == mid and len(got[0]["title"]) <= 120


def test_the_longest_id_the_rules_accept_keeps_a_command_for_the_full_id():
    mid = "claude-" + "x" * 73                                                   # 80 characters: what `pin.SAFE` allows
    assert pin.SAFE.fullmatch(mid) and not pin.SAFE.fullmatch(mid + "x")
    got = pinned(mid, "claude-" + "y" * 73)
    assert len(got) == 1 and records.check(got[0]) is None and records.title_model(got[0]["title"])[0] == mid
    assert pin.shown(mid, "claude-new") == "claude-new" and len(pin.shown(mid, "z" * 80)) == FIT - len(mid) and pin.shown(mid, "z" * 80).endswith(hashlib.sha256(b"z" * 80).hexdigest()[:6])
