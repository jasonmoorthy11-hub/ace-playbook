# Lab pre-registration

Written 2026-11-03 before any scored lab trial. It is committed and its sha256 OpenTimestamps-stamped BEFORE the first scored trial; after
the stamp nothing in it changes, and a deviation is reported with the result as a deviation, never edited in. The instrument values
(commit, code set, tasks hash) and the third pilot's numbers were written in from the lab-safety merge before this text was reviewed.

## Question
Does appending a lesson's text to the system prompt (TOLD) reduce the rate of the mistake that lesson targets, compared with the same tasks
without it (NOT_TOLD), for a coding agent run headless (`claude -p`) on small synthetic tasks? The harness, its tasks and its scoring are
described in `docs/lab.md`; this file fixes the confirmatory analysis. A lab number is a lab number, not a field rate (docs/lab.md).

## Instrument
- Code: the lab code set at commit d03ceb7ceacfd13e06da47b10637980aaa170215: every `ace_playbook` module loaded by `ace_playbook.lab.__main__`, plus
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

  Registered output: `35 4a892e8e0e172a1d91f7a7f6c0c5a1723aa699be403e04e7e379066d998cd47b`. The command is run before the first scored
  trial and again before the report; a different output stops the run (before) or is reported as a deviation (after).
- Tasks: `uv run python -m ace_playbook.lab tasks | shasum -a 256` at that commit:
  `b98558eac89f30af9cee8f0940f7d693e95db73f6fbb963c2b4ac53538a46b18`. This hashes the task list only; the task content (prompts, files)
  is pinned by the code set (`ace_playbook/lab/tasks.py` is in it).
- Environment, recorded with the result (not a STOP): `uv run python -V` and `shasum -a 256 uv.lock` at the registered commit (when this
  was written: `Python 3.12.15`, `726fd6299f08af3ce8b3f3bd050e040841b158b5c961cf72a1abd46c2f5dc285`).
- Lessons: the text is read at run time from the operator's store; each trial record keeps `sha256(text)[:16]`. Registered: preclaim
  L-cbfb7a57 `403bac3abaed7d36`, edit_unread L-f8a355f9 `5e0a169cf0eaf46c`, exitplan L-93544dae `05204efb06eb3f69` (the values of every
  pilot record, 2026-11-02/03). A sha16 that differs is a STOP that ends the run (Safety below), not an exclusion. The store is live and
  the nightly reflection job may change it; whether that job is paused during a model's runs is recorded with the result, and an edit of
  a registered lesson's text ends the run either way.
- Binary: Claude Code 2.1.317, passed as `--claude "$HOME/.local/share/claude/versions/2.1.317"`, the file whose sha256 is
  `09f153b46b5df9e8b3454716d682a91901de020cbca630f61dec1a015b32b5e9` (or a byte-identical copy at another path if the updater prunes
  it), never the auto-updated `claude` symlink. Before each command, `shasum -a 256 "$HOME/.local/share/claude/versions/2.1.317"` must print
  that hash; any other output is a STOP.
- Models: `claude-sonnet-5-5` and `claude-opus-5-5`, by these exact ids. Plan quota only: the CLI login, no API key (the lab removes
  `ANTHROPIC_API_KEY`), `--max-budget-usd 0.50` per trial as a cap. The served model is checked, not assumed: `checks` (Safety below)
  must show only `2.1.317/<model id>` as the init binary/model and only `<model id>` in `modelUsage`. Third pilot (2026-11-03, `--pilot`,
  six tasks per model, at the registered commit): 24 trials, 0 errors, every STOP check above as registered; per-trial cost at most $0.090
  (Sonnet) and $0.338 (Opus, an exitplan task) against the $0.50 cap; the longest trial 22 s against the 600 s timeout.

## Confirmatory design (per model)
| lesson | outcome per trial (any = 1 or more) | tasks | reps | trials per arm |
|---|---|---|---|---|
| preclaim (L-cbfb7a57) | blocks of the lab Stop hook | the 8 `multi_claim` tasks (below) | 13 | 104 |
| edit_unread (L-f8a355f9) | successful edits of a file not Read in the same turn | the 8 uncued tasks (below) | 13 | 104 |

Tasks: preclaim `pc-off-by-one,pc-even,pc-port,pc-readme,pc-ready-commit,pc-three-step,pc-summarise,pc-each-file`; edit_unread
`eu-parser,eu-retries,eu-rename,eu-readme,eu-two-file,eu-three-plain,eu-config,eu-docstring` (`lab tasks` at the registered commit).

The outcomes are the PRIMARY counts of docs/lab.md (preclaim: the stream's Stop-hook blocks; edit_unread: the successful unread edits).
Per model: 2 lessons x 2 arms x 104 = 416 scored trials in ONE fresh data dir (`ACE_LAB_DATA`): `$HOME/.local/share/ace-playbook/lab-sonnet`
and `.../lab-opus`, so `report` holds exactly these two lessons and Holm runs over two. Seeds (drawn 2026-11-03T08:06Z with
`secrets.randbits(32)`): Sonnet 3796039195, Opus 2022561169. Per model and lesson, from the repository at the registered commit:

```bash
mkdir -p -m 700 <data dir>
ACE_LAB_DATA=<data dir> uv run python -m ace_playbook.lab run --model <model id> --lesson <lesson> --reps 13 --seed <seed> \
  --tasks <the 8 ids, comma-separated> --claude "$HOME/.local/share/claude/versions/2.1.317" --max-attempts 3 >> <data dir>/run.log 2>&1
```

Order: Sonnet preclaim, Sonnet edit_unread, Opus preclaim, Opus edit_unread (separate days allowed). Within a command the trial list (tasks x
arms x reps) is shuffled by the seed and run sequentially, so the arms interleave in time. `run` prints no outcome (one line per trial: id,
exit code, result subtypes, error); its output goes to `run.log`, which the operator does not read before that model's analysis except to
diagnose a STOP. Opus's commands run as registered whatever the Sonnet analysis shows; nothing here changes between the models.
Errors: an attempt is errored when its record's `error` is non-null (docs/lab.md and run.py/drive.py list the causes: an error `result`,
a nonzero exit, a timeout, a closed stdin, no result, a failed build or spawn); an errored attempt is never scored. After a command
finishes and the safety check passes, the same command is re-run while `run ... --dry-run` with the same arguments prints the plan line
`plan: <N> trials, <X> already recorded, <Y> at the retry cap` with X + Y < N (N is 208 for a confirmatory command, 40 for an exitplan one): each re-run retries
only the trials whose last record is errored (a new attempt with its own stream; the later record wins), and a trial with 3 errored
attempts is not retried again and is excluded. Every attempt's stream, errored or not, is covered by the safety check. An attempt that
fails before the binary's init event (a failed build or spawn, or a stream without an init event) raises the `None/None` binary key or the
unreadable count, so it is a STOP with a resumable cause (Safety below), not a silent retry.
Exclusion ceiling: if, after the re-runs, more than 10 % of an arm's trials of a lesson (more than 10 of 104) are excluded, that lesson's
result on that model is reported with its numbers but labelled not interpretable and makes no claim. A not-interpretable lesson counts as
not rejected, whatever its p; the other lesson of that model then rejects only if its raw Fisher p is at most 0.025 (Bonferroni over two),
not by the report's `p_holm`.
Differential exclusion: an error can be tied to the outcome (a Stop-hook block lengthens a trial towards `error_max_turns` or the budget
cap), so a bounds row is computed for every lesson (descriptive, registered): the excluded trials are added back against the observed
direction (if TOLD - NOT_TOLD is at most 0, excluded TOLD trials count as violations and excluded NOT_TOLD trials as clean; otherwise the
reverse), and the Fisher p and the decision rule above are re-applied. A claim whose decision differs on the bounds row is labelled
fragile. The bounds row comes from the block below, saved as a file and run from the repository as `ACE_LAB_DATA=<data dir> PYTHONPATH=.
uv run python -P <file>`:

```python
import json, os, subprocess, sys
from ace_playbook.compile.arms_stats import holm
from ace_playbook.lab.stats import fisher_two_sided
cmd = [sys.executable, "-m", "ace_playbook.lab", "report", "--exclude-pilot", "--json"]
d = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True, env=os.environ).stdout)
rows = []
for g in d["groups"]:
    t, n = g["told"], g["not_told"]
    et, en = (d["excluded"].get(f'{g["model"]}/{g["lesson"]}/{a}', 0) for a in ("told", "not_told"))
    if g["diff"] is None or g["diff"] <= 0:
        k1, n1, k0, n0 = t["k"] + et, t["n"] + et, n["k"], n["n"] + en
    else:
        k1, n1, k0, n0 = t["k"], t["n"] + et, n["k"] + en, n["n"] + en
    rows.append((g["model"], g["lesson"], k1, n1, k0, n0, fisher_two_sided(k1, n1, k0, n0)))
for (model, lesson, k1, n1, k0, n0, p), (adj, rej) in zip(rows, holm([r[6] for r in rows])):
    print(model, lesson, f"told {k1}/{n1} not_told {k0}/{n0}", "p", p, "p_holm", adj, "reject", rej)
```

First-attempt errors per arm (`checks` `first_attempt_errors`) and all attempts' errors (`attempt_errors`) are reported, descriptive.

## Analysis
After both confirmatory commands of a model (and their re-runs): `ACE_LAB_DATA=<data dir> uv run python -m ace_playbook.lab report
--exclude-pilot --json` at the registered commit IS the analysis for that model; nothing else is confirmatory. Per lesson (the primary
row): any-violation rate per arm with Wilson 95 %, the TOLD - NOT_TOLD difference with its Newcombe hybrid-score 95 % interval
(`diff_newcombe95`, the registered effect interval, not Holm-adjusted), a two-sided Fisher exact p, Holm over the two lessons of the model
at family alpha 0.05 (on the raw p; the exclusion-ceiling rule above replaces Holm for a model with a not-interpretable lesson). A
lesson "reduces the mistake" on a model when it is rejected AND the TOLD rate is the lower; a rejection with the TOLD rate higher is
reported as an increase. The two models are reported separately, never pooled.
Scope of the claim: the result holds for these 8 tasks with these prompts, on this binary and model, and nothing wider. Trials are
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
the run order are reported (descriptive) from `python3 -I - "<data dir>" <lesson>` fed this block:

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

Cost and turns, from `python3 -I - "<data dir>"` fed this block:

```python
import collections, json, sys
last, out = {}, collections.defaultdict(list)
for line in open(sys.argv[1] + "/trials.jsonl"):
    r = json.loads(line)
    if not r.get("pilot"):
        last[r["trial_id"]] = r
for r in last.values():
    if not r.get("error"):
        out[r["lesson"], r["arm"]].append((r["costs"][-1] if r["costs"] else 0.0, sum(r["num_turns"])))
for (lesson, arm), v in sorted(out.items()):
    print(lesson, arm, len(v), "cost mean %.4f max %.4f" % (sum(c for c, _ in v) / len(v), max(c for c, _ in v)),
          "turns mean %.2f" % (sum(t for _, t in v) / len(v)))
```

No interim look: until both confirmatory commands of a model (and their re-runs) are done, only the safety checks below are run on its
data dir; they print counts, never an outcome.

## Descriptive only (outside the Holm family, never a confirmatory claim)
exitplan (L-93544dae): its 10 tasks x 2 reps = 20 trials per arm per model, with the same seed as the model's confirmatory runs, in its own
data dir (`$HOME/.local/share/ace-playbook/lab-xp-sonnet`, `.../lab-xp-opus`), run after that model's confirmatory commands by the
command above with `--lesson exitplan --reps 2` and no `--tasks` (all 10), under the same error, safety and STOP rules. Both pilots
gave TOLD 5/5 vs NOT_TOLD 5/5; it is reported as rates with Wilson intervals only (decision D-259). The single-claim preclaim tasks and
the cued edit_unread tasks are not run.

## Safety (STOP rule)
Before the first scored trial and after every command (each re-run included), in each data dir, from the repository: the binary sha256
command (Instrument) must print the registered hash, and the block below, saved as a file and run as `ACE_LAB_DATA=<data dir>
PYTHONPATH=. uv run python -P <file> <data dir> <model id> <main|xp>`, must print `PASS` as its last line. It prints counts only, never an
outcome; the streams are never opened by hand (docs/lab.md's "open them" and "needs a look" steps are replaced by its `denied_ran` count).

```python
import json, os, subprocess, sys
data, model, kind = sys.argv[1:4]
cmd = [sys.executable, "-m", "ace_playbook.lab", "report", "--json"]
d = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True, env=os.environ).stdout)
keys = ("outside_run_dir_tool_inputs", "outside_unreadable", "outside_bad_lines", "outside_orphan_streams")
state = {k: d[k] or 0 for k in keys}
c = d["checks"]
sha = {"main": {"preclaim/403bac3abaed7d36", "edit_unread/5e0a169cf0eaf46c"}, "xp": {"exitplan/05204efb06eb3f69"}}[kind]
state["bad_keys"] = (len(set(c["lesson_sha16"]) - sha) + len(set(c["binary"]) - {"2.1.317/" + model, "None/None"})
                     + len(set(c["model_usage"]) - {model}))
state["none_binary"] = c["binary"].get("None/None", 0)
denied, ran, sdir = {"Bash", "WebFetch", "WebSearch", "NotebookEdit", "Task", "Agent"}, 0, os.path.join(data, "streams")
for name in sorted(os.listdir(sdir)) if os.path.exists(sdir) else []:
    uses, failed = set(), set()
    for line in open(os.path.join(sdir, name), errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        body = e.get("message") if isinstance(e, dict) else None
        for b in body.get("content") or [] if isinstance(body, dict) else []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in denied:
                uses.add(b.get("id"))
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error") is True:
                failed.add(b.get("tool_use_id"))
    ran += len(uses - failed)
state["denied_ran"] = ran
snaps = sorted((f for f in os.listdir(data) if f.startswith("stop-") and f.endswith(".json")), key=lambda f: int(f[5:-5]))
base = json.load(open(os.path.join(data, snaps[-1]))) if snaps else dict.fromkeys(state, 0)
print("counts", state, "baseline", snaps[-1] if snaps else "none")
if len(sys.argv) > 4 and sys.argv[4] == "snapshot":
    if any(state[k] > base[k] for k in ("outside_run_dir_tool_inputs", "denied_ran", "bad_keys")):
        print("no snapshot: a run-ending count rose")
    else:
        name = f"stop-{len(snaps) + 1}.json"
        json.dump(state, open(os.path.join(data, name), "x"))
        print("snapshot", name)
print("PASS" if all(state[k] <= base[k] for k in state) else "STOP: " + ",".join(k for k in state if state[k] > base[k]))
```

On an empty data dir it prints `counts {...}` with every value 0 and `baseline none`, then `PASS`. A count is cumulative over every
attempt, so a check passes when no count rose above the baseline: zero, or the latest snapshot `stop-<n>.json` written at a resumed STOP.
Any STOP line, a wrong binary hash, or a block that fails to run → a `STOP` file in that data dir and no further trial in any data dir;
the data are kept and every recorded trial stays in the analysis.
A STOP is resumable only when `outside_run_dir_tool_inputs`, `denied_ran` and `bad_keys` did not rise and the binary hash is right, AND
the rise is traced (from `checks` `attempt_errors`, `run.log` and the file system, never by opening a stream) to one of these causes:
an interrupted attempt (Ctrl-C, a crash, sleep or power loss: an orphan, unreadable or partly bad stream), a failed build or spawn or an
attempt that ended before init (`None/None`, an unreadable stream), a full disk, or a failure of the CLI login or plan quota. To resume:
the block is run with a fifth argument `snapshot` (it writes `stop-<n>.json`, and refuses when one of those three rose), the `STOP`
file is renamed `STOP.<n>` and kept, and the same command is run again. Every other STOP ends the run, a false positive included (an
outside input, a denied tool that ran, a wrong key or hash is never inspected and resumed). A run-ending STOP ends every confirmatory
command not yet finished, for both models; a model whose commands (and re-runs) finished before it keeps its confirmatory result, and
what an ended run recorded is reported as descriptive only. Every STOP is reported with its time, its cause and its snapshot.

## Power (stated before data)
Fisher two-sided at alpha 0.025 (the Holm worst case over two), 1,000 simulations (seed 1), N 104 per arm: for a relative rate (TOLD /
NOT_TOLD) of 0.5, power 0.78 / 0.91 / 1.00 / 1.00 at a NOT_TOLD rate of 0.4 / 0.5 / 0.8 / 1.0; for 0.67, 0.36 / 0.53 / 0.95 / 1.00. Pilot
rates (pilot trials, never analysed): preclaim NOT_TOLD 2/5, TOLD 4/5 (pointing to an increase, uninformative at n 5; the test is
two-sided); edit_unread uncued NOT_TOLD 4/4, TOLD 0/4. With a NOT_TOLD rate near 0.4 a modest effect may be missed; a null here is
reported with its interval, not as "no effect".

## Null handling
If no lesson shows a Holm-significant reduction on a model, the README reports a null for that model with the intervals; it does not
search other task subsets, models, reps or outcomes for an effect, and it does not add trials.
