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
   `compile watch` (the watcher) is the one network-using module; it is not on the hook path and the conformance test asserts that.
   The watcher's own limits (see LIMITATIONS.md, "Watcher"): in W1 it fetches as the operator, in the same process that reads the matched
   `~/.claude` lines and runs `claude --version`; there is no `acewatch` user and no egress allowlist until W2. Its SSRF guard drops private,
   loopback, link-local and metadata addresses before a request and follows a redirect only to the same scheme, host and port (at most three
   hops, no userinfo), but it checks the address before the connection, not on the connected socket, so a DNS-rebinding race remains. Its
   sanitizer handles inline styles and simple single-selector `<style>` rules only (not media queries, descendant or attribute selectors, or
   external stylesheets). Web text is never a diff, a target or a command; it appears only as a title or a quote.
6. **Reflector prompt** tells the model to ignore instructions inside tool results. This is the
   weakest control and is not relied on.

## The brief on wake (optional)

`hooks/brief.sh` is a read-only principal: it may read private data (calendar, mail, Linear) and has no write tool, no send and no
outbound channel. Its controls, in order:

1. `--tools ToolSearch`: no built-in file, shell or web tool is loaded at all.
2. `--allowedTools` from `hooks/brief_allowed_tools.txt`: the read allowlist (a calendar, a mail and an issue read).
3. `--disallowedTools` from `hooks/brief_denied_tools.txt`, repeated as `permissions.deny` in `hooks/brief-settings.json`: every tool of
   Gmail, Google Calendar and Linear that is not on the allowlist, by full name (`hooks/brief_connector_tools.txt` is a dated snapshot of the
   real tool list and a test checks each of its names is allowed or denied), every other connector as a whole server, and the built-in file, shell and
   web tools.
   A denied tool is hidden from the model (probed: it reports the tool as not available). A test keeps the two lists equal.
4. `-p` is non-interactive, so a tool that is not allowed is denied rather than asked, with a caveat (D-117): the operator's own user-level
   `permissions.allow` and `defaultMode` still apply inside the run, so an unlisted tool is not denied by `-p` alone. That is why stage 1
   passes `--permission-mode default` and the deny file names every connector tool the brief does not need, reads included.
   **Residual:** a connector tool added after the snapshot, or a user-scope MCP server that is not in it, is NOT stopped by `--tools`
   (built-ins only) or `--allowedTools` (it only adds allows). With a server-wide or wildcard `permissions.allow` entry in the operator's
   user settings (`mcp__claude_ai_Gmail`, `mcp__*`) such a tool is open to stage 1, a new Gmail write tool included. The operator must never
   keep such entries for Gmail, Calendar or Linear. After stage 1, `tools-audit` reads the stream's init tool list and its tool calls and
   refuses the run (exit 2, digest discarded, stage 2 never starts, notification without names) when ANY tool outside the allowlist and
   `ToolSearch` appears, a built-in one too: it detects the drift, it cannot undo a call that already ran. The ledger row and the one-line
   summary name only tools of the checked-in lists, the built-ins and claude's own init list; a name the model invents is counted
   (`unknown`), never written down. It fails closed: a stream line that is not a JSON object, a call block (`tool_use`, `server_tool_use`,
   `mcp_tool_use`) whose name is not a string, or a stream over 50 MB, is refused as `unparseable`; a raw scan names the tools a malformed
   line shows (a parsed line is read by structure, so a tool name inside a mail body or an allowed tool's input is data, not a call).
5. `--no-session-persistence`: no transcript of the raw mail is written where the nightly reflect sweep reads (probed: only an empty
   per-directory folder appears).
6. `disableAllHooks` in the settings file: the operator's own hooks (inject, citation ledger, holdout draws) do not run inside the brief.

The PreToolUse guard is not active in the brief because `disableAllHooks` is required to keep the operator's own hooks out of the run; it is
kept (`hooks/brief_guard.py`, deny by default, fails closed, tested) for principals that run with hooks. Two limits follow: the brief relies
on tool scoping and deny lists, not on a hook, and `--setting-sources` cannot be used to drop the user hooks because it also drops the
claude.ai connectors (probed 2026-10-22). Stage 1 cannot use `--strict-mcp-config` either: the connectors are account-level and cannot be
named in an mcp-config file; stage 2 (no tools) does. Stage 1 emits a JSON digest and code applies the private exclusion list; stage 2
(`--tools "" --strict-mcp-config`) sees only the cleaned digest. Everything gathered is treated as data, never instructions. No model
writes HTML that is served: the model emits JSON, validated and capped by code, and code renders the page with every field escaped, a
`default-src 'none'` CSP, no script and no external resource (D-110). A link is an outbound channel (a query string can carry mail text), and
the digest has no url field, so any url in a brief was invented by a model: the validator sets every `url` to null and the page has no
link. The renderer keeps its host allowlist (`mail.google.com`, `calendar.google.com`, `linear.app`; https, exact host, no userinfo, no
port, at most 500 characters, no control or format character) only as defence in depth. Every call passes an explicit model and
runs without `ANTHROPIC_API_KEY`. None of this is on the hook path; the brief runs `claude -p`, which is the one network use, and only on
your login.

Two more controls. One run at a time: a kernel `flock` (python `fcntl` on fd 9) that the kernel releases when the run dies, so a live run is never taken
over; each stage has a wall-clock limit (`ACE_BRIEF_STAGE_TIMEOUT`, 600 s). The private exclusion match runs in a child process that
`brief_excl.py` SPAWNS (a fresh interpreter, killed after 10 s; D-116): a `re` match in C cannot be interrupted in-process, and the child
sees only the lists and the mail it is handed. That file is the one place under `ace_playbook/compile/` allowed to import
`multiprocessing`; `tests/test_compile_events.py` bans the module everywhere else and names this exemption (a stale exemption fails too).
Spawn is not a shell or a network use, but it does start a process, which the "never spawn" rule of that test otherwise forbids.

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
