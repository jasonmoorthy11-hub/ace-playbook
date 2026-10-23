# The acewatch sandbox (W2): the second user that reads the web

Status: BUILT, NOT LIVE. Nothing here is installed by ace-playbook; every step is the operator's. The model stage is also off in the
config (`watch_reader` is false by default).

## Why a second user

The watcher reads text written by strangers. W1 does that as the operator, in the same process that reads the pinned lines of the operator's
`~/.claude` files. W2 splits the two powers (Rule of Two): a process that reads untrusted text has no power to act.

- The Reader is a `claude -p` call with no tools, no MCP servers, no session, one turn, a cost cap, an empty scratch directory as its working
  directory and an environment without the API key variables. It sees ONE item (its title and summary lines, at most 4 000 characters, the
  hidden HTML already stripped). It never sees a URL it can fetch, a tool, the inventory file, another item or any file. Its answer is a JSON
  object that code validates (`validate.py`); the model writes no diff, and a tier-1 item can only ever become a `lead` (title at most 120
  characters, escaped where it is shown).
- The process around it (fetch, parse, store) runs as `acewatch`, a user with no way into the operator's home, so even a fetch the SSRF guard
  misjudges has no secrets to find there.

## Creating the user (the operator, once)

1. `sudo sysadminctl -addUser acewatch -fullName "ACE watcher" -home /var/acewatch -shell /usr/bin/false` WITHOUT `-admin`, with a password you
   discard (nobody logs in as it). The user is not in `admin`, and gets no sudo rights: no sudo, no sudoers entry, now or later.
2. `/var/acewatch` is owned by `acewatch`, mode 0700. It holds `.config/ace-playbook/` (0700), `logs/` and the watch directory.
3. Give it a checkout copy it can read but not change, OUTSIDE the operator's home (for example a root-owned copy with its own `.venv`); the
   plist templates call it `__ACE_PLAYBOOK_COPY__`. It must not be able to read the operator's home, `~/.claude`, `~/.ssh` or the ACE key
   directory (the default modes already refuse it; check with `sudo -u acewatch ls` on each).
4. `acewatch`'s own config, `ACE_PLAYBOOK_CONFIG` in the plists, is `/var/acewatch/.config/ace-playbook/config.json` (0600): its own
   `watch_dir` (0700, owned by `acewatch`, the ONE writable directory), `watch_sources`, `watch_inventory_map`, `watch_reader`,
   `watch_reader_model` and `watch_claude`. It never names `chain_key_path` or `api_key_path`.
5. The inventory: the operator copies the pinned files the map names into `<watch_dir>/inputs/` (owned by the operator, read-only, mode 0444 in a
   directory `acewatch` cannot write) and points `acewatch`'s `watch_inventory_map` at those copies. The scan reads them; it cannot change them.
6. Its own login for `claude`: as `acewatch`, run `claude setup-token` and keep the token in ONE file in its config dir, mode 0600, for example
   `/var/acewatch/.config/ace-playbook/claude_credential`. The token is never typed into a transcript, never written by the operator's
   Claude, never put in a plist (they are world-readable). Point `watch_claude` at a small wrapper that is mode 0700, owned by `acewatch`,
   reads that file and runs the real claude with `CLAUDE_CODE_OAUTH_TOKEN` set from it for that one process, then execs it with the arguments
   it was given. (Keep `ANTHROPIC_API_KEY` out of that environment: the Reader drops it anyway.)
7. Install the two templates by hand as root: `hooks/acewatch/com.jasonmoorthy.acewatch.plist` (tier 0, daily 04:30 local) and
   `hooks/acewatch/com.jasonmoorthy.acewatch.weekly.plist` (tier 1, Sundays 05:00): fill in the two placeholders, copy to
   `/Library/LaunchDaemons` (root:wheel, 0644), `plutil -lint` them, `launchctl bootstrap system <file>`.

## What it can and cannot do

- Writes: only its watch directory (`proposals/`, `decisions.jsonl`, `seen.json`, `inventory.json`, `scan.lock`) and `logs/`.
- Reads: its own config, the checkout copy, the `inputs/` copies, the network.
- The network beyond the sources' hosts is NOT enforceable by launchd. The fetcher refuses private and loopback addresses and redirects to
  another host, and only the hosts in `watch_sources` are ever requested, but a process running as `acewatch` could open any outbound
  connection. A `pf` rule (an anchor that allows `acewatch` to reach only the source hosts) or a local proxy is the operator's optional step;
  without it, treat the egress as open.
- Not built in W2: moving proposals from `acewatch`'s watch directory to the operator's own (`compile watch queue` appends decisions, so the
  operator reviews a copy of `proposals/W-*.json` in a watch directory of their own). Copy only those files, as the operator; a copied file
  is read through the same store readers, which skip anything of the wrong shape.

## Kill files

- `<watch_dir>/DISABLED` stops the whole scan (`watch: disabled`).
- `<watch_dir>/READER_DISABLED` stops only the model stage: the rule path of W1 keeps running and the scan prints `reader: disabled`.
- `watch_reader` false (the default) is the same as no model stage at all.

## The canary

`python -m ace_playbook.compile watch canary` runs `tests/fixtures/watch/canary.xml` (planted injections: a request for a model pin, a request
for extra JSON keys, text hidden in HTML, a 5 000-character body) through the real Reader path with the configured claude, writes nothing and
prints `canary: 0 proposals` (exit 0) or `canary: N proposals` (exit 1). Run it weekly as `acewatch` (`sudo -u acewatch` from the operator's
shell, with `ACE_PLAYBOOK_CONFIG` set to its config). Exit 2 (`canary: reader unavailable`) means the claude could not be run and the check
proved nothing; a model change (`watch_reader_model`) is a reason to run it before the next scan.

## How to rotate the token

Run `claude setup-token` again as `acewatch`, replace the one file (mode 0600), run the canary, then revoke the old token in the account
settings. Nothing else holds a copy; a leak of the file is a leak of that one login, which has no connectors and no access to the operator's
data.

## Uninstalling

`sudo launchctl bootout system/com.jasonmoorthy.acewatch` and `sudo launchctl bootout system/com.jasonmoorthy.acewatch.weekly`, remove the two
plists from `/Library/LaunchDaemons`, then `sudo sysadminctl -deleteUser acewatch` (this removes its home, so copy out anything you want
first) and revoke its token.
