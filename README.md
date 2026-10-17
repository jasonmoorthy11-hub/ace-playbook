# ACE playbook

A measured lesson-to-hook compiler for CLI coding agents. It implements ideas from
*Agentic Context Engineering* (Zhang et al., ICLR 2026, [arXiv:2510.04618](https://arxiv.org/abs/2510.04618);
reference code at [github.com/ace-agent/ace](https://github.com/ace-agent/ace)) for a single
user's day-to-day coding agent, tested with Claude Code.

**Status: stage 1 scaffold, no results yet.** Everything below is plumbing plus an evaluation
design. Nothing here shows that injecting lessons helps, and the pre-registration is a draft
that is not sealed. Read [LIMITATIONS.md](LIMITATIONS.md) first.

Not affiliated with or endorsed by Anthropic or by the authors of the ACE paper.

## What it is

An agent that is told the same things in every session never learns which of them matter.
This project keeps an **append-only store of short lessons** extracted from past sessions, records
**which lessons were shown in which session** (with a randomized hide/show holdout), and is built
so that lessons which prove their worth can later be **compiled into enforcing hooks** instead of
staying as prose the agent may ignore. The compile step and the evaluation harness are planned
(see below); stage 1 builds everything underneath them.

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

Planned and **not built**: the compiler, curator and refiner, migration of any existing lesson
set, the replay and task-suite evaluation harness, the judge. See [PREREGISTRATION.md](PREREGISTRATION.md).

Design rules that shape the code: no model output is applied as a rewrite; lessons are minted only
from evidence the user actually wrote; a scrub hit sends the whole lesson to quarantine; the
injected block is advisory data, not instructions; hooks fail open and stay out of the way.
[SECURITY.md](SECURITY.md) has the threat model.

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
