#!/bin/bash
# SwiftBar plugin (the ".5m." in the file name is the 5-minute refresh): ACE in the menu bar, read-only. Line 1 is a glyph and the counts
# (● ok, ◐ needs you, ○ stale), then "---", the ACE and Watch lines, up to three proposal titles and two buttons. status.json is found
# like hooks/statusline.sh does: $ACE_PLAYBOOK_CONFIG (else the default config file), its data_dir. One `python3 -I -S`, stdlib only,
# no network, no model. Item text is cleaned (no "|", no control characters, 80 characters). Always exits 0.
PY="${ACE_PYTHON:-python3}"
"$PY" -I -S -c '
import calendar, json, os, sys, time, unicodedata
from urllib.parse import quote
def clean(t, n=80):
    t = t if isinstance(t, str) else ""
    return "".join(c for c in t if c >= " " and c not in "|\x7f" and unicodedata.category(c) not in ("Cf", "Cc", "Zl", "Zp"))[:n]
def num(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10 ** 6 else 0
def sub(d, k):
    return d.get(k) if isinstance(d.get(k), dict) else {}
def ts(s):
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return None
def age(t, now):
    secs = None if t is None else max(0, now - t)
    return "never" if secs is None else "%dm" % (secs // 60) if secs < 3600 else "%dh" % (secs // 3600) if secs < 172800 else "%dd" % (secs // 86400)
def arg(v):
    return "\"%s\"" % v if " " in v else v
def load(path):
    with open(path) as f:
        v = json.load(f)
    if not isinstance(v, dict):
        raise ValueError("not an object")
    return v
sys.stdout.reconfigure(encoding="utf-8")
home, now = os.path.expanduser("~"), int(time.time())
env = os.environ.get("ACE_PLAYBOOK_CONFIG")
conf = env or os.path.join(home, ".config", "ace-playbook", "config.json")
try:
    try:
        data = load(conf).get("data_dir")
    except Exception:
        data = None
    data = os.path.expanduser(data) if isinstance(data, str) and data else os.path.join(home, ".local", "share", "ace-playbook")
    path = os.path.join(data, "status.json")
    s = load(path) if os.path.exists(path) else {}
    gen, hb = ts(s.get("generated_at")), sub(s, "heartbeat")
    beat = max([ts(hb.get(k)) or 0 for k in ("last_hook_row", "last_offline_scan", "last_nightly_ok")])
    opened, leads, unl = num(s.get("watch_open")), num(s.get("watch_leads")), num(s.get("queue_total"))
    chains = all(sub(sub(s, "measurement"), "chains").get(k) == "ok" for k in ("approvals", "demotions", "labels"))
    if gen is None or now - gen > 36 * 3600:
        glyph, tail = "○ stale", ""
    elif opened or unl:
        glyph, tail = "◐ needs you", "  watch %d  unlabelled %d" % (opened, unl)
    else:
        glyph, tail = ("● ok", "  watch 0  unlabelled 0") if now - beat <= 36 * 3600 else ("○ stale", "")
    repo = os.environ.get("ACE_PLAYBOOK_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(sys.argv[1]))))
    cmd = ["uv", "run", "--directory", repo, "python", "-m", "ace_playbook.compile"] + (["--config", env] if env else []) + ["watch", "queue"]
    out = [glyph + tail, "---", "ACE  unlabelled %d  chains %s  heartbeat %s" % (unl, "ok" if chains else "BROKEN", age(beat or None, now)) if s
           else "ACE  no status file",
           "Watch  open %d  leads %d  oldest %s" % (opened, leads, age(ts(s.get("watch_oldest")), now))]
    titles = s.get("watch_titles") if isinstance(s.get("watch_titles"), list) else []
    out += ["- " + clean(t) for t in titles[:3] if clean(t)]
    out += ["---", "Open panel | href=file://" + quote(os.path.join(data, "dashboard.html")),
            "Review queue | bash=/usr/bin/env " + " ".join("param%d=%s" % (i, arg(a)) for i, a in enumerate(cmd, 1)) + " terminal=true"]
    print("\n".join(out))
except Exception as exc:
    print("○ ACE: no status\n---\nerror: " + type(exc).__name__)
' "$0" 2>/dev/null || printf '○ ACE: no status\n---\nerror: python3 unavailable\n'
exit 0
