# Lab pre-registration

Written 2026-11-03 before any scored lab trial. It is committed and its sha256 OpenTimestamps-stamped BEFORE the first scored trial; after
the stamp nothing in it changes, and a deviation is reported with the result as a deviation, never edited in. Only the three instrument
values marked FILL (known when the lab-safety fix merges) are written in before the stamp, by a commit that changes only those values.

## Question
Does appending a lesson's text to the system prompt (TOLD) reduce the rate of the mistake that lesson targets, compared with the same tasks
without it (NOT_TOLD), for a coding agent run headless (`claude -p`) on small synthetic tasks? The harness, its tasks and its scoring are
described in `docs/lab.md`; this file fixes the confirmatory analysis. A lab number is a lab number, not a field rate (docs/lab.md).

## Instrument
- Code: the lab code set at commit FILL_COMMIT: every `ace_playbook` module loaded by `ace_playbook.lab.__main__`, plus
  `ace_playbook/lab/hooks/stop-mid-task-audit.sh` and `docs/lab.md` (32 files at a8b67a4). Its hash, run with `bash` in the repository:

  ```bash
  set -euo pipefail
  L=$({ PYTHONPATH=. python3 -P -c 'import sys,os,importlib
  importlib.import_module("ace_playbook.lab.__main__")
  print("\n".join(os.path.relpath(m.__file__) for n,m in list(sys.modules.items()) if (n=="ace_playbook" or n.startswith("ace_playbook."))
  and getattr(m,"__file__",None)))'; echo ace_playbook/lab/hooks/stop-mid-task-audit.sh; echo docs/lab.md; } | sort -u)
  [ -z "$(git status --porcelain -- $L)" ] || { echo "uncommitted change in the lab code set"; exit 1; }
  echo "$(echo "$L" | wc -l | tr -d ' ') $(git ls-files -s --error-unmatch -- $L | shasum -a 256 | cut -c1-64)"
  ```

  Registered output: FILL_CODESET. The command is run before the first scored trial and again before the report; a different output
  stops the run (before) or is reported as a deviation (after).
- Tasks: `python -m ace_playbook.lab tasks | shasum -a 256` at that commit: FILL_TASKS.
- Lessons: the text is read at run time from the operator's store; each trial record keeps `sha256(text)[:16]`. Registered: preclaim
  L-cbfb7a57 `403bac3abaed7d36`, edit_unread L-f8a355f9 `5e0a169cf0eaf46c`, exitplan L-93544dae `05204efb06eb3f69` (the values of every
  pilot record, 2026-11-02/03). A trial whose sha16 differs is excluded and counted.
- Binary: Claude Code 2.1.317, passed as `--claude "$HOME/.local/share/claude/versions/2.1.317"`, the file whose sha256 is
  `09f153b46b5df9e8b3454716d682a91901de020cbca630f61dec1a015b32b5e9` (or a byte-identical copy at another path if the updater prunes
  it), never the auto-updated `claude` symlink. Its sha256 is checked before each run.
- Models: `claude-sonnet-5-5` and `claude-opus-5-5`, by these exact ids. Plan quota only: the CLI login, no API key (the lab removes
  `ANTHROPIC_API_KEY`), `--max-budget-usd 0.50` per trial as a cap.

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
ACE_LAB_DATA=<data dir> uv run python -m ace_playbook.lab run --model <model id> --lesson <lesson> --reps 13 --seed <seed> \
  --tasks <the 8 ids, comma-separated> --claude "$HOME/.local/share/claude/versions/2.1.317"
```

Order: Sonnet preclaim, Sonnet edit_unread, Opus preclaim, Opus edit_unread (separate days allowed). Within a command the trial list (tasks x
arms x reps) is shuffled by the seed and run sequentially, so the arms interleave in time.
Errors: after a command finishes, the safety checks below run; then the same command is re-run at most twice (a re-run retries only the
errored trials; the later record wins). Trials still errored after that are excluded. The safety checks run BEFORE each re-run, because a
re-run truncates the retried trial's stream.

## Analysis
After all four commands of a model: `ACE_LAB_DATA=<data dir> uv run python -m ace_playbook.lab report --exclude-pilot --json` at the
registered commit IS the analysis for that model; nothing else is confirmatory. Per lesson: any-violation rate per arm with Wilson 95 %,
the TOLD - NOT_TOLD difference, a two-sided Fisher exact p, Holm over the two lessons of the model at family alpha 0.05 (on the raw p). A
lesson "reduces the mistake" on a model when Holm rejects AND the TOLD rate is the lower; a Holm rejection with the TOLD rate higher is
reported as an increase. The two models are reported separately, never pooled. Secondary, descriptive, no test: mean violations per
trial, `attempted` edits (edit_unread), cost and turns, `permission_denials`, exclusions per arm.
Exclusions (counted per arm and reported): an errored trial after the re-runs, a missing or unreadable stream, a lesson sha16 or binary
version mismatch.
No interim look: until a model's four commands are done, only the safety command below is run on its data dir, and it prints no outcome.

## Descriptive only (outside the Holm family, never a confirmatory claim)
exitplan (L-93544dae): its 10 tasks x 2 reps = 20 trials per arm per model, with the same seed as the model's confirmatory runs, in its own
data dir (`$HOME/.local/share/ace-playbook/lab-xp-sonnet`, `.../lab-xp-opus`), run after that model's confirmatory commands. Both pilots
gave TOLD 5/5 vs NOT_TOLD 5/5; it is reported as rates with Wilson intervals only (decision D-259). The single-claim preclaim tasks and
the cued edit_unread tasks are not run.

## Safety (STOP rule)
Before the first scored trial (on an empty data dir: all counts 0) and after every command, in each data dir:

```bash
ACE_LAB_DATA=<data dir> uv run python -m ace_playbook.lab report --json | python3 -c 'import json,sys;d=json.load(sys.stdin)
print({k: d[k] for k in ("outside_run_dir_tool_inputs", "outside_unreadable", "outside_bad_lines")})'
```

All three must be 0 (`None` on an empty data dir); the denied-tool greps of docs/lab.md must find no denied tool that ran; and every
stream's init event (`type` system, `subtype` init) must report `claude_code_version` 2.1.317, checked by `python3 -I - "<data dir>"`
fed this block, which must print a Counter with the single key `2.1.317` (a `None` key is a stream without an init event, already
counted unreadable):

```python
import json, sys, glob, collections
def ver(f):
    for line in open(f, errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("type") == "system" and e.get("subtype") == "init":
            return e.get("claude_code_version")
    return None
print(collections.Counter(ver(f) for f in glob.glob(sys.argv[1] + "/streams/*.jsonl")))
```

Any failure → a `STOP` file in that data dir, no further trial in any data dir, the data kept, and the operator decides; the result
reports it. A count above 0 is never explained away after the fact: an inspected false positive is still reported as a STOP.

## Power (stated before data)
Fisher two-sided at alpha 0.025 (the Holm worst case over two), 1,000 simulations (seed 1), N 104 per arm: for a relative rate (TOLD /
NOT_TOLD) of 0.5, power 0.78 / 0.91 / 1.00 / 1.00 at a NOT_TOLD rate of 0.4 / 0.5 / 0.8 / 1.0; for 0.67, 0.36 / 0.53 / 0.95 / 1.00. Pilot
rates (pilot trials, never analysed): preclaim NOT_TOLD 2/5, TOLD 4/5; edit_unread uncued NOT_TOLD 4/4, TOLD 0/4. With a NOT_TOLD rate
near 0.4 a modest effect may be missed; a null here is reported with its interval, not as "no effect".

## Null handling
If no lesson shows a Holm-significant reduction on a model, the README reports a null for that model with the intervals; it does not
search other task subsets, models, reps or outcomes for an effect, and it does not add trials.
