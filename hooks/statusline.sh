#!/bin/bash
# Claude Code status-line command: stdin is the status-line JSON; prints ONE line,
#   <model> | ctx <pct>% | ace approved N  unlab24h N  unlabelled N  chains ok|BROKEN  heartbeat <age>
# or `<model> | ctx <pct>% | ace: no status file` when status.json is missing or unreadable. approved = proposals in mode
# observe/warn/block; unlab24h = unlabelled queue rows stamped within 24 h of generated_at, `N+` when the queue
# (50 rows, oldest first) is shorter than queue_total; unlabelled = queue_total; heartbeat = age of last_hook_row against the clock.
# status.json is found like the other hooks do: $ACE_PLAYBOOK_CONFIG (else the default config file), its data_dir. One
# `python3 -I -S` with stdlib json only: no package import, no uv, no network. Always exits 0.
PY="${ACE_PYTHON:-python3}"
"$PY" -I -S -c '
import calendar, json, math, os, sys, time
def jload(f):
    try:
        v = json.load(f)
    except Exception:
        return {}
    return v if isinstance(v, dict) else {}
def sub(d, k):
    return d.get(k) if isinstance(d.get(k), dict) else {}
def ts(s):
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return None
def age(t, now):
    secs = max(0, now - t) if t is not None else None
    return "never" if secs is None else "%dm" % (secs // 60) if secs < 3600 else "%dh" % (secs // 3600) if secs < 172800 else "%dd" % (secs // 86400)
inp, home = jload(sys.stdin), os.path.expanduser("~")
model, pct = sub(inp, "model").get("display_name"), sub(inp, "context_window").get("used_percentage")
ok_pct = isinstance(pct, (int, float)) and not isinstance(pct, bool) and math.isfinite(pct)
model = "".join(c for c in model if 31 < ord(c) != 127) if isinstance(model, str) else ""
head = "%s | ctx %s%% |" % (model or "claude", int(round(pct)) if ok_pct else "?")
try:
    with open(os.environ.get("ACE_PLAYBOOK_CONFIG") or os.path.join(home, ".config", "ace-playbook", "config.json")) as f:
        cfg = jload(f)
except Exception:
    cfg = {}
data = cfg.get("data_dir")
data = os.path.expanduser(data) if isinstance(data, str) and data else os.path.join(home, ".local", "share", "ace-playbook")
try:
    with open(os.path.join(data, "status.json")) as f:
        s = jload(f)
except Exception:
    s = {}
if not s:
    sys.exit(print(head, "ace: no status file"))
q = s.get("queue") if isinstance(s.get("queue"), list) else []
total = s.get("queue_total") if isinstance(s.get("queue_total"), int) else len(q)
gen = ts(s.get("generated_at"))
fires = sum(1 for r in q if gen is not None and isinstance(r, dict) and (ts(r.get("ts")) or 0) > gen - 86400)
approved = sum(1 for p in sub(s, "proposals").values() if isinstance(p, dict) and p.get("mode") in ("observe", "warn", "block"))
ok = all(sub(sub(s, "measurement"), "chains").get(k) == "ok" for k in ("approvals", "demotions", "labels"))
print("%s ace approved %d  unlab24h %d%s  unlabelled %d  chains %s  heartbeat %s" % (head, approved, fires, "+" if total > len(q) else "",
      total, "ok" if ok else "BROKEN", age(ts(sub(s, "heartbeat").get("last_hook_row")), int(time.time()))))
' 2>/dev/null || echo "ace: status line unavailable"
exit 0
