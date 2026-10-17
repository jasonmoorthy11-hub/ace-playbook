"""Synthetic lesson pool and the delta log built from it (no real lesson text)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..store import DeltaLog, make_lesson_id

PROJECTS = ["acme-widgets", "globex-api", "initech-site"]
BASE_TS = (2026, 1, 5)

# (text, applies_when, kind, scope: "global" or index into PROJECTS, user quote)
POOL: list[tuple[str, str, str, str | int, str]] = [
    ("Use python3, not python, when invoking scripts", "running python scripts", "gotcha", "global",
     "please always call python3, plain python is not installed here"),
    ("Prefer uv over pip for installing and running Python tools", "installing python packages", "preference",
     "global", "we use uv for everything, do not use pip in this setup"),
    ("Environment variable changes only take effect after a redeploy", "changing deployment env vars", "gotcha",
     0, "the new value did nothing until we redeployed the service"),
    ("Run the linter before staging files for a commit", "before committing", "workflow", 0,
     "run the linter first, last time the commit bounced in review"),
    ("Migrations must be applied to the staging database before the API deploy", "deploying the api", "rule", 1,
     "apply the migrations to staging before you deploy the api"),
    ("The test suite needs the seed fixtures regenerated after schema changes", "after changing the schema",
     "gotcha", 1, "tests fail until you regenerate the seed fixtures after a schema change"),
    ("Keep the marketing pages free of client-side tracking snippets", "editing marketing pages", "rule", 2,
     "no tracking snippets on the marketing pages, legal asked us to drop them"),
    ("Images for the site must be exported at two sizes", "adding images", "workflow", 2,
     "export every image at two sizes, the small one is for mobile"),
    ("Commit messages use the conventional commits format", "writing commit messages", "preference", "global",
     "use conventional commits for every message"),
    ("Ask before deleting any file outside the working directory", "deleting files", "rule", "global",
     "never delete anything outside this folder without asking me first"),
    ("Cache keys include the tenant id", "writing cache code", "gotcha", 1,
     "the cache key has to include the tenant id or tenants see each other's data"),
    ("The CLI exits non-zero on warnings when strict mode is set", "running the build in CI", "gotcha", 0,
     "strict mode makes the build exit non-zero even for warnings"),
    ("Do not reformat unrelated code in a bug-fix change", "fixing bugs", "preference", "global",
     "keep the diff small, do not reformat code you are not fixing"),
    ("Snapshots are regenerated with the update flag, never edited by hand", "updating snapshot tests", "rule", 2,
     "never hand edit the snapshots, regenerate them with the update flag"),
    ("Background jobs run in a separate worker process", "debugging queued jobs", "fact", 1,
     "the jobs run in a separate worker process, not in the web process"),
    ("Use tabs in Makefiles", "editing Makefiles", "gotcha", "global",
     "makefile recipes need tabs, spaces break them"),
    ("Feature flags default to off in every new environment", "adding a feature flag", "rule", 0,
     "every new flag defaults to off, turn it on per environment"),
    ("Date fields are stored in UTC and formatted only in the UI layer", "handling dates", "rule", 1,
     "store dates in UTC and only format them in the UI layer"),
    ("Pin third-party GitHub Actions to a full commit reference", "editing CI workflows", "preference", "global",
     "pin actions to a full commit reference, tags can move"),
    ("Run database backups before destructive migrations", "running destructive migrations", "rule", 1,
     "take a backup before any destructive migration, always"),
    ("The staging site is rebuilt nightly, so manual edits there are lost", "editing the staging site", "fact", 2,
     "staging is rebuilt every night so manual edits there disappear"),
    ("Bundle-size budgets are enforced in CI", "adding frontend dependencies", "gotcha", 0,
     "ci fails if the bundle grows past the budget, check before adding a dependency"),
    ("Write tests first for bug fixes", "fixing bugs", "workflow", "global",
     "write the failing test first, then fix the bug"),
    ("Log lines must not include customer email addresses", "adding logging", "rule", 1,
     "do not put customer emails in log lines, privacy review flagged it"),
    ("Use the shared date helper instead of calling the date library directly", "formatting dates", "preference", 2,
     "go through the shared date helper, not the date library directly"),
    ("Release tags use a v prefix", "tagging releases", "preference", "global",
     "tag releases with a v prefix like v1.4.0"),
    ("Rate-limit settings live in the gateway config, not the app", "changing rate limits", "fact", 1,
     "rate limits are configured in the gateway config, the app does not do it"),
    ("Dark-mode colors come from design tokens only", "styling components", "rule", 2,
     "dark mode colors must come from the design tokens, no hard coded hex"),
    ("Retry flaky integration tests once before reporting a failure", "running integration tests", "workflow", 0,
     "retry the flaky integration tests once before telling me they failed"),
    ("Never commit generated lockfile changes in a docs-only change", "editing documentation", "rule", "global",
     "docs only changes should not touch the lockfile"),
    ("Check the changelog before bumping a major dependency version", "upgrading dependencies", "workflow",
     "global", "read the changelog before bumping any major version"),
]


def scope_of(item: str | int) -> dict:
    return {"type": "global"} if item == "global" else {"type": "project", "key": PROJECTS[item]}


def _ts(day: int, hour: int = 9) -> str:
    d = datetime(*BASE_TS, hour, tzinfo=timezone.utc) + timedelta(days=day)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_log(rng: random.Random, path: Path) -> dict:
    """Write deltas.jsonl; returns {'ids': [...], 'pinned': [...]} for the manifest."""
    log = DeltaLog(path)
    items = POOL[:]
    rng.shuffle(items)
    ids, day = [], 0
    for text, when, kind, scope_i, quote in items:
        scope = scope_of(scope_i)
        status = rng.choice(["active", "active", "provisional"])
        draft = {"op": "ADD", "lesson_id": make_lesson_id(scope, text), "session_id": f"sess-{day:03d}",
                 "fields": {"text": text, "scope": scope, "kind": kind, "applies_when": when,
                            "status": status,
                            "evidence": [{"quote": quote, "origin": "user_message",
                                          "session_id": f"sess-{day:03d}"}]}}
        log.append(draft, ts=_ts(day))
        ids.append(draft["lesson_id"])
        day += 1
    pinned = _post_ops(rng, log, ids, day)
    return {"ids": ids, "pinned": pinned}


def _post_ops(rng: random.Random, log: DeltaLog, ids: list[str], day: int) -> list[str]:
    state = log.state()
    pinned = rng.sample([i for i in ids if state[i]["status"] == "active"], 10)
    for lid in pinned:
        log.append({"op": "PATCH", "lesson_id": lid, "fields": {"pinned": True}}, ts=_ts(day, 10))
    for lid in rng.sample(ids, 12):
        c = {"shown": rng.randint(1, 9)}
        if rng.random() < 0.5:
            c["cited"] = rng.randint(1, 3)
        if rng.random() < 0.25:
            c["violated"] = 1
        log.append({"op": "NOOP", "lesson_id": lid, "counters": c}, ts=_ts(day, 11))
    rest = [i for i in ids if i not in pinned]
    survivor, dup = rest[0], rest[1]
    log.append({"op": "MERGE", "lesson_id": survivor, "fields": {"merged_from": [dup]}}, ts=_ts(day + 1))
    log.append({"op": "RETIRE", "lesson_id": rest[2],
                "fields": {"reason": "superseded by a newer convention", "to_status": "retired"}},
               ts=_ts(day + 1))
    log.append({"op": "RETIRE", "lesson_id": rest[3],
                "fields": {"reason": "now enforced by a hook", "to_status": "compiled"}}, ts=_ts(day + 1))
    return sorted(pinned)
