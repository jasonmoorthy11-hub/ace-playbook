"""`compile dashboard` (docs/design/compiler.md 8.x): a static HTML page rendered from the status dict and nothing else."""

import json
import re
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import dashboard, status

from . import compile_support as cs
from .test_compile_status import NOW, ROOT, build, labelled_world, pid_of, w  # noqa: F401 - w is a fixture

FAMILIES = (          # verdict, mode, precision, tp, fp, unlabelled, eligible
    ("retired", "disabled", [0.0, 0.2281], 0, 13, 0, 17),
    ("label 3 fires", "observe", "INSUFFICIENT", 2, 1, 3, 4),
    ("warn eligible", "observe", [0.62, 0.97], 11, 1, 0, 9),
    ("needs 5 human TPs (has 3)", "observe", "INSUFFICIENT", 3, 1, 0, 6),
    ("silent: eligible 0", "observe", "INSUFFICIENT", 0, 0, 0, 0),
    ("noisy: add an exception or retire", "observe", [0.19, 0.68], 5, 7, 0, 8),
    ("warn earliest 2026-11-03", "observe", [0.45, 0.99], 6, 0, 0, 5),
    ("warn eligible", "warn", [0.8, 0.95], 20, 2, 0, 12),
    ("label 1 fires", "observe", [0.5, 0.9], 4, 2, 1, 7),
)


def synth(n: int = 4, queue: int = 3, **over) -> dict:
    """An invented status dict: proposal i has the verdict family i (4 for the sample page, 9 for the size test)."""
    props = {}
    for i, (verdict, mode, prec, tp, fp, unlab, elig) in enumerate(FAMILIES[:n]):
        props[f"P-a1b2c3{i:02d}"] = {
            "detector": f"det_{i}", "strength": "strong", "mode": mode, "approved_mode": mode, "approved_at": "2026-10-18T16:10:29Z",
            "rows": tp + fp + unlab, "fires": tp + fp + unlab, "tp": tp, "fp": fp, "unlabelled": unlab, "eligible": elig,
            "fires_per_eligible": 0.5, "precision": prec, "fp_upper_95": None, "warn_earliest": "2026-11-01T16:10:29Z", "verdict": verdict}
    pids = list(props) or ["P-a1b2c300"]
    qs = [{"id": f"{i:08x}", "ts": f"2026-10-20T{i % 12:02d}:00:00Z", "proposal_id": pids[i % len(pids)], "detector": "det_0",
           "session8": "38946dc9", "event": "PreToolUse", "cause": "detector"} for i in range(min(queue, 50))]
    s = {"schema": 1, "generated_at": NOW, "report_sha256": "ab" * 32, "days": 14,
         "measurement": {"start": "2026-10-18T16:10:26Z", "day": 3, "week1_due": "2026-10-25T16:10:26Z", "keyed": True,
                         "chains": {"approvals": "ok", "demotions": "ok", "labels": "ok"}},
         "heartbeat": {"last_hook_row": "2026-10-20T11:00:00Z", "last_live_fire": "2026-10-20T09:00:00Z",
                       "last_offline_scan": "2026-10-19T12:00:00Z", "last_nightly_ok": "2026-10-20T03:00:00Z", "notes": ["silent:det_4"]},
         "proposals": props, "queue": qs, "queue_total": queue,
         "per_day": [{"date": f"2026-10-{d:02d}", "proposal_id": p, "rows": 3 + d % 4} for d in (18, 19, 20) for p in pids],
         "cost": {"p50_ms": 2, "p95_ms": 4, "rows": 13, "threshold_ms": 200}, "agreement": {"live_only": 4, "offline_only": 1, "awaiting_scan": 2},
         "queue_retired": 0,
         "demotions": [{"cause": "source_sha256", "lifted": True, "proposal_id": pids[0], "review_required": False,
                        "to_mode": "disabled", "ts": "2026-10-20T10:44:21Z"}]}
    return {**s, **over}


def svg_of(page: str) -> ET.Element:
    return ET.fromstring(re.search(r"<svg.*?</svg>", page, re.S).group(0))


def by_class(root: ET.Element, cls: str) -> list[ET.Element]:
    return [e for e in root.iter() if cls in e.get("class", "").split()]


# ------------------------------------------------------------------ safety
def test_every_dynamic_string_is_escaped():
    s = synth()
    bad = 'P-<script>alert(1)</script>"x'
    s["proposals"][bad] = s["proposals"].pop("P-a1b2c300")
    s["proposals"][bad]["detector"] = '<img src=x onerror="1">'
    s["heartbeat"]["notes"] = ['<script>alert("n")</script>', 'a"b']
    s["queue"][0]["proposal_id"] = bad
    s["demotions"][0].update(proposal_id=bad, cause="<b>x</b>")
    s["per_day"][0]["proposal_id"] = bad
    out = dashboard.render(s)
    assert "<script" not in out and "<img" not in out and "<b>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;&quot;x" in out and "&lt;script&gt;alert(&quot;n&quot;)" in out and "a&quot;b" in out
    ET.fromstring(re.search(r"<svg.*?</svg>", out, re.S).group(0))          # still well-formed with the hostile id in the chart


def test_both_theme_blocks_and_an_explicit_body_background():
    out = dashboard.render(synth())
    assert "@media (prefers-color-scheme: dark)" in out and ':root:not([data-theme="light"])' in out and ':root[data-theme="dark"]' in out
    assert re.search(r"body\{[^}]*background:var\(--page\)", out)
    for tok in ("#fcfcfb", "#f9f9f7", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#1a1a19", "#0d0d0d", "#f0efec", "#2c2c2a", "#383835",
                "#2a78d6", "#3987e5", "#eb6834", "#d95926", "#1baf7a", "#199e70", "#0ca30c", "#fab219", "#ec835a", "#d03b3b"):
        assert tok in out, tok
    assert out.count("--surface:#1a1a19") == 2                              # dark appears under the media query and under data-theme


def test_a_normal_world_page_loads_nothing_from_outside(w):
    labelled_world(w)
    out = dashboard.render(build(w))
    for bad in ("http://", "https://", "<script", "<link", "@import", "url(", "src="):
        assert bad not in out, bad
    assert '<meta name="viewport"' in out and "overflow-x:auto" in out and 'width="100%"' in out and "viewBox=" in out


def test_the_page_names_every_proposal_and_verdict_of_a_real_world(w):
    labelled_world(w)
    s = build(w)
    out = dashboard.render(s)
    for pid, p in s["proposals"].items():
        assert pid in out and p["verdict"] in out and p["detector"] in out
    assert "label 2 fires" in out and "INSUFFICIENT" in out


# ------------------------------------------------------------------ content
def test_the_hero_shows_the_day_and_the_dates():
    out = dashboard.render(synth())
    assert "day 3 of 7" in out and "2026-10-18T16:10:26Z" in out and "2026-10-25T16:10:26Z" in out and ("ab" * 4) in out
    assert "keyed yes" in out and "chains ok" in out and ("ab" * 32) not in out
    assert "day - of 7" in dashboard.render(synth(measurement={**synth()["measurement"], "day": None, "start": None, "week1_due": None}))
    px = int(re.search(r"\.day\{[^}]*font-size:(\d+)px", out).group(1))
    assert px >= 48


def test_a_real_world_hero_matches_status_day(w):
    s = build(w)
    assert f"day {s['measurement']['day']} of 7" in dashboard.render(s)


def test_a_broken_chain_is_stated_with_a_glyph_and_words():
    s = synth()
    s["measurement"]["chains"]["labels"] = "broken:5"
    out = dashboard.render(s)
    assert "chains BROKEN" in out and "labels broken:5" in out and "✖" in out


def test_the_kpi_tiles_carry_their_numbers():
    out = dashboard.render(synth())
    assert "oldest 12h" in out                                              # queue[0] is 2026-10-20T00:00 against NOW 12:00
    assert "p95 4 ms vs 200 ms" in out
    for part in ("hook 1h", "live fire 3h", "offline scan 24h", "nightly ok 9h"):
        assert part in out, part
    assert "live only 4" in out and "offline only 1" in out and "awaiting scan 2" in out
    over = synth(cost={"p50_ms": 2, "p95_ms": 250, "rows": 13, "threshold_ms": 200})
    assert "p95 250 ms vs 200 ms" in dashboard.render(over)
    assert "live only -" in dashboard.render(synth(agreement="-"))
    assert "awaiting scan -" in dashboard.render(synth(agreement={"live_only": None, "offline_only": None}))
    assert "no rows" in dashboard.render(synth(cost={"p50_ms": "-", "p95_ms": "-", "rows": 0, "threshold_ms": 200}))


@pytest.mark.parametrize("verdict, cls, glyph", [
    ("retired", "muted", "⊘"), ("label 3 fires", "warn", "⚠"), ("silent: eligible 0", "serious", "▲"),
    ("noisy: add an exception or retire", "crit", "✖"), ("warn eligible", "good", "✓"),
    ("needs 5 human TPs (has 3)", "neutral", "◦"), ("warn earliest 2026-11-03", "neutral", "◦"), ("something new", "neutral", "◦"),
    ("not eligible: 1 FPs (warn needs 0)", "serious", "▲"), ("observe only: weak detector", "muted", "⊘")])
def test_verdict_families_pair_a_colour_class_with_a_glyph(verdict, cls, glyph):
    assert dashboard.family(verdict) == (cls, glyph)
    cell = str(dashboard.badge(verdict))
    assert glyph in cell and verdict in cell and f'class="st {cls}"' in cell


def test_the_table_has_one_row_per_proposal_inside_a_scroll_container():
    out = dashboard.render(synth(9))
    table = re.search(r'<div class="scroll"><table class="props">.*?</table>', out, re.S).group(0)
    assert table.count("<tr>") == 10 and "<th>fires/eligible</th>" in table and "[0.62, 0.97]" in table
    assert "INSUFFICIENT" in table and "2/1/3" in table


def test_rows_per_day_queue_demotions_notes_and_footer():
    out = dashboard.render(synth())
    assert "2026-10-19" in out and "id=\"perday\"" in out
    assert "00000000" in out and "38946dc9" in out and "showing 3 of 3" in out
    assert "source_sha256" in out and "disabled" in out
    assert "silent:det_4" in out
    assert NOW in out and "status.json is the single source; nothing here reaches the model" in out


def test_retired_proposals_hidden_from_the_queue_are_counted_on_the_page():
    out = dashboard.render(synth(queue_retired=4))
    assert "showing 3 of 3, oldest first" in out and "4 more on retired proposals (label --queue --all)" in out
    empty = dashboard.render(synth(queue=0, queue_retired=4))
    assert "nothing to label" in empty and "4 more on retired proposals" in empty
    assert "retired proposals" not in dashboard.render(synth())


def test_an_older_status_dict_without_the_new_keys_still_renders():
    s = synth()
    del s["queue_retired"]
    s["agreement"] = {"live_only": 1, "offline_only": 0}
    out = dashboard.render(s)
    assert "awaiting scan -" in out and "retired proposals" not in out


def test_an_empty_world_renders_without_a_chart_row():
    out = dashboard.render(synth(0, queue=0, demotions=[], per_day=[], heartbeat={**synth()["heartbeat"], "notes": []}))
    assert "no proposals" in out and "nothing to label" in out and "none" in out


def test_the_queue_list_is_capped_at_fifty_with_the_total():
    out = dashboard.render(synth(queue=55))
    assert "showing 50 of 55" in out
    assert len(re.findall(r'<tr class="q">', out)) == 50


# ------------------------------------------------------------------ the chart
def test_insufficient_rows_have_no_bar_and_labelled_rows_a_bar_at_the_interval():
    s = synth(9)
    root = svg_of(dashboard.render(s))
    bars = {e.get("data-pid"): e for e in by_class(root, "bar")}
    labelled = {pid for pid, p in s["proposals"].items() if p["precision"] != "INSUFFICIENT"}
    insuf = {pid for pid, p in s["proposals"].items() if p["precision"] == "INSUFFICIENT"}
    assert set(bars) == labelled and insuf
    assert {e.get("data-pid") for e in by_class(root, "insuf")} == insuf
    assert all(e.text == "INSUFFICIENT" for e in by_class(root, "insuf"))
    left, right = dashboard.PAD, dashboard.W - dashboard.PAD
    for pid in labelled:
        lo, hi = s["proposals"][pid]["precision"]
        x, width = float(bars[pid].get("x")), float(bars[pid].get("width"))
        assert x == pytest.approx(left + lo * (right - left), abs=0.01) and x + width == pytest.approx(left + hi * (right - left), abs=0.01)
        assert bars[pid].get("height") == "2"
    pts = {e.get("data-pid"): e for e in by_class(root, "pt")}
    assert set(pts) == labelled and all(float(e.get("r")) >= 4 for e in pts.values())
    p = s["proposals"]["P-a1b2c302"]                                          # warn eligible, [0.62, 0.97], 11 tp 1 fp
    assert float(pts["P-a1b2c302"].get("cx")) == pytest.approx(left + 11 / 12 * (right - left), abs=0.01)
    assert p["precision"] == [0.62, 0.97]


def test_the_chart_has_ticks_thresholds_and_a_legend():
    out = dashboard.render(synth())
    root = svg_of(out)
    texts = [e.text for e in root.iter() if e.tag == "text"]
    assert {"0", ".5", "1", "0.8", "0.9"} <= set(texts)
    thr = by_class(root, "thr")
    assert len(thr) == 2 and all(e.get("stroke-dasharray") for e in thr)
    xs = sorted(float(e.get("x1")) for e in thr)
    assert xs == [pytest.approx(dashboard.PAD + 0.8 * 368, abs=0.01), pytest.approx(dashboard.PAD + 0.9 * 368, abs=0.01)]
    assert "Wilson 95 % interval; point = tp/(tp+fp); INSUFFICIENT below 5 labels" in out


# ------------------------------------------------------------------ the verb
def run(w, *args, capsys):
    code = cli.main(["--config", w.conf, "dashboard", *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_verb_writes_dashboard_html_0600_and_prints_a_relative_line(w, capsys):
    labelled_world(w)
    code, out, err = run(w, capsys=capsys)
    path = w.cfg.path("dashboard.html")
    assert (code, err, out) == (0, "", "wrote dashboard.html in the data dir\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and not list(w.data.glob("dashboard.html.tmp"))
    assert path.read_text().startswith("<!doctype html>") and "day " in path.read_text() and str(w.data) not in path.read_text()


def test_out_names_a_file_elsewhere(w, capsys, tmp_path):
    target = tmp_path / "elsewhere" / "d.html"
    code, out, _ = run(w, "--out", str(target), capsys=capsys)
    assert code == 0 and out == f"wrote {target}\n" and "<svg" in target.read_text()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600 and not w.cfg.path("dashboard.html").exists()


def test_days_is_validated_like_status(w, capsys):
    code, out, err = run(w, "--days", "0", capsys=capsys)
    assert (code, out) == (2, "") and err.startswith("error: --days must be 1..") and not w.cfg.path("dashboard.html").exists()


def test_a_corpus_mixup_refuses_with_the_report_exit_code(w, capsys):
    cs.append_jsonl(w.cfg.path("shadow.jsonl"), {"ts": NOW, "session": "a", "proposal_id": "P-0a1b2c3d", "payload_sha256": "b", "corpus": "synthetic"})
    code, _, err = run(w, capsys=capsys)
    assert code == 2 and err.startswith("error: ")


def test_an_unwritable_out_is_reported_by_type_only(w, capsys, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    code, _, err = run(w, "--out", str(blocker / "d.html"), capsys=capsys)
    assert code == 2 and err.startswith("error: dashboard.html not written (") and str(tmp_path) not in err


def test_the_page_holds_no_session_hash_or_data_path(w):
    labelled_world(w)
    from ace_playbook.compile import state as st
    out = dashboard.render(build(w))
    assert st.session_hash("s1") not in out and str(w.data) not in out and "/" + "Users/" not in out


# ------------------------------------------------------------------ budgets and isolation
def test_the_hook_path_never_imports_the_dashboard():
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import ace_playbook.compile.shadow; "
            "bad = [m for m in ('ace_playbook.compile.dashboard', 'ace_playbook.compile.dashboard_html') if m in sys.modules]; "
            "sys.exit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], cwd=ROOT).returncode == 0


def test_a_nine_proposal_page_stays_under_64_kib():
    assert len(dashboard.render(synth(9, queue=55)).encode()) < 64 * 1024


def test_the_modules_stay_within_their_line_budgets():
    for name in ("dashboard.py", "dashboard_html.py"):
        assert len((ROOT / "ace_playbook" / "compile" / name).read_text().splitlines()) <= 300, name
    assert status.WEEK == 7
