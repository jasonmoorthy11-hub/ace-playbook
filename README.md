# ACE playbook

A measured lesson-to-hook compiler for CLI coding agents. It implements ideas from
*Agentic Context Engineering* (Zhang et al., ICLR 2026, [arXiv:2510.04618](https://arxiv.org/abs/2510.04618);
reference code at [github.com/ace-agent/ace](https://github.com/ace-agent/ace)) for a single
user's day-to-day coding agent, tested with Claude Code.

**Status: stage 2 shadow mode, observe only, no results yet.** The stage-1 plumbing and the
stage-2 compiler exist as code; as of 2026-10-19 no proposal has been approved, so nothing has been
measured. Nothing here shows that injecting lessons or compiling them into hooks helps, and the
pre-registration is a draft that is not sealed. Read [LIMITATIONS.md](LIMITATIONS.md) first.

Not affiliated with or endorsed by Anthropic or by the authors of the ACE paper.

## What it is

An agent that is told the same things in every session never learns which of them matter.
This project keeps an **append-only store of short lessons** extracted from past sessions, records
**which lessons were shown in which session** (with a randomized hide/show holdout), and is built
so that lessons which prove their worth can later be **compiled into enforcing hooks** instead of
staying as prose the agent may ignore. The compile step exists in shadow mode (see below); the
evaluation harness is planned.

## Why

- ACE's idea of itemized lessons changed only by small validated deltas, never rewritten whole,
  is sound. Its published gains depend on execution feedback that a coding assistant mostly lacks.
- Independent work finds auto-generated context can be neutral or harmful. The honest position is
  that "lessons help" is a hypothesis, so the project is designed to measure it and to report a
  null result if that is what comes out.
- Anthropic's own documentation describes CLAUDE.md-style memory as context, not enforced
  configuration. Where a rule must hold, a hook enforces it.

## What stage 1 contains

| Module | What it does |
|---|---|
| `ace_playbook/store` | Delta schema (ADD, PATCH, MERGE, NARROW, RETIRE, NOOP), append-only hash-chained `deltas.jsonl` with atomic whole-file writes, SQLite+FTS5 view rebuilt from the log, read-time decay (rank only) |
| `ace_playbook/clean` | Transcript cleaner: drops harness noise, sidechain records, thinking and large tool results; head 25K + tail 75K sampling; header of outcome signals (user corrections, hook blocks, repeated failing commands) |
| `ace_playbook/scrub` | Regex + entropy + base64-decoded secret scanner, PII and long-token lints, quarantine semantics, repo pre-commit check |
| `ace_playbook/keys` | Project key from the git common directory, with an optional aliases/paths map |
| `ace_playbook/inject` | SessionStart injector: at most 7,000 characters, inert advisory block, pinned lessons only by default (ranked mode adds unpinned `active` lessons, never `provisional` ones), seeded per-(session, lesson) holdout, `injections.jsonl` ledger, citation recorder |
| `ace_playbook/reflect` | Haiku 5.5 reflector client (stdlib HTTP, mockable), provenance gate, minting runner with usage logging and refusal/error paths |
| `hooks/` | Hook scripts, a nightly sweep, a launchd example and `INSTALL.md` (nothing is installed for you) |
| `ace_playbook/fixtures` | Seeded synthetic corpus generator, a frozen seed-1 copy, `DATASHEET.md` |

Planned and **not built**: enforcement by the compiler (only its shadow hook in observe mode exists,
see below), the curator and refiner, the replay and task-suite
evaluation harness, the judge. See [PREREGISTRATION.md](PREREGISTRATION.md).

### Migration

`ace_playbook/migrate` is a one-shot importer for an existing set of legacy lesson files. It
reads the legacy files, a file of already classified records and optional hand overrides, all
named by command-line flags, and either reports what it would do (`--dry-run`, which never
touches the store) or appends the lessons to the delta log (`--apply`, safe to repeat: a second
run adds nothing). It makes no network calls; every verdict comes from its input files. Its
outputs (report, review packet, id map, handoff file) hold lesson text or provenance and are
private: they go to the `--out-dir` you choose and are never committed. Text and notes shown in
the review packet and handoff file are scrub-masked first, and the files are created mode 0600.

Provenance: migrated lessons carry evidence with origin `user_file`, a quote equal to the lesson
text and a `session_id` prefixed `legacy:`. That is self-quoted, model-written provenance, so no
migrated lesson is active unless an override row promoted it: a classified `keep_active` lands as
`provisional` (reason `legacy_unverified`), and only an override with `"action": "keep_active"`
yields `active`. Any future evidence-strength logic must not count `legacy:` evidence as user
evidence. A record flagged `quarantine: true` is quarantined unless an override releases it.

Wording: the normalizer turns non-ASCII arrows and inequalities into words (`to`, `from`,
`at least`, `at most`); mapping `<-` to `from` is a wording change, not a transliteration. Plain
ASCII `->`, `>=` and `<-` already present in legacy text are left alone and still trip the
injector's `angle_brackets` rule, so such lessons land provisional (or stay dormant).

```sh
uv run python -m ace_playbook.migrate --legacy-glob 'DIR/lessons-*.json' \
  --classified classified.jsonl --out-dir OUT --dry-run
```

Design rules that shape the code: no model output is applied as a rewrite; lessons are minted only
from evidence the user actually wrote; a scrub hit sends the whole lesson to quarantine; the
injected block is advisory data, not instructions; hooks fail open and stay out of the way
(stage-1 hooks never block; stage-2 shadow hooks block only a proposal the operator promoted to
block mode after the observe/warn ladder, see [docs/design/compiler.md](docs/design/compiler.md)).
[SECURITY.md](SECURITY.md) has the threat model.

### Compiler (stage 2, shadow mode, in progress)

`ace_playbook/compile` turns a well-evidenced lesson into a spec-only JSON proposal that names one
human-written detector from a fixed catalogue (`python -m ace_playbook.compile cards|propose|validate`),
and the operator approves it at a terminal with `approve <id> --mode observe`. The shadow hook
(`hooks/shadow.sh`, [INSTALL.md](hooks/INSTALL.md) section 7) then evaluates approved proposals on each
Bash call, re-validating them from disk every time (a proposal records a per-detector source hash, so editing one detector's own function body retires only its own proposals and any other edit to the detector or catalogue code, including adding a detector, retires all of them; the release that introduced it made every earlier proposal stale once, to be re-proposed and re-approved), and appends what **would** have fired to
`shadow.jsonl` (ids, hashes and a timestamp, never the command). In observe mode it prints nothing,
changes nothing and cannot block; `approve --mode warn|block` is the promotion ladder (14 days, 5 human-labelled true
positives and no false positive, a hash-chained `approvals.jsonl`, HMAC-keyed once `compile keygen` has made the chain key; warn reaches the model through `additionalContext`).
`compile label` records human labels at a terminal in a hash-chained `labels.jsonl`: an `incident` label is
the only way a legacy lesson gets an evidence card, and `tp`/`fp` labels on shadow rows feed the ladder.
`compile label --queue` is the labelling path: a blind queue of unlabelled fires, oldest first, one key each (tp, fp, skip);
`label --rows` stays for audit.
`compile report` prints counts per proposal from the shadow rows and those human labels, then per corpus the eligible-session
rates with Wilson intervals, friction, hook cost and live-versus-offline agreement; synthetic rows are reported
apart and never blended with the private ones. There are still no results: to produce the reproducible synthetic table run
`python3 -m ace_playbook.compile replay-synthetic` and then `compile report` (the private table stays empty until a proposal is approved). The nightly `compile offline` pass (run by `hooks/nightly_sweep.py`)
records detector events from finished transcripts, pre-fills labels that a human row always overrides, and
appends hash-chained demotions that only ever lower a proposal's mode and that the shadow hook honours until the operator approves it again.
`python3 -m ace_playbook.compile seal --dry-run` prints the pre-registration's frozen-instruments block with live values and writes nothing (the real seal is built but refuses until the seed commitment exists). No model writes a check, a hook or a setting, and nothing is installed for you.
`python3 -m ace_playbook.compile status [--json] [--out PATH]` is the operator's one screen: measurement day, per-proposal fires, labels,
precision with a Wilson interval (`INSUFFICIENT` below five labels), the label queue, a heartbeat and a verdict per proposal. It is built
from the same dict as `compile report --json`, writes `status.json` (0600) and shows nothing to a model.
`compile digest [--since ISO]` prints the same data as six short blocks (status, needs you, detectors, health, changes, next); the nightly
sweep runs it quietly and appends one line to `digest.log`, so a night without a digest shows.
`compile dashboard [--days N] [--out PATH]` renders the same status as one self-contained HTML page (no script, no network; `dashboard.html`, 0600).
`hooks/statusline.sh` is an optional Claude Code status-line command: one line from `status.json` (approved, unlabelled fires in the last 24 h, unlabelled total, chains, heartbeat).

## Run it

Requirements: Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). Runtime code uses only the
standard library; pytest is the only dev dependency.

```sh
uv sync
uv run pytest -q                                    # all tests; no network, no API key
uv run python -m ace_playbook.fixtures.generate --seed 1 --out /tmp/ace-fixtures
uv run python -m ace_playbook.scrub check --mode pre-commit   # passes: fixtures are allowlisted
uv run python -m ace_playbook.scrub check --mode audit        # lists the planted fake secrets
```

The audit run is expected to exit non-zero: it ignores the allowlist and reports the fake
credentials planted in `ace_playbook/fixtures/frozen/`. Pre-commit mode tolerates a hit there only
when the matched text carries the marker `FAKE`.

Try the store on the synthetic corpus:

```sh
export ACE_PLAYBOOK_CONFIG=/tmp/ace-demo.json
echo '{"data_dir": "/tmp/ace-demo"}' > "$ACE_PLAYBOOK_CONFIG"
mkdir -p /tmp/ace-demo && cp /tmp/ace-fixtures/deltas.jsonl /tmp/ace-demo/
uv run python -m ace_playbook.store verify
uv run python -m ace_playbook.store search "migrations staging"
echo '{"session_id":"demo","cwd":"/work/acme-widgets","source":"startup"}' \
  | PYTHONPATH=. python3 -m ace_playbook.inject start
```

Promoting provisional lessons is a human-only step. `python -m ace_playbook.store provisional [--limit N]` lists them (id and the first 80 characters, newest first); `python -m ace_playbook.store promote L-xxxxxxxx [--evidence-quote "your own words"]` (omit the flag to be prompted for the quote and a y/N confirmation on the terminal) then appends one PATCH that sets the lesson `active` and adds your quote as `user_message` evidence (session `promote-cli`). It refuses unless stdin is a terminal, the lesson exists and is `provisional`, the quote is 12 to 400 characters, and the quote passes the outbound scrub (secrets and your configured deny terms) and the injectable-text rule (printable ASCII, no `===`). `--dry-run` prints the record without writing. A PATCH may add evidence items, never remove them, and a lesson holds at most five. The log also refuses a bare `PATCH` to `active` on a lesson whose only user evidence is migrated `legacy:` evidence; see "Provisional promotion" in [LIMITATIONS.md](LIMITATIONS.md) for what `ADD` records still allow.

`PYTHONPATH=. python3 -m ace_playbook.inject --health` prints the nightly sweep's `health.json` (last run, ok, consecutive failures, last error) or `no health record`; it reads no stdin, API key or store. After 3 failed nights in a row the injected block carries one warning line.

No API call is made anywhere in the tests or the commands above. The reflector needs a key file
only when you run `python -m ace_playbook.reflect` or the sweep for real; see `hooks/INSTALL.md`.

## Layout

```
ace_playbook/   package (store, clean, scrub, keys, inject, reflect, fixtures, sweep, enqueue)
hooks/          hook scripts, nightly sweep, launchd example, INSTALL.md
tests/          pytest suite
```

## Credit

The delta-based playbook idea comes from Zhang et al., "Agentic Context Engineering: Evolving
Contexts for Self-Improving Language Models" (ICLR 2026). The compile-to-enforcement direction is
motivated by TRACE (arXiv:2606.13174). This repository is an independent implementation and shares
no code with the reference repository. Citation metadata is in [CITATION.cff](CITATION.cff).

## License

Apache-2.0. See [LICENSE](LICENSE).
