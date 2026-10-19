# Datasheet: ACE playbook synthetic fixture corpus

Following the "datasheets for datasets" outline, shortened to what this corpus needs.

## Motivation

Why it exists. The pipeline reads session transcripts and writes lessons. Real transcripts
and real lessons contain client names, ticket ids, paths and credentials, so none of them
may be published. This corpus lets anyone run the tests, the scrubber and the demo without
any private data. It is not a benchmark and supports no claim about how well the system
works on real sessions.

Who made it and who funds it. A single author, as part of this repository. No outside
funding.

## Composition

Everything is invented. A seeded generator (`python -m ace_playbook.fixtures.generate
--seed N --out DIR`) writes, as a pure function of the seed:

| Path | Contents |
|---|---|
| `deltas.jsonl` | About 31 invented lessons across three invented projects (`acme-widgets`, `globex-api`, `initech-site`) plus global ones, with ADD, PATCH, MERGE, RETIRE and counter-only NOOP deltas, a valid hash chain, and ten pinned lessons |
| `engagements.json` | Alias and path map for the key resolver |
| `transcripts/*.jsonl` | Five short Claude Code-shaped transcripts, each built to exercise one behavior: a grounded correction, harness noise and a hook block, a repeated failing command, planted fake credentials, and a hostile instruction inside fetched content |
| `reflector_responses/*.json` | Canned reflector outputs matching those transcripts, so the reflector runs end to end with a mocked HTTP layer and no API call. They include an invented quote, a lesson grounded only in tool output, and a lesson containing a fake key |
| `planted/candidates.jsonl` | Candidate lesson texts labelled with the outcome the pipeline must produce (`mint`, `inert_reject`, `quarantine`) |
| `shadow_corpus.json` | Four invented sessions of Bash commands (`/w/...` paths), each event tagged with the rm detector it is checked against and the outcome it must give; `compile replay-synthetic` feeds it to the shadow evaluator. Not seeded: the same bytes for every seed |
| `MANIFEST.json` | Seed, generator version and SHA-256 of every file |

`ace_playbook/fixtures/frozen/` is the output for seed 1, committed so the repository's
secret-scan allowlist and the tests have a fixed target. A test regenerates seed 1 and
fails if the frozen copy has drifted.

## Planted fake secrets and PII

The corpus deliberately contains token-shaped strings for each scrubber rule (Anthropic,
OpenAI, GitHub, JWT, Railway, Vercel, Linear, Neon, bearer), base64-encoded copies, a fake
email and a fake home-directory path. All are invented, none is valid for any service, and
each carries the marker `FAKE` in the matched text. They exist to be detected.

Handling in this repository: `python -m ace_playbook.scrub check --mode audit` ignores the
allowlist and reports them (all inside `ace_playbook/fixtures/frozen/`). `--mode pre-commit`
tolerates a hit under that directory only when the matched text, or its base64 decoding,
contains `FAKE`; a real-looking secret pasted into a fixture still fails the check.

## Collection process

None. Nothing was collected, scraped, sampled or derived from real lessons or real
transcripts. Lesson texts are written by hand as generic engineering conventions
(for example "use python3, not python"); the generator only selects, orders, assigns scopes
and counters. No fixture was produced by editing a real lesson.

## Uses and non-uses

Intended: unit and integration tests, scrubber and injector demos, documentation examples.

Not valid: estimating real-world repeat-error rates, lesson quality, or any effect size.
Counters are random draws and carry no information. Transcripts are far shorter and cleaner
than real sessions, and the hostile-instruction transcript is one hand-written example, not
a sample of attacks.

## Distribution and maintenance

Distributed with the code under Apache-2.0. The generator version is recorded in
`MANIFEST.json`; any change to generated content bumps `GENERATOR_VERSION` and regenerates
the frozen copy in the same commit.

## Known limitations

Fake tokens are generated to match the scrubber's own patterns, so detection on this corpus
is circular and says nothing about recall on real credentials. Providers' real formats can
differ from the shapes assumed here.
