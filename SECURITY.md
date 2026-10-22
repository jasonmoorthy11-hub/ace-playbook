# Security

## Reporting

Please use the repository's private vulnerability reporting (Security tab, "Report a
vulnerability") rather than a public issue. This is a single-maintainer research project:
expect a best-effort response, not an SLA.

## Threat model summary

The reflector reads transcripts, and transcripts contain hostile text: fetched web pages,
third-party READMEs, cloned repositories and command output. Whatever the reflector turns into a
lesson is injected at the start of every future session. The realistic attack is indirect prompt
injection into memory, not a query-only attacker. Published results show writing to memory is
easy while the behavioral payoff varies widely (from near zero to very high depending on setup),
so this design relies on structural controls rather than on a classifier.

Other assets at risk: credentials and personal data that pass through transcripts, the integrity
of the lesson store, and the settings and hooks of the agent that consumes it.

## Controls in stage 1 (in code, with tests)

1. **Provenance-gated minting.** A lesson needs a verbatim evidence quote found in a user message
   or in a project file the user owns that the agent did not write. Quotes found only in tool
   output, assistant text or nowhere are quarantined. The schema also refuses a live status
   without user-origin evidence.
2. **Inert, non-authoritative injection.** The injected block is delimited, labelled advisory data,
   capped at 7,000 characters, and built only from lessons that pass an inertness check (no URLs,
   code, shell syntax, markup, override phrases or tool-directed imperatives). A lesson that fails
   is left out, never rewritten.
3. **Quarantine, not redaction.** The scrubber runs on outbound text (masking, for data
   minimization), on every returned lesson (hit means the whole lesson goes to an untracked
   quarantine file), in the store writer (which refuses text with hits) and as a repo check.
4. **Append-only, hash-chained log** with all-or-nothing atomic writes, a per-run limit on lessons
   touched and on new lessons, and a verifier for tampering.
5. **Fail-open hooks with no network.** Stage-1 hooks never block a session; stage-2 shadow hooks
   block only a proposal the operator promoted to block mode after the observe/warn ladder (see
   docs/design/compiler.md; amended 2026-10-19). No hook calls the API; only the reflector does,
   with no tools, from a path-configured key that is never logged.
6. **Reflector prompt** tells the model to ignore instructions inside tool results. This is the
   weakest control and is not relied on.

## The brief on wake (optional)

`hooks/brief.sh` is a read-only principal: it may read private data (calendar, mail, Linear) and has no write tool, no send and no
outbound channel. Three controls, each doing a different job:

1. **Scope.** Stage 1 loads only `--tools ToolSearch` as a built-in (no file or shell tool exists in it) and `--allowedTools` names the
   read tools from `hooks/brief_allowed_tools.txt`. This narrows what the model is offered and pre-approves; it is not the enforcement.
2. **The guard** (`hooks/brief_guard.py`, a PreToolUse hook): the hard control. It is deny-by-default (any tool, connector or built-in,
   not named on the allowlist is denied) and fails closed on any parse, read or other error. One gap is outside our code: Claude Code
   treats a hook that exceeds its timeout as non-blocking, so the timeout is 30 seconds (a cold `python3` on wake) and the settings
   command ends in `|| exit 2` so a crash, though not a timeout, blocks.
3. **The deny lists**, the backstop for connector writes: `hooks/brief_denied_tools.txt` is passed as `--disallowedTools` and repeated in
   the settings file's `permissions.deny` (with the built-in file, shell and web tools). It names every write tool of the three servers the
   brief reads by full name and every other connector as a whole server; a test keeps the two lists equal.

Stage 1 emits a JSON digest and code applies the private exclusion list; stage 2 has no tools (`--tools "" --strict-mcp-config`) and sees
only the cleaned digest. Stage 1 cannot use `--strict-mcp-config`: the claude.ai connectors are account-level and cannot be named in an
mcp-config file (phase 0: strict mode showed zero MCP servers), so strict mode would remove the only data sources. Both stages pass
`--no-session-persistence` (no transcript of raw mail where the nightly reflect sweep reads) and `--setting-sources project,local` (no
user hook runs inside the brief). Everything gathered is treated as data, never instructions. No model writes HTML that is served: the
model emits JSON, validated and capped by code, and code renders the page with every field escaped, a `default-src 'none'` CSP, no script
and no external resource (D-110). A link is an outbound channel, so a `url` survives validation only on `mail.google.com`,
`calendar.google.com` or `linear.app` (https, exact host, no userinfo, no port). Every call passes an explicit model and runs without
`ANTHROPIC_API_KEY`. None of this is on the hook path; the brief runs `claude -p`, which is the one network use, and only on your login.

## Planned, not implemented

A compiler that emits specs and diffs only, behind a human gate and a second reviewer that sees
provenance; a `ConfigChange` audit hook; canary lessons and fake-secret fixtures fed through the
pipeline on a schedule; signature or hash verification of the log at session start; an LLM
classifier used only as a signal to human review. The compiler must never output executable text
that is applied automatically.

## Do not commit

Real lesson text, evidence quotes, transcript excerpts, session ids, client or ticket names,
project ids, internal hostnames, home-directory paths, API keys, `.env` files, or any ledger,
quarantine file or SQLite view. `.gitignore` excludes the usual names; CI runs
`python -m ace_playbook.scrub check` on every push. Fixtures are synthetic and every planted
credential carries the marker `FAKE`. If a real credential ever lands in a file or in history,
rotate it first; deleting the file does not remove it from history.

## Operational hygiene

Do not run the reflector as an agent session with an untrusted clone as its working directory;
the reflector here is a direct API call with no tools. Do not enable live secret verification
tools against real data. Keep the API key in a dedicated, spend-capped workspace.
