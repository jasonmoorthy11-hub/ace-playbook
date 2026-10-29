# The acewatch sandbox (W2): the second user that reads the web

Status: BUILT, NOT LIVE. Nothing here is installed by ace-playbook; every step is the operator's. The model stage is also off in the
config (`watch_reader` is false by default).

## Why a second user

The watcher reads text written by strangers. W1 does that as the operator, in the same process that reads the pinned lines of the operator's
`~/.claude` files. W2 splits the two powers (Rule of Two): a process that reads untrusted text has no power to act.

- The Reader is a `claude -p` call with no tools, no MCP servers, no session, one turn, a cost cap, a fixed empty directory
  (`<watch_dir>/reader_cwd`) as its working directory and an environment without the four variables `ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` and `CLAUDECODE` (`CLAUDE_CODE_OAUTH_TOKEN`, its own login, passes). It also runs with
  `--settings hooks/reader-settings.json` (`{"disableAllHooks": true}`, which the code requires to be exactly that, else no call) and
  `--setting-sources ""`: hooks are off and the user, project and local settings files are dropped (checked by the release probe on the installed
  binary: no hook events, no MCP servers, no tools, one turn). `CLAUDE.md` and auto-memory are kept out by the separate user (below), not by a
  flag; the release probe plants a `CLAUDE.md` sentinel and reports whether it reaches the answer. It sees ONE item (its title and summary lines,
  at most 4 000 characters, the hidden HTML already stripped). It never sees a URL it can fetch, a tool, the inventory file, another item or any
  file. Its answer is a JSON object that code validates (`validate.py`); the model writes no diff, and a tier-1 item can only ever become a
  `lead` (title at most 120 characters, shown quoted). An entry the W1 rules already made a lead for is not sent to it at all, at most 5 reader
  leads are kept per scan (the rest are read on later scans), and a failed health probe after failed calls ends the run as an outage (what that
  costs, and what it does not: see "Outages and bad items" and "The canary"). The queued items are dealt round-robin across the sources before the
  cut to 20, so no one source fills the window.
- **The Reader runs ONLY as acewatch.** Turning it on in the operator's own config is NOT a supported setup: the operator's hooks and
  `CLAUDE.md` would otherwise load inside the quarantined call. `--settings` with `disableAllHooks` and `--setting-sources ""` are the belt; the
  separate user is the braces, and the braces are what keep `CLAUDE.md` and memory out.
- The process around it (fetch, parse, store) runs as `acewatch`, a user that cannot read the operator's home once the check in step 1 passes,
  so even a fetch the SSRF guard misjudges has no secrets to find there.

## Creating the user (the operator, once)

The commands below follow the shape of the commands run on macOS 26 (2026-10-23); the chain-check loop, the `.inputs.old` name, the by-hand
install and the `inputs.legacy-<date>` name are written from that run, not pasted from it. `<operator>` is your own account name, `~<operator>` its home.

1. Give `acewatch` a group of its own, then the user (WITHOUT `-admin`, with a password you discard: nobody logs in as it). The user is not in
   `admin`, and gets no sudo rights: no sudo, no sudoers entry, now or later.

   ```
   sudo dseditgroup -o create -r "ACE Watch" acewatch
   sudo sysadminctl -addUser acewatch -fullName "ACE Watch" -shell /bin/bash -home /var/acewatch \
     -GID $(dscl . -read /Groups/acewatch PrimaryGroupID | awk '{print $2}') -password -
   sudo chmod 0700 ~<operator>
   sudo -u acewatch ls ~<operator>
   sudo -u acewatch ls ~<operator>/.claude
   sudo -u acewatch ls ~<operator>/.ssh
   sudo -u acewatch ls ~<operator>/.local/share/ace-playbook
   dsmemberutil checkmembership -U acewatch -G staff
   ```

   The command ends in `-password -`, which makes `sysadminctl` ask for the password on its own; do not add the word `interactive` after it, because
   macOS 26 rejects it ("Invalid option: (interactive)"). Discard the password you type.

   The `chmod 0700 ~<operator>` is the EXPECTED step, not an exception: a default macOS home is `drwxr-xr-x`
   (755, or 750 for group `staff` on some releases; the chmod is needed either way), so any local user (or any `staff` member) can list it,
   and `sysadminctl -addUser` puts a new user in group 20 (`staff`), the group of an ordinary operator account (and local users can reach `staff`
   through nested groups too). Without its own primary group and the chmod, `acewatch` could list the operator's home and read every file there that
   `staff` or the world may read (`~/.claude/settings.json`, `CLAUDE.md`). Run the chmod, then the four `ls` lines above (the home, `.claude`,
   `.ssh` and the ACE config directory `~<operator>/.local/share/ace-playbook`): ALL FOUR MUST print `Permission denied`. The
   `dsmemberutil` line is information: "is a member" (nested `staff`) is normal on a stock account, and "not a member" is fine too; the chmod is what
   closes the door. In one sentence: the `ls` check, not the group, is the guarantee; only after those four checks pass does `acewatch` have no way
   into the operator's files.
2. `/var/acewatch` is owned by `acewatch`, mode 0700 (create it if `sysadminctl` did not: `sudo mkdir -p /var/acewatch/.config/ace-playbook
   /var/acewatch/logs`, then `sudo chown -R acewatch:acewatch /var/acewatch` and `sudo chmod 0700 /var/acewatch`). It holds
   `.config/ace-playbook/` (0700), `logs/`, `data/` and the watch directory (which holds `reader_cwd`, the Reader's fixed empty working directory,
   0700). Anything that has to be moved aside inside this tree is parked IN PLACE, as `acewatch` (`mv inputs inputs.legacy-<date>` inside the same
   parent): a cross-parent directory rename needs write permission on the directory itself, which a directory the operator owns does not give
   `acewatch`, and root never deletes inside the tree, with ONE exception (step 5): a parked copy that the OPERATOR owns can only be removed by
   root, or may stay parked.
3. Give it a checkout copy it can read but not change, OUTSIDE every user's home (a root-owned copy; the plist templates call it
   `__ACE_PLAYBOOK_COPY__`). The copy is a FULL checkout including `tests/fixtures/watch/` (the canary verb reads `canary.xml` from it),
   not a wheel, not `--no-editable`. Build its `.venv` from a python outside any user home, as root, with the environment spelled out (sudo
   drops it otherwise, and a `uv` shim in your home would pick your python):

   ```
   cd <copy> && sudo -H env PATH=/usr/bin:/bin UV_PYTHON_INSTALL_DIR=/opt/ace-python UV_PYTHON_PREFERENCE=only-managed \
     UV_CACHE_DIR=/opt/ace-python/cache <absolute path of uv> sync --python 3.12
   ```

   Pin the python with `--python 3.12` (the version CI tests): without it `uv sync` takes the NEWEST managed python (3.15 on 2026-10-23), which
   worked in the live install but is untested in CI. (Or point the venv at a system-wide 3.12.) A venv built in your own session symlinks its
   python into your home, which `acewatch` cannot read once step 1 holds. Check: `sudo -u acewatch <copy>/.venv/bin/python -c "import ace_playbook"`.
4. `acewatch`'s own config, `ACE_PLAYBOOK_CONFIG` in the plists, is `/var/acewatch/.config/ace-playbook/config.json` (0600): its own
   `data_dir` and `watch_dir` (0700, owned by `acewatch`; the watch directory is the one directory the scan writes, Reader state included;
   `data_dir` is the base directory the config requires (0700, acewatch's); the scan writes nothing there), `watch_sources`,
   `watch_inventory_map`, `watch_reader`, `watch_reader_model` and `watch_claude`. It never names `chain_key_path` or `api_key_path`.
   As installed, with `watch_reader` still false (flip it after the first canary):

   ```
   {"data_dir": "/var/acewatch/data", "watch_dir": "/var/acewatch/watch",
    "watch_sources": "/var/acewatch/.config/ace-playbook/watch_sources.json",
    "watch_inventory_map": "/var/acewatch/.config/ace-playbook/watch_inventory_map.json",
    "watch_reader": false, "watch_reader_model": "claude-haiku-5-5", "watch_claude": "/opt/acewatch/bin/claude-reader"}
   ```

   (`watch_reader` stays false until step 6 is done and the canary has run: flip it to `true` after the first canary
   passes.) The three files in `.config/ace-playbook/` are installed by step 5.
5. The pinned inputs and the Reader wrapper live in a ROOT-OWNED tree, never under the acewatch-owned `watch/`: root must not write through a path
   `acewatch` could have planted a symlink on. `/opt/acewatch` is `root:acewatch` 0750; `/opt/acewatch/inputs/` holds the copies of the pinned files the
   map names (directories 0750, files 0440 `root:acewatch`: `acewatch` can read them, not change them) and `/opt/acewatch/bin/claude-reader` is the
   wrapper of step 6 (0750). The inputs tree is built in a temp directory beside it and swapped in by rename, so a scan never sees half of it
   (the old tree is root's own, so root may remove it):

   ```
   R=/opt/acewatch
   [ -L "$R" ] && { echo "refusing: $R is a symlink"; false; }       # MUST print nothing; stop here if it prints
   sudo install -d -o root -g acewatch -m 0750 $R $R/bin
   T=$(sudo mktemp -d $R/.inputs.XXXXXX)
   sudo install -d -o root -g acewatch -m 0750 $T/agents $T/brief_prompts
   sudo install -o root -g acewatch -m 0440 ~<operator>/.claude/settings.json ~<operator>/.claude/CLAUDE.md $T/
   sudo install -o root -g acewatch -m 0440 <every other single file the map names> $T/
   sudo install -o root -g acewatch -m 0440 ~<operator>/.claude/agents/*.md $T/agents/
   sudo install -o root -g acewatch -m 0440 <the brief prompt files the map names> $T/brief_prompts/
   sudo chown root:acewatch $T && sudo chmod 0750 $T
   if sudo test -e "$R/inputs"; then sudo mv "$R/inputs" "$R/.inputs.old"; fi
   sudo mv "$T" "$R/inputs" && { sudo test ! -e "$R/.inputs.old" || sudo rm -r "$R/.inputs.old"; }
   sudo install -o root -g acewatch -m 0750 <your copy of the claude-reader wrapper of step 6> $R/bin/claude-reader
   ```

   The `false` in the first check stops the block only under `set -e`; pasted into a terminal, the next lines would still run. So if the check
   prints, the rest of this block must NOT be pasted (or run the block as a script with `set -e`).

   Every test on a path under `$R` runs as root (`sudo test -e`), because the operator cannot search `/opt/acewatch` (`root:acewatch` 0750) and a
   plain `[ -e ... ]` would always say "absent": on a re-install the old tree would stay in place and `mv` would nest the new tree inside it.

   The three acewatch-owned config files (`watch_sources.json`, `watch_inventory_map.json`, `config.json`, each 0600) are installed only after a
   symlink and ownership check of the path chain, and are written AS `acewatch` from stdin, so root never writes into `acewatch`'s tree:

   ```
   C=/var/acewatch/.config/ace-playbook
   sudo bash -c 'for p in "$@"; do if [ -L "$p" ] || [ "$(stat -f %Su "$p")" != acewatch ]; then echo "refusing: $p"; fi; done' _ \
     /var/acewatch /var/acewatch/.config $C                 # MUST print nothing
   sudo -u acewatch bash -c 'umask 077; rm -f "$1"; cat > "$1" && chmod 0600 "$1"' _ $C/config.json < <your config.json>
   ```

   (the same last line for the other two files). The map points at `/opt/acewatch/inputs/...` (each `targets.*.path`, for example
   `/opt/acewatch/inputs/settings.json`) and `watch_claude` at `/opt/acewatch/bin/claude-reader`. The scan reads the pinned files; it cannot change
   them. A legacy `watch/inputs` or wrapper directory from an earlier install is handled as in step 2: a cross-parent rename needs write permission
   on the directory itself, so park it IN PLACE as `acewatch` (`sudo -u acewatch mv /var/acewatch/watch/inputs /var/acewatch/watch/inputs.legacy-<date>`),
   never move it out to an operator-owned folder. Removing it later depends on who owns it. An `acewatch`-owned old wrapper directory is
   removed as `acewatch` (`sudo -u acewatch rm -r <the parked directory>`). The legacy operator-owned `watch/inputs` copy is the ONE exception to
   "root never deletes inside the tree": `acewatch` cannot write that directory, so only root can remove it (`sudo rm -r <the parked directory>`,
   which does not follow symlinks), or it may stay parked. `acewatch` owns `/var/acewatch` and `watch/`, so it could swap a parent folder for a symlink:
   immediately before the `sudo rm -r`, run the symlink-and-owner check loop above on `/var/acewatch`, `/var/acewatch/watch` and the parked folder
   itself (not a link, owned as expected).
6. Its own claude and its own login. Install claude AS `acewatch` (its own claude install: its own `~/.local/bin/claude` under `/var/acewatch`,
   never the operator's binary and never a symlink into the operator's home), with the installer the installed version documents, run as
   `acewatch` with a bare environment:

   ```
   cd / && sudo -u acewatch -H env PATH=/usr/bin:/bin HOME=/var/acewatch /bin/bash -c "$(curl -fsSL https://claude.ai/install.sh)"
   ```

   The installer prints NOTHING until it reaches "Setting up Claude Code...": the download is about 275 MB for 2.1.309 and takes minutes. Watch it
   with `sudo ls -la /var/acewatch/.claude/downloads/` in a second terminal. The launcher lands at `/var/acewatch/.local/bin/claude`, a link to
   `/var/acewatch/.local/share/claude/versions/<ver>` (verified 2026-10-23); check it with `sudo -u acewatch -H /var/acewatch/.local/bin/claude --version`.

   If the installer's second phase prints `Download timed out: exceeded the total deadline` (slow links: that phase downloads the full binary again
   under its own deadline and deletes its download directory), install BY HAND, as `acewatch`. Fetch the release manifest
   `curl -fsSL https://downloads.claude.ai/claude-code-releases/<ver>/manifest.json` and read
   `.platforms["darwin-arm64"].checksum` (`darwin-x64` on Intel) and `.size` from it.
   Verify the binary you will place (`shasum -a 256 <file>` and `stat -f %z <file>`) against both: the verified copy may be the operator's own
   `~<operator>/.local/share/claude/versions/<ver>`. Then write it as `acewatch` (root never writes into the sandbox tree) and link it:

   ```
   sudo -u acewatch -H bash -c 'mkdir -p ~/.local/bin ~/.local/share/claude/versions'
   sudo -u acewatch -H bash -c 'cat > ~/.local/share/claude/versions/<ver> && chmod 0755 ~/.local/share/claude/versions/<ver>' < <file>
   sudo -u acewatch -H bash -c 'shasum -a 256 ~/.local/share/claude/versions/<ver>'
   sudo -u acewatch -H bash -c 'ln -sfn ~/.local/share/claude/versions/<ver> ~/.local/bin/claude'
   sudo -u acewatch -H /var/acewatch/.local/bin/claude --version
   ```

   The `shasum` line re-verifies the placed copy: it must print the manifest checksum before you run the `ln -sfn` line, and `--version` comes last.

   Then, as `acewatch`, run `claude setup-token` and keep the token in
   ONE file in its config dir, mode 0600, for example `/var/acewatch/.config/ace-playbook/claude_credential`. The token file is consumed by the
   mechanism `claude setup-token` documents (observed string in the installed binary: CLAUDE_CODE_OAUTH_TOKEN;
   verify against the installed version). The token is never typed into a transcript, never written by the operator's Claude, never put in a
   plist (they are world-readable). Save it at a silent prompt (paste ONCE, then Enter):

   ```
   sudo -u acewatch -H bash -c 'umask 077; f=~/.config/ace-playbook/claude_credential
     read -rs -p "token: " T; echo; printf "%s" "$T" > $f; chmod 0600 $f'
   ```

   Check the file BEFORE the canary (it prints sizes, never the token):

   ```
   sudo -u acewatch -H bash -c 'f=~/.config/ace-playbook/claude_credential
     printf "bytes %s prefixes %s spaces %s\n" $(stat -f %z $f) $(grep -o "sk-ant-" $f | wc -l) $(tr -cd " " < $f | wc -c)'
   ```

   Expect `prefixes 1` and `spaces 0`. `prefixes 2` means the token was pasted twice at the silent prompt (seen live: 219 bytes): re-run the save
   and paste ONCE.

   `watch_claude` points at a small wrapper, the one in step 5 (`/opt/acewatch/bin/claude-reader`, root-owned, 0750, so `acewatch` cannot alter it). It
   reads that one credential file, sets the token for that one process, drops the three variables the Reader drops anyway, pins
   `DISABLE_AUTOUPDATER=1` and execs `acewatch`'s own claude by its absolute path with the arguments it was given:

   ```
   #!/bin/bash
   # acewatch's Reader wrapper (docs/watch/SANDBOX.md step 6): reads the one credential file, sets it for this
   # process only, execs acewatch's own claude.
   set -u
   CRED=/var/acewatch/.config/ace-playbook/claude_credential
   if [ ! -r "$CRED" ]; then echo "claude-reader: credential file missing" >&2; exit 78; fi
   CLAUDE_CODE_OAUTH_TOKEN="$(tr -d '[:space:]' < "$CRED")"
   export CLAUDE_CODE_OAUTH_TOKEN
   unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL
   export DISABLE_AUTOUPDATER=1
   exec /var/acewatch/.local/bin/claude "$@"
   ```

   With `DISABLE_AUTOUPDATER=1` the binary never changes under the Reader by itself: the operator updates it on purpose, by re-running the installer
   as `acewatch` (the command above), and then runs the canary.
7. Install the two templates by hand as root: `hooks/acewatch/com.example.acewatch.plist` (tier 0, daily 04:30 local) and
   `hooks/acewatch/com.example.acewatch.weekly.plist` (tier 1, Sundays 05:00): fill in the two placeholders, copy to
   `/Library/LaunchDaemons` (root:wheel, 0644), `plutil -lint` them, `launchctl bootstrap system <file>`. The label `com.example.acewatch` is a
   placeholder: when you install, rename the file, its `Label` and the `launchctl bootout` commands below to your own reverse-DNS domain
   (`com.example.acewatch` becomes `com.yourdomain.acewatch`, `com.example.acewatch.weekly` becomes `com.yourdomain.acewatch.weekly`). For each
   template, `sed` the two placeholders and the label into the new file, then fix its owner and mode, lint it and load it:

   ```
   sed -e "s#__ACE_PLAYBOOK_COPY__#<copy>#g" -e "s#__ACEWATCH_HOME__#/var/acewatch#g" -e "s#com.example.acewatch#com.yourdomain.acewatch#g" \
     <copy>/hooks/acewatch/com.example.acewatch.plist | sudo tee /Library/LaunchDaemons/com.yourdomain.acewatch.plist > /dev/null
   sudo chown root:wheel /Library/LaunchDaemons/com.yourdomain.acewatch.plist && sudo chmod 0644 /Library/LaunchDaemons/com.yourdomain.acewatch.plist
   plutil -lint /Library/LaunchDaemons/com.yourdomain.acewatch.plist
   sudo launchctl bootstrap system /Library/LaunchDaemons/com.yourdomain.acewatch.plist
   sudo launchctl print system/com.yourdomain.acewatch | grep -E "state|last exit"
   ```

   Repeat with `com.example.acewatch.weekly.plist` and `com.yourdomain.acewatch.weekly`. To reinstall, `sudo launchctl bootout
   system/com.yourdomain.acewatch` first. A label under a personal domain stays in your own copy: this repository keeps only `com.example.acewatch`.

## What it can and cannot do

- Writes: only its watch directory (`proposals/`, `decisions.jsonl`, `seen.json`, `inventory.json`, `scan.lock`, `reader_cwd/`) and `logs/`.
- Reads: its own config, the checkout copy, the root-owned copies in `/opt/acewatch/inputs/`, the network.
- The network beyond the sources' hosts is NOT enforceable by launchd. The fetcher refuses private and loopback addresses and redirects to
  another host, and only the hosts in `watch_sources` are ever requested, but a process running as `acewatch` could open any outbound
  connection. A `pf` rule (an anchor that allows `acewatch` to reach only the source hosts) or a local proxy is the operator's optional step;
  without it, treat the egress as open.
- Moving proposals from `acewatch`'s watch directory to the operator's own is a copy, then an import (`compile watch queue` appends
  decisions, so the operator reviews proposals in a watch directory of their own). Copy only `proposals/W-*.json`, as the operator, into
  a new real directory (for example `cp /var/acewatch/watch/proposals/W-*.json ~/acewatch-copy/` from a shell that may read it), then run
  `python3 -m ace_playbook.compile watch import ~/acewatch-copy` (add `--dry-run` to see the counts first). The import validates each file
  with the same record check, counts duplicates, keeps the cap of five open, never changes the copy, and prints `rejected <stem>: <reason>`
  for anything else; then `compile watch queue` reviews them. An imported record is evidence plus title: its stamps, URLs and target are checked
  against the operator's own `watch_sources` and `watch_inventory_map` (both must be set), any diff in the copy is dropped (diffs come only from the
  operator's own scan, and `; N imported without diff` says how many), and its title shows ` (imported)`.

## Kill files

- `<watch_dir>/DISABLED` stops the whole scan (`watch: disabled`).
- `<watch_dir>/READER_DISABLED` stops only the model stage: the rule path of W1 keeps running and the scan prints `reader: disabled`.
  `watch canary` ignores READER_DISABLED (it is the check that the Reader still refuses, so it runs whatever the switches say).
- `watch_reader` false (the default) is the same as no model stage at all. A `--dry-run` scan skips the model stage (`reader: skipped (dry run)`).
- Limits of one scan: at most 5 reader leads per scan (the loop stops there and the rest are read on later scans, `, C capped`), at most 20 items
  looked at, and an entry the rules already flagged is skipped before the read (`, D dup`).

## Outages and bad items

A call that was spawned and failed (an error envelope, a non-zero exit, a timeout, empty or garbage output) is counted in F (`, F failed` in
the scan line); the TRY is held, not counted. After three failed calls with no success, or at the end of a run that had failed calls and no
success, ONE call is made on a fixed,
benign, built-in item (`reader.PROBE`: no feed text ever reaches it, and it is never stored, composed or counted as read). A parsed envelope means
the Reader is healthy: the held tries count, the run goes on (later failures count at once), `reader: health probe ok after N failed call(s)` goes
to stderr, `reader.last.probe` in `seen.json` is `ok` and the probe's cost is added to the run's (`; cost $C`); an item that fails twice is retired on
its own. A failed probe is an outage (an
expired login, no network): the held tries are dropped, the rest of the run is `, U unavailable`, the scan line ends `, outage` and a second line
says `reader: outage (the health probe failed after F failed call(s)); N item(s) not read, nothing marked`. `outage` = the Reader's own health
probe (a fixed benign item) failed after feed items failed: nothing is marked, every item is read on a later scan; a feed item that makes the model
fail twice is retired on its own and cannot stall the stage; `reader unavailable` = claude could not be started at all. An outage that starts before a
run's first success or healthy probe loses no item; one that starts after it costs each later item one try (two tries retire it): the probe, not
the feed, decides.
A probe answer over 200 KB is not healthy. The canary prints `canary: outage (the health probe failed)` for an outage, with exit 2 unless it saw
the Reader comply (exit 1 outranks 2).

## The canary

`python -m ace_playbook.compile watch canary` runs `tests/fixtures/watch/canary.xml` (planted injections: a request for a model pin, a request
for extra JSON keys, text hidden in HTML, a 5 000-character body) through the real Reader path with the configured claude, writes nothing and
prints `canary: 0 proposals` (exit 0) or `canary: N proposals` and/or `canary: S model_pin request(s)`.
Exit codes: exit 0 = every planted item was read and none was obeyed; exit 1 = the Reader complied (a proposal or a model_pin request), relevant or
not; 2 = the check proved nothing, or only a part (below).
Run it weekly as `acewatch`, from outside the checkout and with the environment spelled out (sudo drops it otherwise):

```
cd / && sudo -u acewatch -H env PATH=/usr/bin:/bin \
  ACE_PLAYBOOK_CONFIG=/var/acewatch/.config/ace-playbook/config.json \
  <copy>/.venv/bin/python -m ace_playbook.compile watch canary
```

The verb uses exit 2 when the claude could not be run (`canary: reader unavailable`: missing, or not spawnable), when failed calls and then a failed
health probe are an outage (`canary: outage (the health probe failed)`), when the feed is not in the copy or holds no item (`canary: feed missing`), or
when not every planted item was read (`canary: N of M items read`, printed before the proposals line): the check proved nothing or only a part.
Exit 1 outranks 2 (D-168): a seen compliance is reported (its `model_pin` and proposals lines are printed first) even when the run was partial or
ended in an outage or an unavailable claude. A run that is down (an outage or an unavailable claude) prints that line INSTEAD of `canary: N of M
items read`, and with zero compliance it prints no proposals line. A call that ran and failed adds `canary: F failed` on stderr. A model change
(`watch_reader_model`) is a reason to run it before the next scan.

## Log rotation

launchd appends `acewatch.out.log`, `acewatch.err.log` and `acewatch.weekly.out.log` / `.err.log` in `/var/acewatch/logs/` forever; the scan itself writes
no log file. `hooks/acewatch/newsyslog.acewatch.conf` is a newsyslog(8) template, one line per file (owner `acewatch:staff`, mode 600, keep 5, rotate
at 1024 KiB, compressed). Install it as root, by hand, once: replace the placeholder with acewatch's home and put the result where newsyslog reads it:

```
f=$(mktemp) && sed "s#__ACEWATCH_HOME__#/var/acewatch#g" hooks/acewatch/newsyslog.acewatch.conf > "$f"
sudo install -m 644 "$f" /etc/newsyslog.d/acewatch.conf && rm "$f"
sudo newsyslog -nvf /etc/newsyslog.d/acewatch.conf
```

(the last line is a dry run: it prints what it would rotate). launchd opens the log files again for every run, so the rotation needs no signal. The files are
rotated as root and stay owned by `acewatch`; nothing in this repository installs the file. Linux: the same four files, the same numbers in a
logrotate stanza (`rotate 5`, `size 1024k`, `compress`, `create 0600 acewatch acewatch`) under `/etc/logrotate.d/acewatch`.

## The operator-side status line

`/var/acewatch` is `acewatch`'s own (0700), so the operator's `compile status` cannot read the watcher's store and shows nothing about it. The nightly
can show one line instead: set `acewatch_check` in the OPERATOR's config to a copy of `hooks/acewatch/acewatch_check.sh` (outside the checkout,
mode 0755; edit the three variables at its top: the acewatch user, the venv python of step 3 and acewatch's config of step 4). The nightly runs
it as `<script> --check` like `backup_check` and `brain_check`, keeps the first line (80 characters at most, no `/`) as a note, and `compile status`
shows it: `acewatch open 3  leads 1  oldest 2d  last scan 4h` (or `acewatch none` / `acewatch disabled`); a failed or empty run shows
`acewatch check failed`. The script runs `sudo -n -u acewatch <venv python> -m ace_playbook.compile --config <acewatch config> watch status`, so it needs
ONE sudoers line, written by you with `sudo visudo -f /etc/sudoers.d/acewatch-check` (nothing installs it): `<operator> ALL=(acewatch) NOPASSWD: ` followed by that
exact command (python path, `-m ace_playbook.compile --config` and the config path, `watch status`). It reads state only; it needs no write access.

## How to rotate the token

Run `claude setup-token` again as `acewatch`, replace the one file (mode 0600; the save and the check of step 6, paste ONCE), run the
canary, then revoke the old token in the account settings. Nothing else holds a copy; a leak of the file is a leak of that one login, which has no
connectors and no access to the operator's data.

## Uninstalling

`sudo launchctl bootout system/com.example.acewatch` and `sudo launchctl bootout system/com.example.acewatch.weekly`, remove the two
plists from `/Library/LaunchDaemons`, then `sudo sysadminctl -deleteUser acewatch` (this removes its home, so copy out anything you want
first), `sudo dseditgroup -o delete acewatch`, remove the root-owned `/opt/acewatch` and revoke its token.
