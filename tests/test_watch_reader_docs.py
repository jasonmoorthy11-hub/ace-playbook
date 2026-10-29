"""W2, the docs and templates of the quarantined Reader (docs/watch/SANDBOX.md, hooks/INSTALL.md step 5, docs/design/compiler.md "Reader (W2, as
built)", LIMITATIONS.md, hooks/acewatch/*.plist): the claims a reader of those files acts on are pinned here, so a reworded doc cannot quietly lose one.

No test here runs a claude; the code behind each claim is tested in tests/test_watch_reader.py.
"""

import plistlib
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLISTS = sorted((ROOT / "hooks" / "acewatch").glob("*.plist"))
SANDBOX = ROOT / "docs" / "watch" / "SANDBOX.md"
INSTALL = ROOT / "hooks" / "INSTALL.md"
DESIGN = ROOT / "docs" / "design" / "compiler.md"
LIMITS = ROOT / "LIMITATIONS.md"
OUTAGE_DOC = ("`outage` = the Reader's own health probe (a fixed benign item) failed after feed items failed: nothing is marked, every item is read on a "
              "later scan; a feed item that makes the model fail twice is retired on its own and cannot stall the stage; `reader unavailable` = claude "
              "could not be started at all")                                                 # D-165: one sentence in INSTALL, SANDBOX, compiler.md
LOSES_NOTHING = ("outage that starts before a run's first success or healthy probe loses no item; one that starts after it costs each later item "
                 "one try (two tries retire it)")
EXIT_2 = ("canary: reader unavailable", "canary: outage (the health probe failed)", "canary: N of M items read",
          "exit 0 = every planted item was read and none was obeyed")


def flat(path: Path) -> str:
    """The file with every run of whitespace folded to one space, so a needle may span a line break."""
    return " ".join(path.read_text(encoding="utf-8").split())


def test_the_acewatch_templates_lint_and_carry_the_documented_keys():
    assert [p.name for p in PLISTS] == ["com.example.acewatch.plist", "com.example.acewatch.weekly.plist"]
    if shutil.which("plutil"):
        for p in PLISTS:
            assert subprocess.run(["plutil", "-lint", str(p)], capture_output=True, text=True).returncode == 0
    daily, weekly = (plistlib.loads(p.read_bytes()) for p in PLISTS)
    assert (daily["Label"], weekly["Label"]) == ("com.example.acewatch", "com.example.acewatch.weekly")
    for d, tier in ((daily, "0"), (weekly, "1")):
        args = d["ProgramArguments"]
        assert d["UserName"] == "acewatch" and args[-4:] == ["watch", "scan", "--tier", tier]
        assert "-m" in args and "ace_playbook.compile" in args
        assert set(d["EnvironmentVariables"]) == {"ACE_PLAYBOOK_CONFIG", "HOME", "PATH"} and d["EnvironmentVariables"]["PATH"] == "/usr/bin:/bin"
        assert d["StandardOutPath"].startswith("__ACEWATCH_HOME__/") and d["StandardErrorPath"].startswith("__ACEWATCH_HOME__/")
        assert d["EnvironmentVariables"]["ACE_PLAYBOOK_CONFIG"].startswith("__ACEWATCH_HOME__/") and "RunAtLoad" not in d
    assert daily["StartCalendarInterval"] == {"Hour": 4, "Minute": 30}
    assert weekly["StartCalendarInterval"] == {"Weekday": 0, "Hour": 5, "Minute": 0}
    text = b"".join(p.read_bytes() for p in PLISTS).decode()
    assert ("/" + "Users" + "/") not in text and "@" not in text


def test_the_plist_comments_name_a_venv_built_outside_any_home():
    for p in PLISTS:
        text = p.read_text(encoding="utf-8")
        assert "python outside any user home" in text and "full checkout" in text.lower()


def test_sandbox_md_documents_the_operating_model_of_the_second_user():
    text = flat(SANDBOX)
    for needle in ("sysadminctl -addUser acewatch", "claude setup-token", "0600", "no sudo", "READER_DISABLED", "DISABLED", "pf",
                   "launchctl bootout system/com.example.acewatch", "rotate", "watch canary", "ACE_PLAYBOOK_CONFIG", "0700"):
        assert needle.lower() in text.lower(), needle
    assert ("/" + "Users" + "/") not in text and "launchctl bootout system/com.example.acewatch.weekly" in text and "rename the file, its `Label`" in text


def test_sandbox_md_isolates_the_group_the_venv_and_the_claude_and_checks_the_home_is_closed():
    """linux I-1 / I-2 / N-L4 and canary MINOR-3: a group of its own, failing `ls` and membership checks, a venv and a claude outside the operator's home."""
    text = flat(SANDBOX)
    for needle in ("dseditgroup -o create -r \"ACE Watch\" acewatch", "-GID $(dscl . -read /Groups/acewatch PrimaryGroupID", "Permission denied",
                   "chmod 0700 ~", "dsmemberutil checkmembership -U acewatch -G staff", "not a member", "the `ls` check, not the group, is the guarantee",
                   "UV_PYTHON_INSTALL_DIR=/opt/ace-python", "UV_PYTHON_PREFERENCE=only-managed", "UV_CACHE_DIR=/opt/ace-python/cache",
                   "<absolute path of uv> sync",
                   "its own claude", "sudo -u acewatch -H env PATH=/usr/bin:/bin HOME=/var/acewatch", "the installer the installed version documents",
                   "tests/fixtures/watch/", "not a wheel", "--no-editable", "cd / && sudo -u acewatch -H env PATH=/usr/bin:/bin",
                   "ACE_PLAYBOOK_CONFIG=/var/acewatch/.config/ace-playbook/config.json", "-m ace_playbook.compile watch canary"):
        assert needle in text, needle
    assert "default modes already refuse" not in text and "no way into the operator's home" not in text


def test_sandbox_md_states_the_reader_guarantees_honestly():
    """design m3 / m4, linux N-L1: the four dropped variables by name, and what each flag was checked for (CLAUDE.md is the separate user's job)."""
    text = flat(SANDBOX)
    for needle in ("CLAUDE_CODE_OAUTH_TOKEN", "verify against the installed version", "ONLY as acewatch", "--setting-sources", "disableAllHooks",
                   "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDECODE", "checked by the release probe",
                   "kept out by the separate user", "ignores READER_DISABLED", "uses exit 2", "reader_cwd",
                   "exit 1 = the Reader complied (a proposal or a model_pin request)", "health probe", "at most 5 reader leads per scan",
                   OUTAGE_DOC, LOSES_NOTHING, "reader.PROBE", *EXIT_2):
        assert needle in text, needle
    for banned in ("no hook, `CLAUDE.md` or memory of the account it runs as is loaded", "cannot load"):
        assert banned not in text


def test_install_and_the_design_say_what_is_true_about_the_reader():
    install, design, limits = flat(INSTALL), flat(DESIGN), flat(LIMITS)
    for needle in ("U unavailable", "F failed", "D dup", "reader: claude is unavailable; U item(s) not read", "ONLY", "acewatch", "ignores READER_DISABLED",
                   "reader-settings.json", "reader: skipped (dry run)", "the rest are read on later scans", "health probe",
                   "exit 1 = the Reader complied (a proposal or a model_pin request)", OUTAGE_DOC, LOSES_NOTHING, *EXIT_2[:3],
                   "/opt/acewatch/bin/claude-reader", "(the root-owned token wrapper of SANDBOX steps 5 and 6)"):
        assert needle in install, needle
    for needle in ("reader-settings.json", "--setting-sources", "for the `acewatch` user ONLY", "target is the source id", "failed", "capped",
                   "reader_cwd", "start_new_session", "reader: skipped (dry run)", "the Reader is only asked about entries the rules did not already flag",
                   "checked by the release probe", "ANTHROPIC_AUTH_TOKEN", "model_pin request", "`reader.PROBE`", "(D-165)", "`, outage`",
                   OUTAGE_DOC, LOSES_NOTHING, *EXIT_2, "of EVERY keyword entry, past the W1 cap of ten"):
        assert needle in design, needle
    assert "target `lead`" not in design and "a run of five dashes inside the item cannot spell one" not in design
    assert "the queue and the scan output" in limits and "queue and the digest" not in limits
    assert "checked by the release probe" in limits and "cannot load" not in limits and "never by feed items" in limits
    for text in (install, design, limits, flat(SANDBOX)):
        assert "breaker" not in text and "go to the back of the next scan" not in text


def test_the_outage_claim_is_scoped_in_every_doc_and_in_the_code_d167():
    """An outage starting after the run's first success or healthy probe counts each later failure at once; no file may still say an outage loses no item."""
    loop = ROOT / "ace_playbook" / "compile" / "watch" / "readerloop.py"
    for path in (SANDBOX, INSTALL, DESIGN, LIMITS, loop):
        text = flat(path).lower()
        assert "an outage loses no item" not in text and "no item is lost" not in text, path.name
    for path in (SANDBOX, INSTALL, DESIGN, loop):
        assert "starts before a run's first success or healthy probe loses no item" in flat(path).lower(), path.name
    sandbox = flat(SANDBOX)
    assert "the TRY is held" in sandbox and "garbage output) is held, not counted" not in sandbox and "A failed call is held, not counted" not in flat(INSTALL)


def test_the_docs_say_what_the_probe_prints_and_stores_and_that_exit_1_outranks_2_d168():
    for path in (SANDBOX, INSTALL, DESIGN):
        text = flat(path)
        assert "reader: health probe ok after N failed call(s)" in text and "reader.last.probe" in text, path.name
        assert "exit 1 outranks 2" in text.lower() and "holds no item" in text, path.name
    canary_doc = flat(ROOT / "ace_playbook" / "compile" / "watch" / "canary.py")
    assert "Exit 1 outranks 2 (D-168)" in canary_doc and "holds no item" in canary_doc
    assert "round-robin" in flat(SANDBOX) and "round-robin" in flat(DESIGN)
    assert "The queued items are dealt round-robin across the sources" in flat(SANDBOX) and "Reader leads are dealt round-robin" not in flat(SANDBOX)
    for text in (flat(SANDBOX), flat(DESIGN), flat(INSTALL), canary_doc):                 # a down run prints its line INSTEAD of "N of M items read"
        assert "instead of `canary: n of m items read`" in text.lower() or "instead of `n of m items read`" in text.lower(), text[:40]
        assert "no proposals line" in text


def step(n: int) -> str:
    """Step n of SANDBOX "Creating the user", whitespace-folded (a step starts at a line beginning `N. `)."""
    body = SANDBOX.read_text(encoding="utf-8").split("## Creating the user", 1)[1].split("\n## ", 1)[0]
    return " ".join(next(p for p in re.split(r"(?m)^(?=\d\. )", body) if p.startswith(f"{n}. ")).split())


def test_sandbox_steps_1_to_3_are_the_commands_that_ran_on_macos_26():
    """macOS 26 rejects `interactive`; a default 755 home makes the chmod the expected path; the newest managed python is untested in CI."""
    raw = SANDBOX.read_text(encoding="utf-8")
    block = raw[raw.index("sudo sysadminctl -addUser acewatch"):]
    block = block[:block.index("```")]
    assert "-password -" in block and "interactive" not in block
    assert sum("sudo -u acewatch ls" in ln for ln in block.splitlines()) == 4
    assert "sudo chmod 0700 ~<operator>" in block and block.index("chmod 0700") < block.index("sudo -u acewatch ls")
    one = step(1)
    assert "ALL FOUR MUST print `Permission denied`" in one and "EXPECTED step, not an exception" in one
    assert "(755, or 750 for group `staff` on some releases; the chmod is needed either way)" in one
    assert "follow the shape of the commands run on macOS 26" in flat(SANDBOX) and "were run as written" not in flat(SANDBOX)    # M-1 provenance
    assert "~<operator>/.local/share/ace-playbook" in one and "\"is a member\" (nested `staff`) is normal" in one
    assert "Invalid option: (interactive)" in one and "if it lists files" not in flat(SANDBOX)
    assert "--python 3.12" in step(3) and "<absolute path of uv> sync --python 3.12" in step(3) and "3.15" in step(3) and "untested in CI" in step(3)
    for n in (2, 5):                                     # a cross-parent rename needs write on the directory itself: park legacy copies in place
        assert "needs write permission on the directory itself" in step(n) and "IN PLACE" in step(n), n
    assert "inputs.legacy-<date>" in step(2) and "inputs.legacy-<date>" in step(5) and "never move it out to an operator-owned folder" in step(5)


def test_sandbox_step_5_puts_the_inputs_and_the_wrapper_in_a_root_owned_tree():
    five = step(5)
    for needle in ("ROOT-OWNED tree", "never under the acewatch-owned `watch/`", "/opt/acewatch/inputs", "/opt/acewatch/bin/claude-reader",
                   "mktemp -d $R/.inputs.XXXXXX", "swapped in by rename", "refusing: $R is a symlink", "installed only after a symlink and ownership check",
                   "[ -L \"$p\" ]", "stat -f %Su", "written AS `acewatch` from stdin", "root never writes into `acewatch`'s tree",
                   "`watch_claude` at `/opt/acewatch/bin/claude-reader`", "0440"):
        assert needle in five, needle
    assert "<watch_dir>/inputs/" not in flat(SANDBOX) and "owned by the operator, read-only" not in flat(SANDBOX)
    assert "sudo test -e \"$R/inputs\"" in five and "sudo test ! -e \"$R/.inputs.old\"" in five and "[ -e $R" not in five   # I-1: root-run tests
    assert "the operator cannot search `/opt/acewatch`" in five
    assert "[ -L \"$R\" ] && { echo \"refusing: $R is a symlink\"; false; }" in five and "stop here if it prints" in five     # M-6: stop on print
    assert "the rest of this block must NOT be pasted" in five and "run the block as a script with `set -e`" in five      # N-2: false needs set -e
    assert "ONE exception to \"root never deletes inside the tree\"" in five and "only root can remove it (`sudo rm -r <the parked directory>`" in five
    assert "before the `sudo rm -r`" in five and "`/var/acewatch/watch`" in five and "the parked folder itself" in five            # N-4: chain check first
    assert "ONE exception" in step(2) and "can only be removed by root" in step(2)                                              # I-2
    assert "the one directory the scan writes" in step(4) and "ONE writable directory" not in step(4)                          # M-7
    assert "holds the Reader" not in step(4) and "the scan writes nothing there" in step(4)                                     # N-1: state in watch_dir
    assert "As installed, with `watch_reader` still false (flip it after the first canary):" in step(4)                         # N-5
    assert "\"watch_reader\": false" in step(4) and "\"watch_reader\": true" not in step(4) and "flip it to `true` after the first canary" in step(4)  # M-3
    assert "/opt/acewatch/inputs/" in flat(SANDBOX).split("## What it can and cannot do", 1)[1]


def test_sandbox_step_6_documents_the_installer_the_wrapper_the_credential_and_the_by_hand_install():
    six = step(6)
    for needle in ("prints NOTHING until it reaches \"Setting up Claude Code...\"", "275 MB for 2.1.309", "sudo ls -la /var/acewatch/.claude/downloads/",
                   "/var/acewatch/.local/bin/claude", ".local/share/claude/versions/<ver>", "Download timed out: exceeded the total deadline",
                   "https://downloads.claude.ai/claude-code-releases/<ver>/manifest.json", ".platforms[\"darwin-arm64\"].checksum", "`.size`",
                   "shasum -a 256", "stat -f %z", "ln -sfn", "root never writes into the sandbox tree", "-H /var/acewatch/.local/bin/claude --version",
                   "' < <file>", "umask 077; f=~/.config/ace-playbook/claude_credential", "read -rs -p", "chmod 0600 $f",
                   "export CLAUDE_CODE_OAUTH_TOKEN", "unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL", "export DISABLE_AUTOUPDATER=1",
                   "exec /var/acewatch/.local/bin/claude \"$@\"", "re-running the installer as `acewatch`"):
        assert needle in six, needle
    assert "downloads.claude.ai/claude-code-releases" in flat(SANDBOX) and "DISABLE_AUTOUPDATER=1" in flat(SANDBOX)
    assert "(`darwin-x64` on Intel)" in six                                                                                     # M-4
    assert six.count("shasum -a 256") == 2 and "re-verifies the placed copy" in six                                             # M-5
    assert six.rindex("shasum -a 256") < six.index("ln -sfn") < six.rindex("-H /var/acewatch/.local/bin/claude --version")     # N-3: verify, link, run
    assert "prefixes 1" in six and "spaces 0" in six and "`prefixes 2` means the token was pasted twice" in six and "(seen live: 219 bytes)" in six
    assert "paste once" in six.lower() and "BEFORE the canary" in six and "bytes %s prefixes %s spaces %s" in six


def test_sandbox_step_7_renames_the_label_and_installs_the_daemons_the_way_the_script_did():
    seven = step(7)
    for needle in ("`com.example.acewatch` becomes `com.yourdomain.acewatch`", "sed -e \"s#__ACE_PLAYBOOK_COPY__#<copy>#g\"",
                   "s#__ACEWATCH_HOME__#/var/acewatch#g", "s#com.example.acewatch#com.yourdomain.acewatch#g",
                   "/Library/LaunchDaemons/com.yourdomain.acewatch.plist", "chown root:wheel", "chmod 0644", "plutil -lint",
                   "launchctl bootstrap system /Library/LaunchDaemons/com.yourdomain.acewatch.plist",
                   "launchctl print system/com.yourdomain.acewatch | grep -E \"state|last exit\"", "com.yourdomain.acewatch.weekly"):
        assert needle in seven, needle


def test_the_bare_path_sentence_is_in_the_reader_section_and_limitations_name_the_percent_decode_residual():
    install = INSTALL.read_text(encoding="utf-8")
    scrub = install.split("## 5. Optional: git pre-commit scrub", 1)[1].split("## 6. Uninstall", 1)[0]
    reader = install.split("5. The Reader (W2", 1)[1].split("The count also reaches", 1)[0]
    assert "bare PATH" not in scrub and "three Reader test files under a bare PATH" in " ".join(reader.split())
    limits = flat(LIMITS)
    assert "A tool that percent-decodes a non-URL path argument itself (`a%20b.md` written as `a b.md`) is not tracked" in limits
    assert "records every tool string in its percent-decoded form as well" in limits
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.count("astral-sh/setup-uv@") == 2 and ci.count("enable-cache: false") == 2
