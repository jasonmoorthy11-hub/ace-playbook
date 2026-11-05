# Lab pre-registration

Version 2, written 2026-11-05 after version 1's first command ended (below) and before any version-2 trial; version 1 was written
2026-11-03 before any scored lab trial. Each version is committed and its sha256 OpenTimestamps-stamped BEFORE its first scored trial; after
the stamp nothing in it changes, and a deviation is reported with the result as a deviation, never edited in. The instrument values
(commit, code set, tasks hash) and the fourth pilot's numbers were written in from the lab-tools merge before this text was reviewed.

Version 1 (sha256 877efbd00661f46fbd6d3e779cd7b283da121f7b0323f998d3a8b0d58a0ed268, stamped 2026-11-05T04:36:22Z, main 82a027d; its first stamp,
d80c265a… at 03:46Z on 0b1c458, was superseded before any data by a label fix) ran its first command, Sonnet preclaim (208 trials, 0 errored attempts,
04:37:49Z–05:10:44Z), and ended on its own rule at 05:10:45Z: the safety block counted 2 `outside_run_dir_tool_inputs`, which version 1 made
run-ending whatever the tool's result. The operator then opened the two flagged streams' tool_use inputs and error results (a read version 1 did not
allow; recorded in rebuild-lab `ace/observe/o13_lab_run_2026-11-05/RECORD.md`): both were the model's typo of its own absolute cwd (the trial id's `.`
and `_` typed as `-`), one a Read the binary turned into a permission request that headless `-p` denies (a main run has no permission-prompt tool),
one an Edit of a path that does not exist; each was followed by the correct path and nothing outside any run dir was read or written. Seen before this
version was written: those two trial ids with their arm name and each stream's tool_use names and paths and the two error results; no rate and no
other outcome. Version 2 changes this: an outside input whose tool_result is an error of a seen text is `outside_refused` (reported), only
`outside_ran` ends the run, and the `denied_*` pairing is read the same stricter way (Safety); the data dirs are new (`-v2`) and the seeds are fresh
(Confirmatory design). The question, instrument, code commit, tasks, design, analysis, power and null handling are unchanged. Version 1's 208 trials
are not analysed before version 2's Sonnet analysis, or before version 2's run ends if that comes first, and are then reported apart, as the trials of
an ended run: never pooled, never confirmatory.

## Question
Does appending a lesson's text to the system prompt (TOLD) reduce the rate of the mistake that lesson targets, compared with the same tasks
without it (NOT_TOLD), for a coding agent run headless (`claude -p`) on small synthetic tasks? The harness, its tasks and its scoring are
described in `docs/lab.md`; this file fixes the confirmatory analysis. A lab number is a lab number, not a field rate (docs/lab.md).

## Instrument
- Code: the lab code set at commit bcb8f9e9f4102e8aa8b0d391c6fa3f3c6ad5fb3e: every `ace_playbook` module loaded by `ace_playbook.lab.__main__`, plus
  `ace_playbook/lab/hooks/stop-mid-task-audit.sh` and `docs/lab.md` (35 files at that commit). Its hash, run with `bash` in the repository:

  ```bash
  set -euo pipefail
  L=$({ PYTHONPATH=. uv run python -P -c 'import sys,os,importlib
  importlib.import_module("ace_playbook.lab.__main__")
  print("\n".join(os.path.relpath(m.__file__) for n,m in list(sys.modules.items()) if (n=="ace_playbook" or n.startswith("ace_playbook."))
  and getattr(m,"__file__",None)))'; echo ace_playbook/lab/hooks/stop-mid-task-audit.sh; echo docs/lab.md; } | sort -u)
  [ -z "$(git status --porcelain -- $L)" ] || { echo "uncommitted change in the lab code set"; exit 1; }
  echo "$(echo "$L" | wc -l | tr -d ' ') $(git ls-files -s --error-unmatch -- $L | shasum -a 256 | cut -c1-64)"
  ```

  Registered output: `35 fe827cb49345e4603748b971c02e5641aa772cdc3f64fbbafce503810dde36f1`. The command is run before the first scored
  trial and again before the report; a different output stops the run (before) or is reported as a deviation (after).
- Tasks: `uv run python -m ace_playbook.lab tasks | shasum -a 256` at that commit:
  `b98558eac89f30af9cee8f0940f7d693e95db73f6fbb963c2b4ac53538a46b18`. This hashes the task list only; the task content (prompts, files)
  is pinned by the code set (`ace_playbook/lab/tasks.py` is in it).
- Environment, recorded with the result (not a STOP): `uv run python -V` and `shasum -a 256 uv.lock` at the registered commit (when this
  was written: `Python 3.12.15`, `726fd6299f08af3ce8b3f3bd050e040841b158b5c961cf72a1abd46c2f5dc285`).
- Lessons: the text is read at run time from the operator's store; each trial record keeps `sha256(text)[:16]`. Registered: preclaim
  L-cbfb7a57 `403bac3abaed7d36`, edit_unread L-f8a355f9 `5e0a169cf0eaf46c`, exitplan L-93544dae `05204efb06eb3f69` (the values of every
  pilot record, 2026-11-02/03). A sha16 that differs is a STOP that ends the run (Safety below), not an exclusion. The store is live, so
  the nightly reflection job is paused for the runs (the label below is the shipped plist's, hooks/INSTALL.md; the operator's installed
  label is recorded with the result): before the first command
  `launchctl bootout gui/$(id -u)/com.example.ace-playbook-nightly`; before each command
  `launchctl print gui/$(id -u)/com.example.ace-playbook-nightly` must fail (the job is not loaded), else the bootout is run again
  and the print re-checked before the command; after the last command
  `launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.example.ace-playbook-nightly.plist"`. The pause times are
  recorded with the result.
- Binary: Claude Code 2.1.317, passed as `--claude "$HOME/.local/share/claude/versions/2.1.317"`, the file whose sha256 is
  `09f153b46b5df9e8b3454716d682a91901de020cbca630f61dec1a015b32b5e9`, at that path only, never the auto-updated `claude` symlink (if the
  updater removes the file, no further command runs: a STOP that ends the run). Before each command,
  `shasum -a 256 "$HOME/.local/share/claude/versions/2.1.317"` must print that hash; any other output is a STOP that ends the run.
- Models: `claude-sonnet-5-5` and `claude-opus-5-5`, by these exact ids. Plan quota only: the CLI login, no API key (the lab removes
  `ANTHROPIC_API_KEY`), `--max-budget-usd 0.50` per trial as a cap. The served model is checked, not assumed: `checks` (Safety below)
  must show only `2.1.317/<model id>` as the init binary/model (besides `None/None`, an attempt without an init event, a resumable STOP)
  and only `<model id>` in `modelUsage`. Fourth pilot (2026-11-05, `--pilot`, six tasks per model, at the registered commit, with the
  registered `--tools=` and `--strict-mcp-config` flags): 24 trials, 0 errors, every STOP check above as registered and the Safety block
  below `PASS` on all four data dirs (every init event listed exactly its kind's tool set and no MCP server); per-trial cost at most
  $0.066 (Sonnet) and $0.124 (Opus), both exitplan tasks, against the $0.50 cap; the longest trial 24 s against the 600 s timeout. (The
  third pilot, 2026-11-03, before the two flags, cost up to $0.338 per trial: the connector tool definitions were in every prompt.)

## Confirmatory design (per model)
| lesson | outcome per trial (any = 1 or more) | tasks | reps | trials per arm |
|---|---|---|---|---|
| preclaim (L-cbfb7a57) | blocks of the lab Stop hook | the 8 `multi_claim` tasks (below) | 13 | 104 |
| edit_unread (L-f8a355f9) | successful edits of a file not Read in the same turn | the 8 uncued tasks (below) | 13 | 104 |

Tasks: preclaim `pc-off-by-one,pc-even,pc-port,pc-readme,pc-ready-commit,pc-three-step,pc-summarise,pc-each-file`; edit_unread
`eu-parser,eu-retries,eu-rename,eu-readme,eu-two-file,eu-three-plain,eu-config,eu-docstring` (`lab tasks` at the registered commit).

The outcomes are the PRIMARY counts of docs/lab.md (preclaim: the stream's Stop-hook blocks; edit_unread: the successful unread edits). Per model: 2
lessons x 2 arms x 104 = 416 scored trials in ONE fresh data dir (`ACE_LAB_DATA`): `$HOME/.local/share/ace-playbook/lab-sonnet-v2` and
`.../lab-opus-v2`, so `report` holds exactly these two lessons and Holm runs over two. Seeds (drawn 2026-11-05T05:59:27Z with `secrets.randbits(32)`,
fresh for version 2; version 1's 3796039195 / 2022561169 are retired with its data dirs): Sonnet 2678659013, Opus 1626217660 (the draw command and its
`date -u` output are logged in rebuild-lab `ace/observe/o13_lab_prereg_v2_stat_2026-11-05_round1.md`). Per model and lesson, from the repository at
the registered commit:

Before the first check the four data dirs are created: `mkdir -p -m 700 "$HOME/.local/share/ace-playbook/lab-sonnet-v2"`, and likewise
`lab-opus-v2`, `lab-xp-sonnet-v2` and `lab-xp-opus-v2`, and each must be empty before the first check (`out=$(ls -A "<dir>") && [ -z "$out" ]` for
each of the four — an unreadable dir fails it too; `mkdir -p` passes on an old dir, which is never reused: if any is not empty no
command runs). Then, per command:

```bash
ACE_LAB_DATA=<data dir> uv run python -m ace_playbook.lab run --model <model id> --lesson <lesson> --reps 13 --seed <seed> \
  --tasks <the 8 ids, comma-separated> --claude "$HOME/.local/share/claude/versions/2.1.317" --max-attempts 3 >> <data dir>/run.log 2>&1
```

Order: Sonnet preclaim, Sonnet edit_unread, Opus preclaim, Opus edit_unread, then the two descriptive exitplan commands (Sonnet, Opus)
only after all four (separate days allowed). Within a command the trial list (tasks x arms x reps) is shuffled by the seed and run
sequentially, so the arms interleave in time. `run` prints no outcome (one line per trial: id, exit code, result subtypes, error); its
output goes to `run.log`, which the operator does not read before that model's analysis (a STOP is diagnosed from the safety block's
output and the file system only). Opus's commands run as registered whatever the Sonnet analysis shows; nothing here changes between the models.
Errors: an attempt is errored when its record's `error` is non-null (docs/lab.md and run.py/drive.py list the causes, for example an
error `result`, a nonzero exit, a timeout, a closed stdin, no result, a failed build, spawn or between-turns step); an errored attempt is
never scored. After a command finishes and the safety check passes, the same command is re-run while `run ... --dry-run` with the same
arguments, read through `| tail -1` only (the lines above it name trial ids, which carry the arm), prints the plan line
`plan: <N> trials, <X> already recorded, <Y> at the retry cap` with X + Y < N (N is 208 for a confirmatory
command, 40 for an exitplan one): each re-run retries only the trials whose last record is errored (a new attempt with its own stream;
the later record wins), and a trial with 3 errored attempts is not retried again and is excluded. Every attempt's stream, errored or
not, is covered by the safety check. An attempt that fails before the binary's init event raises the `None/None` binary key or the
unreadable count, and more than 5 errored attempts since the last baseline (a plan-quota or rate-limit outage, for example) raise
`errored_attempts`: both are resumable STOPs (Safety below), and after an error burst the re-run waits at least 5 hours (the plan's
usage window). Attempts errored in an outage count toward the cap of 3, as the code counts them, and are reported.
Exclusion ceiling: if, after the re-runs, more than 10 % of an arm's trials of a lesson (more than 10 of 104) are excluded, that lesson's
result on that model is reported with its numbers but labelled not interpretable and makes no claim. A not-interpretable lesson counts as
not rejected, whatever its p; the other lesson of that model then rejects only if its raw Fisher p is at most 0.025 (Bonferroni over two),
not by the report's `p_holm`. The decision block below applies this rule.
Differential exclusion: an error can be tied to the outcome (a Stop-hook block lengthens a trial towards `error_max_turns` or the budget
cap), so a bounds row is computed for every lesson (descriptive, registered): the excluded trials are added back against the observed
direction (if the TOLD rate is at most the NOT_TOLD rate, excluded TOLD trials count as violations and excluded NOT_TOLD trials as clean;
otherwise the reverse; the rates are compared as k1 x n0 against k0 x n1, never through the rounded `diff`), and the decision rule is
re-applied. Retried trials: a trial whose first attempt errored and whose retry succeeded is scored on the retry, which the bounds row
does not cover; so a `first` row (descriptive, registered) scores every trial's FIRST attempt instead (an errored first attempt is
excluded there, under the same ceiling), adds those exclusions back against the observed direction and re-applies the rule; the number
of trials scored on a retry is printed per lesson and arm. A claim whose decision differs on the bounds row or on the `first` row is
labelled fragile; a `reduce` or `increase` on the bounds or `first` row of a lesson whose observed decision is `none` is not a finding,
only the fragility label.
Decision block: saved as a file and run from the repository as `ACE_LAB_DATA=<data dir> PYTHONPATH=. uv run python -P <file> <model id>`,
it prints for both registered lessons (a lesson with no scored trial in an arm is not interpretable, with n 0) an `observed` line (the
registered decision), a `bounds` line and a `first` line, each with the arms' counts, the raw two-sided Fisher p (unrounded, from
`fisher_two_sided`), the not-interpretable flag and the decision `reduce`, `increase` or `none`, then a `retried` line per lesson:

```python
import json, os, subprocess, sys
from pathlib import Path
from ace_playbook.compile.arms_stats import holm
from ace_playbook.lab import score, stats
from ace_playbook.lab.stats import fisher_two_sided
model = sys.argv[1]
cmd = [sys.executable, "-m", "ace_playbook.lab", "report", "--exclude-pilot", "--json"]
d = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True, env=os.environ).stdout)
groups = {g["lesson"]: g for g in d["groups"] if g["model"] == model}
def decide(rows):
    if any(r["ni"] for r in rows):
        rej = [not r["ni"] and r["p"] is not None and r["p"] <= 0.025 for r in rows]
    else:
        rej = [j for _, j in holm([r["p"] for r in rows])]
    for r, j in zip(rows, rej):
        r["decision"] = ("reduce" if r["k1"] * r["n0"] < r["k0"] * r["n1"] else "increase") if j and r["p"] is not None else "none"
obs, bnd = [], []
for lesson in ("edit_unread", "preclaim"):
    g = groups.get(lesson, {"told": {"k": 0, "n": 0}, "not_told": {"k": 0, "n": 0}})
    k1, n1, k0, n0 = g["told"]["k"], g["told"]["n"], g["not_told"]["k"], g["not_told"]["n"]
    et, en = (d["excluded"].get(f"{model}/{lesson}/{a}", 0) for a in ("told", "not_told"))
    ni = et > 10 or en > 10 or n1 == 0 or n0 == 0
    obs.append(dict(lesson=lesson, k1=k1, n1=n1, k0=k0, n0=n0, ni=ni))
    if k1 * n0 <= k0 * n1:
        bnd.append(dict(lesson=lesson, k1=k1 + et, n1=n1 + et, k0=k0, n0=n0 + en, ni=ni))
    else:
        bnd.append(dict(lesson=lesson, k1=k1, n1=n1 + et, k0=k0 + en, n0=n0 + en, ni=ni))
recs = stats._records(Path(os.environ["ACE_LAB_DATA"]) / "trials.jsonl", exclude_pilot=True, every=True)   # every attempt, file order
head, last = {}, {}
for r in recs:
    head.setdefault(r["trial_id"], r)
    last[r["trial_id"]] = r
fk, fe, retried = {}, {}, {}                                                      # first-attempt counts, exclusions, retried trials
for tid, r in head.items():
    if r.get("model") != model or r.get("lesson") not in ("edit_unread", "preclaim") or stats._cued(r) or stats._single(r):
        continue
    key = (r["lesson"], r["arm"])
    try:
        if r.get("error") or not isinstance(r.get("stream"), str) or not os.path.isfile(r["stream"]):
            raise OSError("no first stream to score")
        hit = score.score(r["stream"], r["lesson"])["any"]
    except (OSError, KeyError, ValueError):
        fe[key] = fe.get(key, 0) + 1
        retried[key] = retried.get(key, 0) + (last[tid] is not r and not last[tid].get("error"))
        continue
    k, n = fk.get(key, (0, 0))
    fk[key] = (k + bool(hit), n + 1)
fst = []
for o in obs:
    (k1, n1), (k0, n0) = fk.get((o["lesson"], "told"), (0, 0)), fk.get((o["lesson"], "not_told"), (0, 0))
    et, en = fe.get((o["lesson"], "told"), 0), fe.get((o["lesson"], "not_told"), 0)
    ni = et > 10 or en > 10 or n1 == 0 or n0 == 0
    if o["k1"] * o["n0"] <= o["k0"] * o["n1"]:                                   # the OBSERVED direction, as the bounds row
        fst.append(dict(lesson=o["lesson"], k1=k1 + et, n1=n1 + et, k0=k0, n0=n0 + en, ni=ni))
    else:
        fst.append(dict(lesson=o["lesson"], k1=k1, n1=n1 + et, k0=k0 + en, n0=n0 + en, ni=ni))
for name, rows in (("observed", obs), ("bounds", bnd), ("first", fst)):
    for r in rows:
        r["p"] = fisher_two_sided(r["k1"], r["n1"], r["k0"], r["n0"])
    decide(rows)
    for r in rows:
        print(name, model, r["lesson"], f'told {r["k1"]}/{r["n1"]} not_told {r["k0"]}/{r["n0"]}', "p", repr(r["p"]),
              "not_interpretable", r["ni"], "decision", r["decision"])
for lesson in ("edit_unread", "preclaim"):
    print("retried", model, lesson, "told", retried.get((lesson, "told"), 0), "not_told", retried.get((lesson, "not_told"), 0))
```

First-attempt errors per arm (`checks` `first_attempt_errors`) and all attempts' errors (`attempt_errors`) are reported after the
analysis, descriptive.

## Analysis
After both confirmatory commands of a model (and their re-runs): `ACE_LAB_DATA=<data dir> uv run python -m ace_playbook.lab report
--exclude-pilot --json` and the decision block above, at the registered commit, ARE the analysis for that model; nothing else is
confirmatory. Per lesson (the primary
row): any-violation rate per arm with Wilson 95 %, the TOLD - NOT_TOLD difference with its Newcombe hybrid-score 95 % interval
(`diff_newcombe95`, the registered effect interval, not Holm-adjusted), a two-sided Fisher exact p, Holm over the two lessons of the model
at family alpha 0.05 (on the raw p; the exclusion-ceiling rule above replaces Holm for a model with a not-interpretable lesson). A
lesson "reduces the mistake" on a model when the decision block's `observed` line says `reduce` (rejected AND the TOLD rate the lower);
`increase` is reported as an increase. The report's rounded `fisher_p` and `p_holm` are display only. The two models are reported separately, never pooled.
Scope of the claim: the result holds for these 8 tasks with these prompts, on this binary and model, with this tool surface (the
registered allowlist, no MCP server), and nothing wider. Trials are
clustered in 8 fixed tasks (13 reps per task and arm) and Fisher treats them as independent, so the p value does not carry to other
tasks; this is a stated limitation. The primary row's `per_task` table (task x arm: trials with a violation / trials) is reported as
registered, descriptive, no test.
Secondary, descriptive, no test: mean violations per trial, `attempted` edits (edit_unread), cost and turns per trial (block below:
over the last record of each non-pilot trial id that has no error; the cost is the LAST entry of its `costs`, since `total_cost_usd` is
cumulative over the session; the turns are the sum of `num_turns`), `permission_denials`, exclusions per arm (`excluded`: errored
after the re-runs, or a missing stream), first-attempt and all-attempt errors per arm.
Arm order: the trial list is completely randomised by the seed, not blocked, so the arms need not balance over time (the Opus seed puts
60 of 104 TOLD trials of a lesson in the first half), and the two lessons of a model share the seed and the list length, so their arm
order is identical. This is a stated limitation; per lesson, the TOLD share and the errored share of the first attempts by quarter of
the run order are reported (descriptive) from `uv run python -I - "<data dir>" <lesson>` fed this block (this block and the cost block
read `trials.jsonl` and are run only on a data dir that holds one; on a dir without it they raise and nothing is reported):

```python
import json, sys
first = {}
for line in open(sys.argv[1] + "/trials.jsonl"):
    r = json.loads(line)
    if r.get("lesson") == sys.argv[2] and not r.get("pilot"):
        first.setdefault(r["trial_id"], r)
rows = list(first.values())
for i in range(4):
    part = rows[len(rows) * i // 4:len(rows) * (i + 1) // 4]
    print(i + 1, len(part), sum(r["arm"] == "told" for r in part), sum(bool(r.get("error")) for r in part))
```

Cost and turns, from `uv run python -I - "<data dir>"` fed this block (a record with no cost or a turn count that is not a number is
left out and counted as `skipped`):

```python
import collections, json, sys
last, out, skipped = {}, collections.defaultdict(list), collections.Counter()
for line in open(sys.argv[1] + "/trials.jsonl"):
    r = json.loads(line)
    if not r.get("pilot"):
        last[r["trial_id"]] = r
for r in last.values():
    if r.get("error"):
        continue
    turns, costs = r.get("num_turns") or [], r.get("costs") or []
    if not costs or not all(isinstance(t, int) for t in turns):
        skipped[r["lesson"], r["arm"]] += 1
        continue
    out[r["lesson"], r["arm"]].append((costs[-1], sum(turns)))
for (lesson, arm), v in sorted(out.items()):
    print(lesson, arm, len(v), "cost mean %.4f max %.4f" % (sum(c for c, _ in v) / len(v), max(c for c, _ in v)),
          "turns mean %.2f" % (sum(t for _, t in v) / len(v)))
print("skipped", dict(skipped))
```

No interim look: until both confirmatory commands of a model (and their re-runs) are done, only the safety checks below are run on its
data dir; they print counts, never an outcome.

## Descriptive only (outside the Holm family, never a confirmatory claim)
exitplan (L-93544dae): its 10 tasks x 2 reps = 20 trials per arm per model, with the same seed as the model's confirmatory runs, in its own
data dir (`$HOME/.local/share/ace-playbook/lab-xp-sonnet-v2`, `.../lab-xp-opus-v2`), run after all four confirmatory commands by the
command above with `--lesson exitplan --reps 2` and no `--tasks` (all 10), under the same error, safety and STOP rules. Pilot 1 and
the re-pilot gave TOLD 5/5 vs NOT_TOLD 5/5; it is reported as rates with Wilson intervals only (decision D-259). The single-claim preclaim tasks and
the cued edit_unread tasks are not run.

## Safety (STOP rule)
Before the first scored trial and after every command (each re-run included), in each data dir, from the repository: the binary sha256 command
(Instrument) must print the registered hash, and the block below, saved as a file and run as `ACE_LAB_DATA=<data dir> PYTHONPATH=. uv run python -P
<file> <data dir> <model id> <main|xp>`, must print `PASS` as its last line. It prints counts and error kinds (without the arm), never an outcome; the
streams are never opened by hand (docs/lab.md's "open them" and "needs a look" steps, and its "stop if `report` shows outside-run-dir tool inputs
above 0", are replaced by its counts and the split below). The registered tool sets are the lab's `--tools` lists: `Read, Edit, Write, Glob, Grep,
ToolSearch` for a confirmatory (main) dir and `Read, Edit, Write, Glob, Grep, ToolSearch, ExitPlanMode` for an exitplan (xp) dir (Write and Edit: plan
mode writes its plan file through its own exemption, never a request to the driver; a write to any other path would reach the driver and be denied —
not probed, no exitplan trial wrote elsewhere in 58 pilot trials on 2.1.317 (26 exitplan); a write outside the run dir that is not refused is
`outside_ran` regardless). Every init event of every stream must list exactly the tools of its set and no MCP server, else `bad_keys` rises (the
claude.ai connectors attach from a trial's second turn unless `--strict-mcp-config` holds; probed on 2.1.317, 2026-11-04). Every init also lists
`memory_paths.auto` under `~/.claude/projects/`, the binary's auto-memory dir for the run's cwd: a Write there is outside the run dir (`outside_ran`
unless refused; 0 in 58 pilot trials and 208 version-1 trials on 2.1.317), and a file in that dir by any means (the binary could write there without a
tool_use; an unlistable dir or a non-dir at that path counts too) raises `memory_files`, run-ending (pilot 4's 24 dirs held no file). A tool_use of a
tool outside the set (read, like every tool_use since version 2, from the `assistant` events, its tool_results from the `user` events) counts as
`denied_ran` unless its id is a string that exactly one tool_use of the stream carries, at least one tool_result of the stream names it, and every
tool_result of the stream for it is an error whose content is EXACTLY the binary's refusal (`<tool_use_error>Error: No such tool available: <name>.
<name> is disabled for this session, in subagents as well as here.</tool_use_error>`, the 2.1.317 text seen for a disallowed tool (Bash) and for one
left out of `--tools` (CronList), probe streams refuse1/2 of 2026-11-04; for a name 2.1.317 lacks altogether, such as LS, TodoWrite or MultiEdit, the
text is unprobed: two Haiku probes on 2026-11-05 made no such call, and 0 of 58 pilot trials on 2.1.317 did); an exact refusal counts as
`denied_refused` (reported, not a STOP). A tool outside the set that ran and failed, or any other result or none, or a shared or missing id (of its
stream), is `denied_ran` (counted once per id of a stream; all missing ids of a stream count once): a hallucinated call to a lacking name whose error
text differs would end the run, and is reported as that, never overridden. A tool_use input outside the run dir (the report's own test,
`outside.inputs` on the init `cwd`, read as `score._outside` reads it: the tool_use blocks of `assistant` events, with the tool_result blocks of
`user` events, where the binary puts them; the block walks the regular, non-symlink `.jsonl` files of `streams/` and counts anything else there as
opaque) is `outside_refused` only when its tool_use id is a string that exactly one tool_use of the stream carries, at least one tool_result of the
stream names that id, every such result has `is_error` true with a content that is a str of the text seen on 2.1.317 for that tool (or, where stated,
inferred) — for a tool outside the set exactly the binary's refusal above (such an input counts in both `denied_*` and `outside_*`); for Edit exactly
`<tool_use_error>File does not exist. Note: your current working directory is <the stream's init cwd, as the init event prints it>.</tool_use_error>`
(the Edit tool's own error, seen in a main dir, version 1's r12, where it came before any permission request; inferred for an xp dir, where the Edit
tool's existence check still comes before any driver request); otherwise, for a main dir a text beginning `Claude requested permissions to ` (the
headless `-p` denial of a path outside the cwd; a main run has no permission-prompt tool; seen for Read, version 1's r0, inferred for the other tools
of the set) and for an xp dir exactly the driver's `tool not permitted in this lab run` (exitplan.OTHER_MSG, never seen itself on 2.1.317: that it
comes back verbatim is inferred from the driver's twelve ExitPlanMode answers in pilot 4, which did) — and the report counted that stream once,
through a path it could read: exactly one record of trials.jsonl, read as the report reads it (a line that `splitlines()` would split in two — a CR,
VT, FF, FS, GS, RS, NEL, U+2028 or U+2029 inside it — is not read: it counts as an opaque stream, run-ending, and no input is then refused), not a
`build failed` one, names the stream's realpath and the report's scanner (`score.outside_scan`) on that record's own path counts what the walk
counted, or no record names it and the scanner on the file does (an orphan). Every other outside input is `outside_ran`: a result that is not an error
or has any other text (an unseen refusal text would end the run and is reported as that, never overridden, as for `denied_ran`), no result, a shared
or missing id (of its stream), a stream named by two records, a stream the block counts opaque (nothing is refused there), a stream the scanner reads
differently, or an input the report counts in a stream the walk does not read (a record naming a stream outside `streams/`): `outside_ran` is the
report's `outside_run_dir_tool_inputs` minus `outside_refused`, plus the walk's inputs of a stream the report could not read (its scanner raises on
the record's path) — the block prints `outside_ran` and `outside_refused`; `outside_refused` never exceeds the total; a JSON line that is not an
object counts as a bad line, as the report counts it; the block first exits, a STOP, unless `ACE_LAB_DATA`, which the report reads, and its first
argument, which the walk reads, are the same directory. `outside_refused` is reported, not a STOP (version 1's 2 of 208 Sonnet trials were refused
typos of the cwd; 0 of 58 pilot trials); `outside_ran` is run-ending. The counts `outside_unreadable`, `outside_bad_lines` and
`outside_orphan_streams` are the report's, unchanged.

```python
import collections, json, os, posixpath, subprocess, sys
from ace_playbook.lab import outside, score
data, model, kind = sys.argv[1:4]
if os.path.realpath(os.environ["ACE_LAB_DATA"]) != os.path.realpath(data):            # the report and the walk read the same dir
    sys.exit("ACE_LAB_DATA and the data dir argument differ")
cmd = [sys.executable, "-m", "ace_playbook.lab", "report", "--json"]
d = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True, env=os.environ).stdout)
keys = ("outside_unreadable", "outside_bad_lines", "outside_orphan_streams")
state = {k: d[k] or 0 for k in keys}
total = d["outside_run_dir_tool_inputs"] or 0                                  # the report's count of every outside input, refused or not
c = d["checks"]
sha = {"main": {"preclaim/403bac3abaed7d36", "edit_unread/5e0a169cf0eaf46c"}, "xp": {"exitplan/05204efb06eb3f69"}}[kind]
state["bad_keys"] = (len(set(c["lesson_sha16"]) - sha) + len(set(c["binary"]) - {"2.1.317/" + model, "None/None"})
                     + len(set(c["model_usage"]) - {model}))
state["none_binary"] = c["binary"].get("None/None", 0)
state["errored_attempts"] = sum(c["attempt_errors"].values())
allowed = {"main": {"Read", "Edit", "Write", "Glob", "Grep", "ToolSearch"}, "xp": {"Read", "Edit", "Write", "Glob", "Grep", "ToolSearch", "ExitPlanMode"}}[kind]
sdir = os.path.join(data, "streams")
state["denied_ran"] = state["opaque_streams"] = state["memory_files"] = state["outside_ran"] = refused = out_refused = 0
refusal = ("<tool_use_error>Error: No such tool available: {0}. {0} is disabled for this session, in subagents as well as "
           "here.</tool_use_error>")
FNE = "<tool_use_error>File does not exist. Note: your current working directory is {0}.</tool_use_error>"   # the Edit error (v1, r12)
def refused_ok(tool, content, cwd_raw):                                        # a seen text, bound to the tool that produced it
    if not isinstance(content, str):
        return False
    if tool not in allowed:
        return content == refusal.format(tool)                                 # the binary's refusal of a tool outside the set
    if tool == "Edit" and isinstance(cwd_raw, str) and content == FNE.format(cwd_raw):
        return True
    return (content.startswith("Claude requested permissions to ") if kind == "main"   # the headless -p denial (main has no driver)
            else content == "tool not permitted in this lab run")                   # exitplan.OTHER_MSG, the xp driver's denial
named, npath, split_records, unread_walk = collections.Counter(), {}, 0, 0     # realpath -> records naming it, and the path they name
try:
    for line in open(os.path.join(data, "trials.jsonl"), "rb").read().decode("utf-8", "replace").split("\n"):
        if len(line.splitlines()) > 1:                                          # a line the report's splitlines() reader would split
            state["opaque_streams"] += 1                                        # in two records: not read here, run-ending
            split_records += 1
            continue
        try:
            r = json.loads(line) if line.strip() else None
        except ValueError:
            continue                                                            # an unparseable record names nothing
        s = r.get("stream") if isinstance(r, dict) else None
        if isinstance(s, str) and s and not str(r.get("error") or "").startswith("build failed"):
            named[os.path.realpath(s)] += 1
            npath[os.path.realpath(s)] = s
except OSError:
    pass                                                                        # no trials.jsonl: every stream is an orphan
for name in sorted(os.listdir(sdir)) if os.path.exists(sdir) else []:
    path = os.path.join(sdir, name)
    if not name.endswith(".jsonl") or os.path.islink(path) or not os.path.isfile(path):   # not a stream the lab wrote: fail closed
        state["opaque_streams"] += 1
        continue
    try:
        with open(path, "rb") as fh:
            lines = fh.read().decode("utf-8", "replace").split("\n")
    except OSError:
        state["opaque_streams"] += 1
        continue
    bad, uses, outs, ids, results, cut, cwd, cwd_raw, walk = [], {}, {}, collections.Counter(), collections.defaultdict(list), False, None, None, 0
    while lines and not lines[-1].strip():
        lines.pop()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        cut = cut or len(line.splitlines()) > 1          # U+2028, U+2029 or U+0085: the lab's splitlines() parsers cut this line in two
        try:
            e = json.loads(line)
        except ValueError:
            bad.append(i)
            continue
        if not isinstance(e, dict):                                              # a JSON line that is not an event: bad, as the report counts it
            bad.append(i)
            continue
        if e.get("type") == "system" and e.get("subtype") == "init":
            if isinstance(e.get("cwd"), str):
                cwd_raw = e["cwd"]                                               # as the binary prints it in its own messages
                cwd = posixpath.normpath(cwd_raw)                                # the run dir, as score.py reads it; None before an init
            state["bad_keys"] += bool(set(e.get("tools") or []) != allowed or e.get("mcp_servers"))   # exactly the set; none/empty fails
            mem = (e.get("memory_paths") or {}).get("auto")                     # the binary's auto-memory dir: any file there is outside
            if isinstance(mem, str) and os.path.lexists(mem):
                unlisted = []                                                    # a dir that cannot be listed counts: never fail open
                state["memory_files"] += (sum(len(fs) for _, _, fs in os.walk(mem, followlinks=True, onerror=unlisted.append)) + len(unlisted)
                                          if os.path.isdir(mem) else 1)
        body = e.get("message")
        for b in body.get("content") or [] if isinstance(body, dict) else []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and e.get("type") == "assistant":   # as score._outside reads them
                ids[b.get("id")] += 1
                if b.get("name") not in allowed:
                    uses[b.get("id")] = b.get("name")
                if isinstance(b.get("input"), dict):
                    n = outside.inputs(cwd, b.get("name"), b["input"])          # the report's own test of one input (score.py's outside count)
                    walk += n
                    if n:
                        outs[b.get("id")] = (outs.get(b.get("id"), (0,))[0] + n, b.get("name"))
            if isinstance(b, dict) and b.get("type") == "tool_result" and e.get("type") == "user":   # where the binary puts them
                results[b.get("tool_use_id")].append((b.get("is_error"), b.get("content")))
    def one(i):                                                                  # one tool_use carries the id and at least one result
        return isinstance(i, str) and ids[i] == 1 and bool(results[i])
    for i, tool in uses.items():
        ok = one(i) and all(r == (True, refusal.format(tool)) for r in results[i])
        refused += ok
        state["denied_ran"] += not ok
    opaque = cut or (bad and not (bad == [len(lines) - 1] and "tool_" not in lines[-1]))
    rp = os.path.realpath(path)                                                  # counted by the report once, through the path it read?
    via = npath[rp] if named[rp] == 1 else (path if named[rp] == 0 else None)   # one record's own path, or an orphan's
    try:
        same = via is not None and score.outside_scan(via)[0] == walk            # the report's scanner on that path agrees with the walk
    except (OSError, ValueError):
        same = False
        unread_walk += walk                                                      # the report could not read it: the walk's inputs ran
    same = same and not opaque                                                   # nothing is refused in a stream counted opaque
    for i, (n, tool) in outs.items():                                             # refused only when every result is an error of a seen text
        out_refused += n if same and one(i) and all(r[0] is True and refused_ok(tool, r[1], cwd_raw) for r in results[i]) else 0
    if opaque:
        state["opaque_streams"] += 1
out_refused = 0 if split_records else min(out_refused, total)                  # the records were not read as the report reads them
state["outside_ran"] = total - out_refused + unread_walk        # the report's inputs not refused, plus those of streams it could not read
ending = ("outside_ran", "denied_ran", "bad_keys", "opaque_streams", "memory_files")
snaps = sorted((f for f in os.listdir(data) if f.startswith("stop-") and f.endswith(".json")), key=lambda f: int(f[5:-5]))
base = json.load(open(os.path.join(data, snaps[-1]))) if snaps else dict.fromkeys(state, 0)
print("counts", state, "baseline", snaps[-1] if snaps else "none")
errs = collections.Counter()
for k, v in c["attempt_errors"].items():
    errs[k.split("/", 1)[1]] += v
print("errors", dict(sorted(errs.items())), "denied_refused", refused, "outside_refused", out_refused)
if len(sys.argv) > 4 and sys.argv[4] == "snapshot":
    if any(state[k] > base[k] for k in ending):
        print("no snapshot: a run-ending count rose")
    else:
        name = f"stop-{len(snaps) + 1}.json"
        with os.fdopen(os.open(os.path.join(data, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as fh:
            json.dump(state, fh)
        print("snapshot", name)
        base = state
limit = {k: base[k] + (5 if k == "errored_attempts" else 0) for k in state}
print("PASS" if all(state[k] <= limit[k] for k in state) else "STOP: " + ",".join(k for k in state if state[k] > limit[k]))
```

On an empty data dir it prints `counts {...}` with every value 0 and `baseline none`, `errors {} denied_refused 0 outside_refused 0`, then `PASS`. A
count is cumulative over every attempt, so a check passes when no count rose above the baseline (zero, or the latest snapshot
`stop-<n>.json` written at a resumed STOP), except that `errored_attempts` may rise by up to 5.
Any STOP line, a wrong binary hash, or a block that fails to run (an exception or a nonzero exit) → a `STOP` file in that data dir and
no further trial in any data dir; the data are kept and every recorded trial stays in the record. A block that failed to run is resumed
by the rule below once it runs again.
A STOP is resumable exactly when none of `outside_ran`, `denied_ran`, `bad_keys`, `opaque_streams`, `memory_files` rose and the binary
hash is right; this is mechanical (an unreadable stream that is empty, an orphan or a stream whose only bad line is a truncated last line without
`tool_` was still scanned in full by both scanners; a line holding a raw U+2028, U+2029 or U+0085 is NOT: the lab's `splitlines()` parsers cut it in
two and never see that event's tool inputs, so the block counts such a stream opaque, which ends the run). Before the resume: `pgrep -f
'versions/2\.1\.31[7] -p'` prints nothing (the pattern matched a live 2.1.317 `-p` child 7 times over a 3 s probe run, 2026-11-05T03:07Z; no lab
`claude` child survives; if it prints, the operator waits 10 minutes and re-checks, and never kills it), and after an `errored_attempts` STOP at least
5 hours have passed since the STOP; the cause as seen in the block's `errors` line and the file system (never `run.log`, which names the arm) is
recorded, not judged. To resume: the block is run with a fifth argument `snapshot` (its `counts` line still names the old baseline; it then prints
`snapshot stop-<n>.json` (mode 0600) and `PASS` against the new baseline, or `no snapshot: a run-ending count rose` and a STOP line), the `STOP` file
is renamed `STOP.<n>` and kept, and the same command is run again. Every other STOP ends the run, a false positive included (it is never inspected and
resumed). A run-ending STOP ends every command not yet finished, for both models. A model whose confirmatory commands (and re-runs) finished before it
keeps its confirmatory result, except that a `bad_keys` STOP in that model's confirmatory data dir (`lab-sonnet-v2` or `lab-opus-v2`) at any time, or a
wrong binary hash at any check up to and including the one after that model's last confirmatory command, makes its result descriptive only. A result
made descriptive only, and the trials of an ended run, are reported apart from the confirmatory results. Every STOP is reported with its time, its
cause and its snapshot.

## Power (stated before data)
Fisher two-sided at alpha 0.025 (the Holm worst case over two), 1,000 simulations (seed 1), N 104 per arm: for a relative rate (TOLD /
NOT_TOLD) of 0.5, power 0.78 / 0.91 / 1.00 / 1.00 at a NOT_TOLD rate of 0.4 / 0.5 / 0.8 / 1.0; for 0.67, 0.36 / 0.53 / 0.95 / 1.00. Pilot
rates (pilot trials, never analysed): preclaim NOT_TOLD 2/5, TOLD 4/5 (pointing to an increase, uninformative at n 5; the test is
two-sided); edit_unread uncued NOT_TOLD 4/4, TOLD 0/4. With a NOT_TOLD rate near 0.4 a modest effect may be missed; a null here is
reported with its interval, not as "no effect".

## Null handling
If no lesson's `observed` decision is `reduce` on a model, the README reports a null for that model with the intervals; it does not
search other task subsets, models, reps or outcomes for an effect, and it does not add trials.
