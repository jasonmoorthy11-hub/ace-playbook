"""The delivered-path error count of the arm analysis (`arms_window.delivered_errors`, condition (7) of PREREGISTRATION.md) and the
natural end of the horizon: a lowering demotion row stamped after it is not the horizon's demotion. Invented stamps and logs only."""

import os

import pytest

from ace_playbook.compile import arms, demotions, shadow

from .test_arms import DEMOTION, prereg, world  # noqa: F401  (fixtures and helpers)
from .test_compile_queue import w  # noqa: F401  (fixture)

END, NOW = "2026-11-10T00:00:00Z", "2026-11-18T00:00:00Z"            # a demotion stamp (= the horizon end) and an analysis time after it
COUNTED = "2026-11-02T00:00:00Z error:delivered:OSError\n"


def demoted_world(w, cause, ts=END):
    world(w)
    demotions.append(w.data, {"proposal_id": w.pid, **DEMOTION, "cause": cause, "ts": ts})


def analysed(w, now=NOW):
    return next(iter(arms.analyse(w.cfg, now=now)["detectors"].values()))


def delivered_world(w, cause, log=True):
    demoted_world(w, cause)
    if log:
        lines = ["2026-10-30T00:00:00Z error:delivered:OSError", COUNTED.strip(),
                 "2026-11-03T00:00:00Z error:ValueError", "2026-11-09T00:00:00Z error:delivered:BrokenPipeError",
                 "2026-11-12T00:00:00Z error:delivered:OSError", "2026-11-04T00:00:00Z note error:delivered:x"]
        w.cfg.path("shadow.log").write_text("\n".join(lines) + "\n")
    return analysed(w)


# ------------------------------------------------------------------ round 7 docs-lens: the delivered-path error count is built before the seal
def test_an_error_rate_demotion_prints_the_delivered_path_errors_inside_the_window(w, prereg):
    v = delivered_world(w, "error_rate")
    assert v["delivered_errors"] == 2                                  # two inside [arm start, horizon end]; one before, one after, two other
    assert "  delivered-path errors in the horizon: 2\n" in arms.render(arms.analyse(w.cfg, now=NOW))


def test_a_missing_shadow_log_gives_unknown_not_zero(w, prereg):
    v = delivered_world(w, "error_rate", log=False)
    assert v["delivered_errors"] is None
    assert "  delivered-path errors in the horizon: unknown\n" in arms.render(arms.analyse(w.cfg, now=NOW))


def test_another_cause_prints_no_delivered_path_line(w, prereg):
    v = delivered_world(w, "label_fp")
    assert v.get("delivered_errors") is None and "delivered-path" not in arms.render(arms.analyse(w.cfg, now=NOW))


# ------------------------------------------------------------------ round 2 docs-lens: the cap, the unreadable log and the end stamp
def test_a_shadow_log_at_the_size_cap_gives_unknown(w, prereg):
    demoted_world(w, "error_rate")
    pad = shadow.LOG_CAP - 1 - len(COUNTED)
    w.cfg.path("shadow.log").write_bytes(COUNTED.encode() + b"x" * pad)           # LOG_CAP - 1 bytes: still recording, the one line counts
    assert len(w.cfg.path("shadow.log").read_bytes()) == shadow.LOG_CAP - 1 and analysed(w)["delivered_errors"] == 1
    w.cfg.path("shadow.log").write_bytes(COUNTED.encode() + b"x" * (pad + 1))     # exactly LOG_CAP bytes: it has stopped recording
    assert analysed(w)["delivered_errors"] is None


def test_an_unreadable_shadow_log_gives_unknown(w, prereg):
    demoted_world(w, "error_rate")
    log = w.cfg.path("shadow.log")
    log.mkdir()                                                                    # a directory is not a regular file
    assert analysed(w)["delivered_errors"] is None
    log.rmdir()
    os.symlink(w.cfg.path("no-such-target.log"), log)                              # a dangling symlink is refused (O_NOFOLLOW)
    assert analysed(w)["delivered_errors"] is None


def test_the_horizon_end_stamp_is_included_and_one_second_later_is_not(w, prereg):
    demoted_world(w, "error_rate")
    lines = [f"{END} error:delivered:OSError", "2026-11-10T00:00:01Z error:delivered:OSError", "2026-10-31T23:59:59Z error:delivered:OSError"]
    w.cfg.path("shadow.log").write_text("\n".join(lines) + "\n")
    v = analysed(w)
    assert v["horizon_end"] == END and v["delivered_errors"] == 1                  # the end counts; one second after and one before the start do not


# ------------------------------------------------------------------ round 2 docs-lens: a lowering row after the natural end is not the demotion
def test_a_lowering_row_after_the_natural_end_is_not_the_horizons_demotion(w, prereg):
    demoted_world(w, "error_rate", ts="2027-02-10T00:00:00Z")                       # the 12 weeks end 2027-01-24
    w.cfg.path("shadow.log").write_text(COUNTED)
    v = analysed(w, now="2027-02-20T00:00:00Z")
    assert v["demoted_at"] is None and v["demotion_cause"] is None and v["horizon_end"] == "2027-01-24T00:00:00Z" and v["eligible"] == 20
    assert "delivered_errors" not in v
    text = arms.render(arms.analyse(w.cfg, now="2027-02-20T00:00:00Z"))
    assert "horizon ended by demotion" not in text and "delivered-path" not in text
