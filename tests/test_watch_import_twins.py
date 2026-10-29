"""Opus round 2: an import twin never outlives the own scan's diff (expired, auto-closed, forged), the replacement is reported and pinned."""

import json
import os
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.compile.watch import cli, records, store, vet

from .test_compile_watch_cli import NEW, World
from .test_compile_watch_cli import w as world  # noqa: F401 - the fixture
from .test_watch_import import World as ImportWorld, make

FMT = "%Y-%m-%dT%H:%M:%SZ"


@pytest.fixture
def iw(tmp_path, monkeypatch):
    return ImportWorld(tmp_path, monkeypatch)


def stored(w):
    return {p.stem: json.loads(p.read_text()) for p in (w.wd / "proposals").glob("W-*.json")}


def model_pins(w):
    now = datetime.now(timezone.utc).strftime(FMT)
    return [(p, s) for p, s in store.statuses(w.wd, now) if p["class"] == "model_pin"]


def two_worlds(tmp_path, monkeypatch, capsys):
    (tmp_path / "acewatch").mkdir()
    sandbox = World(tmp_path / "acewatch", monkeypatch)
    sandbox.run(capsys, "scan")
    pin = next(r for r in stored(sandbox).values() if r["class"] == "model_pin")
    (tmp_path / "operator").mkdir()
    return pin, World(tmp_path / "operator", monkeypatch)


def plant(mine, rec, **over):
    """A diff-less imported-looking twin written straight into the operator's store (as an import long ago would have)."""
    twin = {**rec, "title": rec["title"] + " (imported)", "change": {**rec["change"], "diff": None, "diff_sha256": None}, **over}
    store.ensure(mine.wd)
    assert store.write_proposal(mine.wd, twin)
    return twin


def live_with_diff(mine):
    return [p for p, s in model_pins(mine) if s in ("open", "queued") and p["change"]["diff"]]


def test_an_expired_copy_is_rejected_as_dates(iw, capsys):
    gone = make(1)
    gone["created"], gone["expires"] = "2026-09-01T00:00:00Z", "2026-09-30T00:00:00Z"
    soon = make(2)
    now = datetime.now(timezone.utc)
    soon["created"], soon["expires"] = (now - timedelta(days=5)).strftime(FMT), (now + timedelta(days=5)).strftime(FMT)
    iw.put(gone)
    iw.put(soon)
    code, out, _ = iw.run(capsys)
    assert out[:2] == ["import: 1 imported, 0 duplicate, 1 rejected", f"rejected {gone['id']}: dates"] and iw.stored() == [soon["id"] + ".json"]


def test_an_expired_diff_less_twin_gives_way_to_the_own_scan(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    plant(mine, pin, created="2026-09-01T00:00:00Z", expires="2026-09-30T00:00:00Z")
    assert live_with_diff(mine) == []
    code, out, _ = mine.run(capsys, "scan", "--force")
    out = out.splitlines()
    assert out[-1].endswith("; diffed 1") and f"diffed {pin['id']}" in out
    [own] = live_with_diff(mine)
    assert own["id"] == pin["id"] and not own["title"].endswith(" (imported)") and records.check(own) is None


def test_an_auto_closed_diff_less_twin_is_replaced_and_reopened(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    plant(mine, pin)
    store.append_decision(mine.wd, pin["id"], "closed", "trigger vanished", "2026-10-28T00:00:00Z")
    assert [s for _, s in model_pins(mine)] == ["auto-closed"]
    code, out, _ = mine.run(capsys, "scan", "--force")
    assert f"diffed {pin['id']}" in out
    [own] = live_with_diff(mine)
    assert own["id"] == pin["id"] and own["change"]["diff"].count("\n") == 5


def test_a_forged_successor_under_the_own_id_hides_nothing_on_this_scan_or_the_next(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    forged = {**pin, "title": pin["title"].replace(NEW, "claude-evil-9") + " (imported)", "change": {**pin["change"], "diff": None, "diff_sha256": None}}
    assert records.check(forged) is None and forged["id"] == pin["id"] and reconcile_key(forged) != reconcile_key(pin)
    store.ensure(mine.wd)
    store.write_proposal(mine.wd, forged)
    for _ in range(2):
        code, out, _ = mine.run(capsys, "scan", "--force")
        [own] = live_with_diff(mine)
        assert own["id"] == pin["id"] and NEW in own["title"] and "evil" not in own["title"] and len(model_pins(mine)) == 1


def reconcile_key(rec):
    from ace_playbook.compile.watch import reconcile
    return reconcile.key(rec)


def test_a_dismissed_twin_is_a_decision_and_is_not_replaced(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    plant(mine, pin)
    store.append_decision(mine.wd, pin["id"], "dismissed", "not trusted", "2026-10-28T00:00:00Z")
    code, out, _ = mine.run(capsys, "scan", "--force")
    assert [s for _, s in model_pins(mine)] == ["dismissed"] and not any(ln.startswith("diffed") for ln in out.splitlines())


def test_a_dry_run_scan_reports_the_replacement_and_changes_no_file(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    twin = plant(mine, pin)
    path = mine.wd / "proposals" / (pin["id"] + ".json")
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    code, out, _ = mine.run(capsys, "scan", "--force", "--dry-run")
    out = out.splitlines()
    assert f"diffed {pin['id']}" in out and out[-1].endswith("; diffed 1")
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime and json.loads(before)["title"] == twin["title"]


def test_the_scan_line_without_a_replacement_is_the_pinned_text(world, capsys):
    code, out, _ = world.run(capsys, "scan")
    assert out.splitlines()[-1] == "watch: fetched 5, unchanged 0, dropped 0; new 3, closed 0, promoted 0; open 2, leads 1"


def test_an_accept_pins_the_diff_hash_so_a_replaced_record_reads_stale(world, capsys):
    world.run(capsys, "scan")
    cfg = cfgmod.load()
    answers = iter(["1"] * 5)
    assert cli.queue(cfg, 5, lambda prompt: next(answers), lambda what: True) == 0
    notes = {d["id"]: d["note"] for d in store.read_decisions(world.wd) if d["decision"] == "accepted"}
    recs = stored(world)
    assert notes and all(n == "diff_sha256 " + (recs[i]["change"]["diff_sha256"] or "none") for i, n in notes.items())
    assert any(n != "diff_sha256 none" for n in notes.values()) and "diff_sha256 none" in notes.values()


# ------------------------------------------------------------------ vet.read branches
def test_a_file_swapped_between_the_lstat_and_the_open_is_refused_as_not_a_file(iw, capsys, monkeypatch, tmp_path):
    rec = make(1)
    iw.put(rec)
    spare = tmp_path / "spare"
    spare.write_text(json.dumps(make(2)))
    real = os.lstat
    swapped = []

    def lstat(name, *a, dir_fd=None, **k):
        out = real(name, *a, dir_fd=dir_fd, **k)
        if dir_fd is not None and not swapped:
            swapped.append(True)
            shutil.copyfile(spare, iw.src / "tmp.swap")
            os.replace(iw.src / "tmp.swap", iw.src / (rec["id"] + ".json"))                 # a new inode under the same name
        return out

    monkeypatch.setattr(vet.os, "lstat", lstat)
    code, out, _ = iw.run(capsys)
    assert out[:2] == ["import: 0 imported, 0 duplicate, 1 rejected", f"rejected {rec['id']}: not a file"] and iw.stored() == []



# ------------------------------------------------------------------ round 3
def fill(mine, n=5):
    store.ensure(mine.wd)
    for i in range(n):
        assert store.write_proposal(mine.wd, {**make(100 + i), "status": "open"})


def free_slots(mine):
    """Two slots: the scan's own queued `version` proposal takes the first one (rebalance runs before the reopen path)."""
    for n in (100, 101):
        store.append_decision(mine.wd, make(n)["id"], "dismissed", "free a slot", "2026-10-29T00:00:00Z")


def statuses(mine):
    return store.statuses(mine.wd, datetime.now(timezone.utc).strftime(FMT))


def pin_status(mine):
    return [s for p, s in statuses(mine) if p["class"] == "model_pin"]


def test_dismissing_a_forged_successor_copy_hides_nothing_but_the_same_key_dismissal_stays(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    forged = {**pin, "title": pin["title"].replace(NEW, "claude-evil-9") + " (imported)", "change": {**pin["change"], "diff": None, "diff_sha256": None}}
    store.ensure(mine.wd)
    store.write_proposal(mine.wd, forged)
    store.append_decision(mine.wd, pin["id"], "dismissed", "wrong successor", "2026-10-28T00:00:00Z")
    for _ in range(2):
        mine.run(capsys, "scan", "--force")
        [own] = live_with_diff(mine)
        assert own["id"] == pin["id"] and NEW in own["title"] and pin_status(mine) == ["open"]
    (tmp_path / "same").mkdir()
    twin_world = World(tmp_path / "same", monkeypatch)
    plant(twin_world, pin)                                                       # the SAME key, dismissed: a decision about this very finding
    store.append_decision(twin_world.wd, pin["id"], "dismissed", "not wanted", "2026-10-28T00:00:00Z")
    twin_world.run(capsys, "scan", "--force")
    assert pin_status(twin_world) == ["dismissed"]


def test_a_dismissed_forged_twin_waits_for_a_free_slot_when_five_are_open(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    forged = {**pin, "title": pin["title"].replace(NEW, "claude-evil-9") + " (imported)", "change": {**pin["change"], "diff": None, "diff_sha256": None}}
    store.ensure(mine.wd)
    store.write_proposal(mine.wd, forged)
    store.append_decision(mine.wd, pin["id"], "dismissed", "wrong successor", "2026-10-28T00:00:00Z")
    fill(mine)
    mine.run(capsys, "scan", "--force")
    assert pin_status(mine) == ["dismissed"] and store.open_count(statuses(mine)) == 5
    free_slots(mine)
    mine.run(capsys, "scan", "--force")
    assert pin_status(mine) == ["open"] and store.open_count(statuses(mine)) == 5 and len(live_with_diff(mine)) == 1


def test_an_auto_closed_twin_with_the_cap_full_stays_out_until_a_slot_frees(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    plant(mine, pin)
    store.append_decision(mine.wd, pin["id"], "closed", "trigger vanished", "2026-10-28T00:00:00Z")
    fill(mine)
    code, out, _ = mine.run(capsys, "scan", "--force")
    assert f"diffed {pin['id']}" in out.splitlines() and pin_status(mine) == ["auto-closed"] and store.open_count(statuses(mine)) == 5
    assert stored(mine)[pin["id"]]["change"]["diff"] and records.check(stored(mine)[pin["id"]]) is None
    free_slots(mine)
    mine.run(capsys, "scan", "--force")
    assert pin_status(mine) == ["open"] and store.open_count(statuses(mine)) == 5


def test_an_expired_twin_with_a_promoted_row_and_the_cap_full_does_not_exceed_the_cap(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    plant(mine, pin, created="2026-09-01T00:00:00Z", expires="2026-09-30T00:00:00Z")
    store.append_decision(mine.wd, pin["id"], "promoted", "slot freed", "2026-09-02T00:00:00Z")
    fill(mine)
    mine.run(capsys, "scan", "--force")
    assert pin_status(mine) == ["expired"] and store.open_count(statuses(mine)) == 5
    free_slots(mine)
    mine.run(capsys, "scan", "--force")
    assert pin_status(mine) == ["open"] and store.open_count(statuses(mine)) == 5 and live_with_diff(mine)[0]["change"]["diff"]


def test_an_expired_twin_with_a_stale_reopened_row_is_live_at_once(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    plant(mine, pin, created="2026-08-01T00:00:00Z", expires="2026-08-30T00:00:00Z")
    store.append_decision(mine.wd, pin["id"], "reopened", "2026-09-29T00:00:00Z", "2026-08-30T00:00:00Z")
    assert pin_status(mine) == ["expired"]
    mine.run(capsys, "scan", "--force")
    assert pin_status(mine) == ["open"] and len(live_with_diff(mine)) == 1


@pytest.mark.parametrize("how", ["expired", "auto-closed"])
def test_a_non_live_twin_of_another_id_and_the_same_key_stays_closed_and_the_candidate_is_written_beside_it(world, capsys, how):
    from .test_watch_import_own import forged_twin
    rec = forged_twin(world, diff=None)
    over = {"created": "2026-09-01T00:00:00Z", "expires": "2026-09-30T00:00:00Z"} if how == "expired" else {}
    store.ensure(world.wd)
    assert store.write_proposal(world.wd, {**rec, **over})
    if how == "auto-closed":
        store.append_decision(world.wd, rec["id"], "closed", "trigger vanished", "2026-10-28T00:00:00Z")
    world.run(capsys, "scan", "--force")
    by_id = {p["id"]: (p, s) for p, s in statuses(world) if p["class"] == "model_pin"}
    assert by_id[rec["id"]][1] == how and len(by_id) == 2
    [(own, status)] = [v for i, v in by_id.items() if i != rec["id"]]
    assert status == "open" and own["change"]["diff"] and not any(d["note"] == "superseded" for d in store.read_decisions(world.wd))


def test_the_same_id_twice_in_one_run_is_replaced_and_listed_once(tmp_path, monkeypatch, capsys):
    from ace_playbook.compile.watch import reconcile
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    plant(mine, pin, created="2026-09-01T00:00:00Z", expires="2026-09-30T00:00:00Z")
    now = datetime.now(timezone.utc).strftime(FMT)
    out = reconcile.reconcile(mine.wd, [pin, pin], [], now, False, None)
    assert out["diffed"] == [pin["id"]] and out["reopened"] == [pin["id"]]
    assert [d["decision"] for d in store.read_decisions(mine.wd) if d["id"] == pin["id"]] == ["reopened"]


def test_an_accept_of_a_record_without_a_diff_hash_key_does_not_raise(world, capsys):
    store.ensure(world.wd)
    rec = {**make(1), "change": {"target": "k1", "diff": None}}
    assert store.write_proposal(world.wd, rec)
    assert cli.queue(cfgmod.load(), 5, lambda prompt: "1", lambda what: True) == 0
    assert [d["note"] for d in store.read_decisions(world.wd)] == ["diff_sha256 none"]

# ------------------------------------------------------------------ Track M2: an import twin of the own key never hides the own diff
OTHER = "W-" + "e" * 8


def forge(mine, pin, how):
    """The own id as a diff-less forged successor (another key), dismissed / accepted / left live; then the live other-id twin of the OWN key."""
    plant(mine, pin, title=pin["title"].replace(NEW, "claude-evil-9") + " (imported)")
    if how != "live":
        store.append_decision(mine.wd, pin["id"], how, "wrong successor", "2026-10-28T00:00:00Z")


def decisions(mine):
    return store.read_decisions(mine.wd)


def superseded_ids(mine):
    return [d["id"] for d in decisions(mine) if d["decision"] == "closed" and d["note"] == "superseded"]


def scan(mine, capsys):
    mine.run(capsys, "scan", "--force")
    return len(decisions(mine))


def one_own_diff(mine, pin):
    assert [p["id"] for p in live_with_diff(mine)] == [pin["id"]]
    assert dict((p["id"], s) for p, s in statuses(mine))[pin["id"]] == "open" and store.open_count(statuses(mine)) <= 5


def test_a_held_dismissed_twin_never_closes_a_live_other_id_twin_of_the_same_key(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    forge(mine, pin, "dismissed")
    plant(mine, pin, id=OTHER)                                                               # another id, the own key, no diff, live
    fill(mine, 4)                                                                            # the live other-id twin is the fifth open one
    scan(mine, capsys)
    assert live_with_diff(mine) == [] and superseded_ids(mine) == []
    assert dict((p["id"], s) for p, s in statuses(mine))[OTHER] in ("open", "queued") and store.open_count(statuses(mine)) == 5
    free_slots(mine)
    scan(mine, capsys)
    one_own_diff(mine, pin)
    assert superseded_ids(mine) == [OTHER] and dict((p["id"], s) for p, s in statuses(mine))[OTHER] != "open"
    seen = scan(mine, capsys)                                                                # a third scan changes nothing
    one_own_diff(mine, pin)
    assert seen == len(decisions(mine)) and superseded_ids(mine) == [OTHER] and pin["id"] not in superseded_ids(mine)


def test_the_slot_free_world_reopens_the_own_record_and_closes_the_other_id_twin_at_once(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    forge(mine, pin, "dismissed")
    plant(mine, pin, id=OTHER)
    seen = scan(mine, capsys)                                                                # the cap is free
    one_own_diff(mine, pin)
    assert superseded_ids(mine) == [OTHER] and [(d["id"], d["decision"]) for d in decisions(mine)] == [
        (pin["id"], "dismissed"), (pin["id"], "reopened"), (OTHER, "closed")]
    for _ in range(2):
        assert scan(mine, capsys) == seen
        one_own_diff(mine, pin)


def test_a_future_created_other_id_twin_cannot_make_the_own_record_the_superseded_one(tmp_path, monkeypatch, capsys):
    from ace_playbook.compile.watch import reconcile
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    soon = (datetime.now(timezone.utc) + timedelta(hours=12)).strftime(FMT)                  # within vet's now + 1 day
    forge(mine, pin, "dismissed")
    plant(mine, pin, id=OTHER, created=soon, expires=(datetime.now(timezone.utc) + timedelta(days=20)).strftime(FMT))
    fill(mine, 4)
    scan(mine, capsys)
    assert live_with_diff(mine) == [] and superseded_ids(mine) == []                         # the held scan closes nothing
    free_slots(mine)
    seen = scan(mine, capsys)
    for _ in range(2):
        one_own_diff(mine, pin)
        assert superseded_ids(mine) == [OTHER] and scan(mine, capsys) == seen
    both = [(pin, "open"), (plant(mine, pin, id="W-" + "d" * 8, created=soon), "open")]      # superseded() keeps the diffed record, whatever the stamps
    assert reconcile.superseded(both, [pin], set()) == ["W-" + "d" * 8]


def test_a_held_accepted_other_key_twin_waits_and_the_other_id_twin_is_closed_after_the_free(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    forge(mine, pin, "accepted")
    plant(mine, pin, id=OTHER)
    fill(mine, 4)
    scan(mine, capsys)
    assert live_with_diff(mine) == [] and superseded_ids(mine) == []
    free_slots(mine)
    seen = scan(mine, capsys)
    one_own_diff(mine, pin)
    assert superseded_ids(mine) == [OTHER] and scan(mine, capsys) == seen
    one_own_diff(mine, pin)


def test_a_live_forged_own_id_twin_is_replaced_in_place_and_the_other_id_twin_is_closed(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    forge(mine, pin, "live")
    plant(mine, pin, id=OTHER)
    seen = scan(mine, capsys)
    one_own_diff(mine, pin)
    assert superseded_ids(mine) == [pin["id"], OTHER] and list(stored(mine)).count(pin["id"]) == 1         # the forged successor first, then the twin
    assert [d["decision"] for d in decisions(mine) if d["id"] == pin["id"]][-1] == "reopened"   # the forged successor: closed as another key, then replaced
    for _ in range(2):
        assert scan(mine, capsys) == seen
        one_own_diff(mine, pin)


# ------------------------------------------------------------------ Track M2 wave 3: an own diffed record that is not live is reopened, whatever the import
OLD = {"created": "2026-09-01T00:00:00Z", "expires": "2026-09-30T00:00:00Z"}


def stamp(**ago):
    return (datetime.now(timezone.utc) + timedelta(**ago)).strftime(FMT)


def own_diffed(mine, pin, how):
    """The operator's own record WITH its diff, expired or auto-closed (not an import)."""
    store.ensure(mine.wd)
    assert store.write_proposal(mine.wd, {**pin, **OLD} if how == "expired" else pin)
    if how == "auto-closed":
        store.append_decision(mine.wd, pin["id"], "closed", "trigger vanished", "2026-10-28T00:00:00Z")


def other_twin(mine, pin, how):
    """A diff-less same-key import under another id: live older / live newer (created up to vet's now + 1 d) / expired / auto-closed."""
    over = {"live older": {"created": "2026-08-01T00:00:00Z", "expires": stamp(days=20)},
            "live newer": {"created": stamp(hours=12), "expires": stamp(days=20)},
            "expired": {"created": "2026-09-15T00:00:00Z", "expires": "2026-10-10T00:00:00Z"},
            "auto-closed": {"created": stamp(hours=12), "expires": stamp(days=20)}}[how]
    plant(mine, pin, id=OTHER, **over)
    if how == "auto-closed":
        store.append_decision(mine.wd, OTHER, "closed", "trigger vanished", "2026-10-28T00:00:00Z")


def rows(mine):
    return [(d["id"], d["decision"], d["note"] if d["decision"] == "closed" else "") for d in decisions(mine)]


def test_an_expired_own_record_without_an_import_is_reopened(tmp_path, monkeypatch, capsys):          # the control
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    own_diffed(mine, pin, "expired")
    scan(mine, capsys)
    one_own_diff(mine, pin)
    assert [r[:2] for r in rows(mine)] == [(pin["id"], "reopened")]


@pytest.mark.parametrize("how", ["expired", "auto-closed"])
@pytest.mark.parametrize("twin", ["auto-closed", "live newer"])
def test_an_expired_or_auto_closed_own_record_is_reopened_beside_a_later_created_import(tmp_path, monkeypatch, capsys, how, twin):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    own_diffed(mine, pin, how)
    other_twin(mine, pin, twin)                                                              # F1 (the import is closed already) and F2 (it is live)
    seen = scan(mine, capsys)
    one_own_diff(mine, pin)
    assert (pin["id"], "reopened", "") in rows(mine) and (superseded_ids(mine) == [OTHER]) == (twin == "live newer")
    for _ in range(2):
        assert scan(mine, capsys) == seen
        one_own_diff(mine, pin)


@pytest.mark.parametrize("other", ["live older", "live newer", "expired", "auto-closed"])
@pytest.mark.parametrize("own", ["expired", "auto-closed", "expired forged"])
def test_a_held_not_live_own_id_twin_is_reopened_after_the_free_whatever_the_other_id_twin(tmp_path, monkeypatch, capsys, own, other):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    if own == "expired forged":
        plant(mine, pin, title=pin["title"].replace(NEW, "claude-evil-9") + " (imported)", **OLD)
    else:
        plant(mine, pin, **(OLD if own == "expired" else {}))
        if own == "auto-closed":
            store.append_decision(mine.wd, pin["id"], "closed", "trigger vanished", "2026-10-28T00:00:00Z")
    other_twin(mine, pin, other)
    live = other.startswith("live")
    fill(mine, 4 if live else 5)                                                             # the live twin is the fifth open record
    before = rows(mine)
    scan(mine, capsys)
    assert rows(mine) == before and live_with_diff(mine) == [] and store.open_count(statuses(mine)) == 5
    free_slots(mine)
    seen = scan(mine, capsys)
    one_own_diff(mine, pin)
    assert superseded_ids(mine) == ([OTHER] if live else [])
    assert scan(mine, capsys) == seen
    one_own_diff(mine, pin)


def test_a_queued_own_id_import_closed_by_a_newer_import_still_gets_the_own_diff_after_the_free(tmp_path, monkeypatch, capsys):      # F6
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    plant(mine, pin, status="queued")                                                        # the own id, an import waiting for a slot
    plant(mine, pin, id=OTHER, created=stamp(hours=12), expires=stamp(days=20))              # a newer same-key import, live
    fill(mine, 4)
    before = len(decisions(mine))
    scan(mine, capsys)
    assert superseded_ids(mine) == [pin["id"]] and len(decisions(mine)) == before + 1       # superseded() keeps the newest of two diff-less records
    assert live_with_diff(mine) == [] and list(stored(mine)).count(pin["id"]) == 1
    assert stored(mine)[pin["id"]]["change"]["diff"]                                         # the own diff is on file (held replacement), waiting
    free_slots(mine)
    seen = scan(mine, capsys)
    one_own_diff(mine, pin)                                                                  # the file still says queued; the `reopened` row makes it open
    assert superseded_ids(mine) == [pin["id"], OTHER] and scan(mine, capsys) == seen
    one_own_diff(mine, pin)


def test_a_lead_never_carries_a_diff_so_the_diff_gated_import_branches_cannot_apply_to_it(tmp_path, monkeypatch, capsys):
    pin, mine = two_worlds(tmp_path, monkeypatch, capsys)
    scan(mine, capsys)                                              # every branch that closes or reopens for an import is gated on a diff;
    leads = [r for r in stored(mine).values() if r["class"] == "lead"]                       # no code path gives a lead one
    assert leads and all(r["change"]["diff"] is None and r["change"]["diff_sha256"] is None for r in leads)
