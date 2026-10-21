"""`compile dashboard` (docs/design/compiler.md 8.x): one self-contained HTML page rendered from the status dict and nothing else.

`render` is pure: it takes `status.build`'s dict and returns the page. The only figure computed here is the chart's point estimate
`tp/(tp+fp)`; every interval, rate and verdict is read from the dict, so the page cannot disagree with `compile status`. Every dynamic
string goes through `esc` (`html.escape`, quotes included); a cell is escaped unless it is a `Raw` built here. No script, font, image or
link is loaded from outside. Nothing reaches a model and the shadow hook never imports this module.
Imports: stdlib, `..atomic`, `..config`, `.dashboard_html`, `.labels`, `.status`.
"""

from __future__ import annotations

import sys
from html import escape
from pathlib import Path

from ..atomic import write_private
from ..config import Config
from . import dashboard_html, labels, metrics, status

OUT, CAP, STALE_HOURS = "dashboard.html", status.QUEUE_MAX, 36
THRESHOLDS, TICKS = ((0.8, "0.8"), (0.9, "0.9")), ((0.0, "0"), (0.5, ".5"), (1.0, "1"))
FAMILIES = (("retired", "muted", "⊘"), ("label", "warn", "⚠"), ("silent", "serious", "▲"), ("noisy", "crit", "✖"),
            ("not eligible", "serious", "▲"), ("observe only", "muted", "⊘"), ("warn only", "muted", "⊘"),
            ("warn eligible", "good", "✓"), ("block eligible", "good", "✓"), ("needs", "neutral", "◦"), ("warn earliest", "neutral", "◦"),
            ("block earliest", "neutral", "◦"), ("block (top rung)", "neutral", "◦"))      # the rungs of status.verdict (measure.NEXT) and block
LEGEND = "Wilson 95 % interval; point = tp/(tp+fp); INSUFFICIENT below 5 labels"
FOOT = "status.json is the single source; nothing here reaches the model"
PROP_HEADS = ("detector", "proposal", "mode", "strength", "fires/eligible", "tp/fp/unlab", "precision", "verdict")


class Raw(str):
    """Markup this module built from escaped parts; any other cell is escaped when it is placed."""


def esc(v: object) -> str:
    return escape(str(v), quote=True)


def family(verdict: str) -> tuple[str, str]:
    """(colour class, glyph) of a verdict; the first family whose word starts it, else neutral."""
    return next(((cls, g) for word, cls, g in FAMILIES if verdict.startswith(word)), ("neutral", "◦"))


def glyph(cls: str, g: str, text: str) -> Raw:
    """A status glyph beside its words: colour is never the only signal."""
    return Raw(f'<span class="st {cls}" aria-hidden="true">{g}</span> {esc(text)}')


def badge(verdict: str) -> Raw:
    return glyph(*family(verdict), verdict)


def attr(name: str, value: str) -> str:
    return f' {name}="{esc(value)}"' if value else ""


def table(heads: tuple, rows: list[list], cls: str = "", ident: str = "", row_cls: str = "") -> Raw:
    """A table in its own scroll container so a narrow screen scrolls the table, not the page."""
    cell = lambda c: c if isinstance(c, Raw) else esc(c)  # noqa: E731
    head = "".join(f"<th>{esc(h)}</th>" for h in heads)
    body = "".join(f"<tr{attr('class', row_cls)}>" + "".join(f"<td>{cell(c)}</td>" for c in r) + "</tr>" for r in rows)
    return Raw(f'<div class="scroll"><table{attr("class", cls)}{attr("id", ident)}><tr>{head}</tr>{body}</table></div>')


def section(title: str, inner: str) -> str:
    return f"<h2>{esc(title)}</h2>\n{inner}\n"


def num(v: object, dash: str = "-") -> str:
    return dash if v is None else str(v)


def pct(v: float) -> float:
    """A 0..1 value as a percentage of the track, clamped."""
    return max(0.0, min(1.0, v)) * 100


def point(p: dict) -> float | None:
    """The point estimate tp/(tp+fp); None when nothing is labelled."""
    r = metrics.ratio(p["tp"], p["tp"] + p["fp"], digits=4)
    return None if isinstance(r, str) else r


def wrow(pid: str, p: dict) -> str:
    """One chart row: the label, then a track holding the threshold hairlines and a Wilson bar with round ends and a point marker."""
    who = esc(pid)
    thr = "".join(f'<div class="thr" style="left:{pct(v):.0f}%"></div>' for v, _ in THRESHOLDS)
    if isinstance(p["precision"], str):
        inner, tip = f'<span class="insuf" data-pid="{who}">{esc(p["precision"])}</span>', p["precision"]
    else:
        lo, hi = p["precision"]
        inner = (f'<div class="bar" data-pid="{who}" style="left:{pct(lo):.2f}%;width:{pct(hi) - pct(lo):.2f}%"></div>'
                 f'<div class="cap" data-pid="{who}" style="left:calc({pct(lo):.2f}% - 2px)"></div>'
                 f'<div class="cap" data-pid="{who}" style="left:calc({pct(hi):.2f}% - 2px)"></div>')
        if (pt := point(p)) is not None:
            inner += f'<div class="point" data-pid="{who}" style="left:calc({pct(pt):.2f}% - 4px)"></div>'
        tip = f"[{lo}, {hi}]"
    return (f'<div class="wrow"><div class="wl">{who}<small>{esc(p["detector"])}</small></div>'
            f'<div class="track" title="{who} {esc(tip)}">{thr}{inner}</div></div>')


def chart(props: dict) -> Raw:
    """The one chart, as HTML rows so its text keeps the page's type size: a Wilson interval per proposal on a 0 to 1 track."""
    if not props:
        return Raw('<p class="sub">no proposals</p>')
    ticks = "".join(f'<span class="tick" style="left:{pct(v):.0f}%;transform:translateX(-{pct(v) if v else 0:.0f}%)">{t}</span>' for v, t in TICKS)
    marks = "".join(f'<span class="tick top" style="left:{pct(v):.0f}%;transform:translateX(-50%)">{t}</span>' for v, t in THRESHOLDS)
    axis = f'<div class="wrow"><div class="wl"></div><div class="axis">{marks}{ticks}</div></div>'
    rows = "".join(wrow(pid, p) for pid, p in props.items())
    return Raw(f'<div class="card chart">{axis}{rows}<p class="legend"><span class="sw"></span>{esc(LEGEND)}</p></div>')


def hero(s: dict) -> str:
    m, c = s["measurement"], s["measurement"]["chains"]
    broken = [f"{k} {v}" for k, v in c.items() if v != "ok"]
    chains = glyph("crit", "✖", "chains BROKEN: " + ", ".join(broken)) if broken else glyph("good", "✓", "chains ok")
    return (f'<section class="card hero"><div class="day">day {esc(num(m["day"]))} of {status.WEEK}</div>'
            f'<p class="sub">started {esc(num(m["start"]))} · week 1 due {esc(num(m["week1_due"]))}</p>'
            f'<p>keyed {"yes" if m["keyed"] else "no"} · {chains} · report {esc(s["report_sha256"][:8])}</p></section>')


def tile(key: str, value: object, lines: list, mark: Raw | None = None) -> str:
    rows = "".join(f'<div class="line">{c if isinstance(c, Raw) else esc(c)}</div>' for c in ([mark] if mark else []) + lines)
    big = f'<div class="v">{esc(value)}</div>' if value != "" else ""
    return f'<div class="card tile"><div class="k">{esc(key)}</div>{big}{rows}</div>'


def late(ts: object, now: str) -> bool:
    """True for a heartbeat that never happened or is over `STALE_HOURS` old (the digest's rule)."""
    secs = status.seconds_since(ts, now)
    return secs is None or secs > STALE_HOURS * 3600


def tiles(s: dict) -> str:
    now, h, c, a = s["generated_at"], s["heartbeat"], s["cost"], s["agreement"] if isinstance(s["agreement"], dict) else {}      # "-" when not computable
    q = s["queue_total"]
    oldest = f"oldest {status.age(s['queue'][0]['ts'], now)}" if s["queue"] else "nothing to label"
    p95, limit = c["p95_ms"], c["threshold_ms"]
    ok = isinstance(p95, (int, float))                                  # "-" when no row has a cost
    cost = glyph("neutral", "◦", "no rows") if not ok else glyph("good", "✓", "within") if p95 <= limit else glyph("crit", "✖", "over")
    ages = [("hook", h["last_hook_row"]), ("live fire", h["last_live_fire"]), ("offline scan", h["last_offline_scan"]), ("nightly ok", h["last_nightly_ok"])]
    stale = [n for n, ts in ages if n != "live fire" and late(ts, now)]       # a live fire may be days old; a hook row may not
    beat = glyph("serious", "▲", "stale: " + ", ".join(stale)) if stale else glyph("good", "✓", "alive")
    return ('<div class="tiles">'
            + tile("queue", q, [oldest], glyph("good", "✓", "clear") if not q else glyph("warn", "⚠", "label them"))
            + tile("hook cost", f"{num(p95)} ms", [f"p95 {num(p95)} ms vs {limit} ms"], cost)
            + tile("heartbeat", "", [f"{n} {status.age(ts, now)}" for n, ts in ages], beat)
            + tile("agreement", "", [f"live only {num(a.get('live_only'))}", f"offline only {num(a.get('offline_only'))}",
                                     f"awaiting scan {num(a.get('awaiting_scan'))}"])
            + "</div>")


def proposals(s: dict) -> Raw:
    rows = [[p["detector"], pid, p["mode"], num(p["strength"]), f"{num(p['fires'])}/{num(p['eligible'])}", f"{p['tp']}/{p['fp']}/{p['unlabelled']}",
             p["precision"] if isinstance(p["precision"], str) else "[{}, {}]".format(*p["precision"]), badge(p["verdict"])]
            for pid, p in s["proposals"].items()]
    return table(PROP_HEADS, rows, "props")


def queue(s: dict) -> str:
    q, now, hidden = s["queue"][:CAP], s["generated_at"], s.get("queue_retired", 0)
    more = f'<p class="sub">{hidden} more on retired proposals (label --queue --all)</p>' if hidden else ""
    if not q:
        return '<p class="sub">nothing to label</p>' + more
    rows = [[r["id"], r["detector"], r["proposal_id"], r["session8"], r["ts"], status.age(r["ts"], now)] for r in q]
    heads = ("id", "detector", "proposal", "session", "ts", "age")
    return f'<p class="sub">showing {len(q)} of {s["queue_total"]}, oldest first</p>' + table(heads, rows, "queue", row_cls="q") + more


def render(s: dict) -> str:
    """The page for status dict `s`."""
    perday = [[r["date"], r["proposal_id"], r["rows"]] for r in s["per_day"]] or [["none", "-", 0]]
    dem = [[r["ts"], r["proposal_id"], r["to_mode"], r["cause"], "yes" if r.get("lifted") else "no"] for r in s["demotions"]]
    notes = "".join(f"<li>{esc(n)}</li>" for n in s["heartbeat"]["notes"])
    body = (hero(s) + tiles(s) + section("Proposals", proposals(s)) + section("Wilson precision per proposal", chart(s["proposals"]))
            + section(f"Rows per day, last {s['days']} days", table(("date", "proposal", "rows"), perday, ident="perday"))
            + section("Unlabelled queue", queue(s))
            + section("Demotions", table(("ts", "proposal", "to", "cause", "lifted"), dem, "dem") if dem else '<p class="sub">none</p>')
            + section("Heartbeat notes", f'<ul class="notes">{notes}</ul>' if notes else '<p class="sub">none</p>')
            + f"<footer>generated {esc(s['generated_at'])} · {esc(FOOT)}</footer>")
    return dashboard_html.page(body)


def add_parser(sub) -> None:
    """Register `dashboard` on the CLI's subparsers."""
    p = sub.add_parser("dashboard", help="a static HTML page from status (read-only; writes dashboard.html, 0600)")
    p.add_argument("--days", type=int, default=14, help="days in the report window (default 14, 1 to 3660)")
    p.add_argument("--out", metavar="PATH", help="where to write the page (default: dashboard.html in the data dir; 0600)")
    p.set_defaults(run=lambda c, a: run(c, a.days, a.out))


def run(cfg: Config, days: int = 14, out: str | None = None) -> int:
    """Entry of `compile dashboard`: build the status, write the page (0600, atomic), print where it went. Exit 0; 2 for a refusal or a failed write."""
    try:
        s = status.build(cfg, days)
    except labels.Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    path = Path(out) if out else cfg.path(OUT)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private(path, render(s))
    except OSError as exc:                                          # type only: an errno message would carry a path
        print(f"error: dashboard.html not written ({type(exc).__name__})", file=sys.stderr)
        return 2
    print(f"wrote {out}" if out else f"wrote {OUT} in the data dir")
    return 0
