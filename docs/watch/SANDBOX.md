# The acewatch sandbox (W2): the second user that reads the web

Status: BUILT, NOT LIVE. Nothing here is installed by ace-playbook; every step is the operator's. The model stage is also off in the
config (`watch_reader` is false by default).

## Why a second user

The watcher reads text written by strangers. W1 does that as the operator, in the same process that reads the pinned lines of the operator's
`~/.claude` files. W2 splits the two powers (Rule of Two): a process that reads untrusted text has no power to act.

- The Reader is a `claude -p` call with no tools, no MCP servers, no session, one turn, a cost cap, a fixed empty directory
  (`<watch_dir>/reader_cwd`) as its working directory and an environment without the API key variables. It also runs with
  `--settings hooks/reader-settings.json` (`{"disableAllHooks": true}`) and `--setting-sources ""`, so no hook, `CLAUDE.md` or memory of the
  account it runs as is loaded into the call. It sees ONE item (its title and summary lines, at most 4 000 characters, the hidden HTML already
  stripped). It never sees a URL it can fetch, a tool, the inventory file, another item or any file. Its answer is a JSON object that code
  validates (`validate.py`); the model writes no diff, and a tier-1 item can only ever become a `lead` (title at most 120 characters, shown
  quoted).
- **The Reader runs ONLY as acewatch.** Turning it on in the operator's own config is NOT a supported setup: the operator's hooks and
  `CLAUDE.md` would otherwise load inside the quarantined call. `--settings` with `disableAllHooks` and `--setting-sources ""` are the belt; the
  separate user is the braces.
- The process around it (fetch, parse, store) runs as `acewatch`, a user that cannot read the operator's home once the check in step 1 passes,
  so even a fetch the SSRF guard misjudges has no secrets to find there.

## Creating the user (the operator, once)

1. Give `acewatch` a group of its own, then the user (WITHOUT `-admin`, with a password you discard: nobody logs in as it). The user is not in
   `admin`, and gets no sudo rights: no sudo, no sudoers entry, now or later.

   ```
   sudo dseditgroup -o create -r "ACE Watch" acewatch
   sudo sysadminctl -addUser acewatch -fullName "ACE Watch" -shell /bin/bash -home /var/acewatch \
     -GID $(dscl . -read /Groups/acewatch PrimaryGroupID | awk '{print $2}') -password - interactive
   sudo -u acewatch ls ~<operator>     # MUST print "Permission denied"; if it lists files: chmod 0700 ~<operator>
   ```

   `sysadminctl -addUser` puts a new user in group 20 (`staff`) by default, the group of an ordinary operator account, and a home directory is
   typically `drwxr-x---` for `staff`: without its own primary group `acewatch` could list the operator's home and read every file there that
   `staff` or the world may read (`~/.claude/settings.json`, `CLAUDE.md`). The `ls` above MUST fail before you install anything; run the same
   check on `~<operator>/.claude`, `~<operator>/.ssh` and the ACE key directory (each must print `Permission denied`). Only after those
   checks pass does `acewatch` have no way into the operator's files.
2. `/var/acewatch` is owned by `acewatch`, mode 0700 (create it if `sysadminctl` did not: `sudo mkdir -p /var/acewatch/.config/ace-playbook
   /var/acewatch/logs`, then `sudo chown -R acewatch:acewatch /var/acewatch` and `sudo chmod 0700 /var/acewatch`). It holds
   `.config/ace-playbook/` (0700), `logs/` and the watch directory (which holds `reader_cwd`, the Reader's fixed empty working directory, 0700).
3. Give it a checkout copy it can read but not change, OUTSIDE every user's home (a root-owned copy; the plist templates call it
   `__ACE_PLAYBOOK_COPY__`). The copy is a FULL checkout including `tests/fixtures/watch/` (the canary verb reads `canary.xml` from it),
   not a wheel, not `--no-editable`. Build its `.venv` from a python outside any user home: as root, in the copy,
   `UV_PYTHON_INSTALL_DIR=/opt/ace-python uv sync` (or point the venv at a system-wide 3.12). A venv built in your own session symlinks its
   python into your home, which `acewatch` cannot read once step 1 holds. Check: `sudo -u acewatch <copy>/.venv/bin/python -c "import ace_playbook"`.
4. `acewatch`'s own config, `ACE_PLAYBOOK_CONFIG` in the plists, is `/var/acewatch/.config/ace-playbook/config.json` (0600): its own
   `watch_dir` (0700, owned by `acewatch`, the ONE writable directory), `watch_sources`, `watch_inventory_map`, `watch_reader`,
   `watch_reader_model` and `watch_claude`. It never names `chain_key_path` or `api_key_path`.
5. The inventory: the operator copies the pinned files the map names into `<watch_dir>/inputs/` (owned by the operator, read-only, mode 0444 in a
   directory `acewatch` cannot write) and points `acewatch`'s `watch_inventory_map` at those copies. The scan reads them; it cannot change them.
6. Its own claude and its own login. Install claude AS `acewatch` (its own claude install: its own `~/.local/bin/claude` under `/var/acewatch`,
   never the operator's binary and never a symlink into the operator's home). Then, as `acewatch`, run `claude setup-token` and keep the token in
   ONE file in its config dir, mode 0600, for example `/var/acewatch/.config/ace-playbook/claude_credential`. The token file is consumed by the
   mechanism `claude setup-token` documents (observed string in the installed binary: CLAUDE_CODE_OAUTH_TOKEN;
   verify against the installed version). The token is never typed into a transcript, never written by the operator's Claude, never put in a
   plist (they are world-readable). Point `watch_claude` at a small wrapper that is mode 0700, owned by `acewatch`, reads that file and runs
   `acewatch`'s own claude by its absolute path with the credential set from it for that one process, then execs it with the arguments it was
   given. (Keep `ANTHROPIC_API_KEY` out of that environment: the Reader drops it anyway.)
7. Install the two templates by hand as root: `hooks/acewatch/com.jasonmoorthy.acewatch.plist` (tier 0, daily 04:30 local) and
   `hooks/acewatch/com.jasonmoorthy.acewatch.weekly.plist` (tier 1, Sundays 05:00): fill in the two placeholders, copy to
   `/Library/LaunchDaemons` (root:wheel, 0644), `plutil -lint` them, `launchctl bootstrap system <file>`.

## What it can and cannot do

- Writes: only its watch directory (`proposals/`, `decisions.jsonl`, `seen.json`, `inventory.json`, `scan.lock`, `reader_cwd/`) and `logs/`.
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
  `watch canary` ignores READER_DISABLED (it is the check that the Reader still refuses, so it runs whatever the switches say).
- `watch_reader` false (the default) is the same as no model stage at all. A `--dry-run` scan skips the model stage (`reader: skipped (dry run)`).

## The canary

`python -m ace_playbook.compile watch canary` runs `tests/fixtures/watch/canary.xml` (planted injections: a request for a model pin, a request
for extra JSON keys, text hidden in HTML, a 5 000-character body) through the real Reader path with the configured claude, writes nothing and
prints `canary: 0 proposals` (exit 0) or `canary: N proposals` (exit 1). Run it weekly as `acewatch`, from outside the checkout and with the
environment spelled out (sudo drops it otherwise):

```
cd / && sudo -u acewatch -H env PATH=/usr/bin:/bin \
  ACE_PLAYBOOK_CONFIG=/var/acewatch/.config/ace-playbook/config.json \
  <copy>/.venv/bin/python -m ace_playbook.compile watch canary
```

The verb uses exit 2 (`canary: reader unavailable` or `canary: feed missing`) ONLY when the claude could not be run (missing, or not spawnable)
or the feed is not in the copy: the check proved nothing. A call that ran and failed (an error envelope, a non-zero exit, a timeout) is `canary: 0 proposals`
with exit 0 and `canary: F failed` on stderr: read stderr. A model change (`watch_reader_model`) is a reason to run it before the next scan.

## How to rotate the token

Run `claude setup-token` again as `acewatch`, replace the one file (mode 0600), run the canary, then revoke the old token in the account
settings. Nothing else holds a copy; a leak of the file is a leak of that one login, which has no connectors and no access to the operator's
data.

## Uninstalling

`sudo launchctl bootout system/com.jasonmoorthy.acewatch` and `sudo launchctl bootout system/com.jasonmoorthy.acewatch.weekly`, remove the two
plists from `/Library/LaunchDaemons`, then `sudo sysadminctl -deleteUser acewatch` (this removes its home, so copy out anything you want
first), `sudo dseditgroup -o delete acewatch` and revoke its token.
