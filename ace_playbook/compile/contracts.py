"""Contract worlds (docs/design/compiler.md 6): the must-fire / must-not-fire payloads a proposal records.

A proposal may carry an OPTIONAL key `contract` = {"must_fire": [payload, ...], "must_not_fire": [payload, ...]}, each payload
shaped like a `regression_examples` item (tool_name, tool_input, expect, source). `proposal.validate` runs every payload through
the proposal's own check, at propose, validate and approve and when the hook loads the file, so a detector change that breaks a world refuses the proposal
the same way a stale regression example does. `compile propose` copies CONTRACT_WORLDS[detector] into the record (data only:
no detector reads this table, it is not hashed with the catalogue, and the proposal id covers whatever was recorded).
A record without the key keeps today's behaviour. Stdlib only; on the hook path (proposal.py imports it), so it stays small.
"""

from __future__ import annotations

KEY = "contract"
SIDES = {"must_fire": ("fire", 1), "must_not_fire": ("no_fire", 3)}     # side -> (expected verdict, minimum worlds)


def _w(command: str, expect: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "expect": expect, "source": "synthetic"}


# rm_rf_nonscratch v2 (D-204): the evasion classes the detector closes, each with its scratch twin, and the shapes it must not
# trust (a cwd not carried by `&&`, a tainted `||` chain, a dropped variable, a worktree outside its chain, `ls` without -d). Paths are invented.
# The agent scratch tree lives under /private/tmp; its real name is left out because the proposal scrub lints a 13-character
# token that mixes letters and digits, so the worlds use /private/tmp/agent/s/scratchpad.
CONTRACT_WORLDS = {
    "rm_rf_nonscratch": {
        "must_fire": [_w(c, "fire") for c in (
            "rm -rf /w/src",
            "cd /w/src && rm -rf ./*",
            'D=/w/src; rm -rf "$D"',
            "export D=/w/src && rm -rf ${D}/x",
            "cd /tmp/work && cd /w/src && rm -rf ./*",
            "cd && rm -rf ./*",
            "rm -rf $UNSET/x",
            "rm -rf /tmp/work /w/src",
            "bash -c 'cd /w/src && rm -rf ./*'",
            "eval 'rm -rf /w/src'",
            "ls -d /w/src/* | xargs rm -rf",
            "find /w/src -type d -delete",
            "find /w/src -exec rm -rf {} +",
            "git worktree add /w/wt b && rm -rf /w/other",
            "cd /tmp/work; rm -rf ./*",
            "cd /tmp/work || true; rm -rf ./*",
            'D=/tmp/work; unset D; rm -rf "$D"',
            "ls /tmp/work | xargs rm -rf",
            "git worktree add /w/wt b; rm -rf /w/wt",
            "true || cd /tmp/work && rm -rf ./*",
            "printf -vD /w; rm -rf $D",
            "env cd /tmp/work && rm -rf ./*",
        )],
        "must_not_fire": [_w(c, "no_fire") for c in (
            "cd /private/tmp/agent/s/scratchpad && rm -rf ./*",
            "rm -rf /private/tmp/agent/s/scratchpad/out",
            'D=/tmp/work; rm -rf "$D"',
            "export D=/tmp/work && rm -rf ${D}/x",
            "cd /tmp/work && rm -rf .",
            "cd /tmp/work && cd sub && rm -rf ./*",
            "bash -c 'cd /tmp/work && rm -rf ./*'",
            "sh -c 'rm -rf /tmp/work/x'",
            "ls -d /tmp/work/* | xargs rm -rf",
            "find /tmp/work -type d -delete",
            "find /tmp/work -exec rm -rf {} +",
            "git worktree add /w/wt b && rm -rf /w/wt",
            "rm -f /w/src/file.txt",
            "echo rm -rf /w/src",
        )],
    },
}


def contract_for(detector: str) -> dict:
    """{"contract": {...}} for a detector that has worlds, else {} (a copy: a proposal never aliases the table)."""
    worlds = CONTRACT_WORLDS.get(detector)
    return {KEY: {side: [dict(w, tool_input=dict(w["tool_input"])) for w in worlds[side]] for side in SIDES}} if worlds else {}


def id_part(p: dict) -> dict:
    """What `proposal_id` adds to its hashed body: nothing without a contract, so an old record keeps its id."""
    return {KEY: p[KEY]} if KEY in p else {}


def shape(p: dict, payload_err) -> str | None:
    """Schema error of the optional `contract` key, or None. `payload_err(item, keys)` is proposal._payload_err."""
    if KEY not in p:
        return None
    c = p[KEY]
    if not (isinstance(c, dict) and set(c) == set(SIDES)):
        return "contract keys differ from must_fire / must_not_fire"
    for side, (verdict, minimum) in SIDES.items():
        items = c[side]
        if not isinstance(items, list) or len(items) < minimum:
            return f"contract {side} needs at least {minimum} example(s)"
        for x in items:
            if payload_err(x, {"tool_name", "tool_input", "expect", "source"}) or x["expect"] != verdict or x["source"] != "synthetic":
                return "contract example malformed"
    return None


def run(p: dict, fires) -> str | None:
    """Run every contract world through `fires(payload)` (the proposal's own check); None when all behave."""
    c = p.get(KEY)
    for side, (verdict, _minimum) in SIDES.items():
        bad = [i for i, x in enumerate((c or {}).get(side, ())) if fires(x) is (verdict != "fire")]
        if bad:
            return f"contract {side} example(s) {bad} " + ("do not fire" if verdict == "fire" else "fire")
    return None
