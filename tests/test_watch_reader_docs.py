"""W2, the docs and templates of the quarantined Reader (docs/watch/SANDBOX.md, hooks/INSTALL.md step 5, docs/design/compiler.md "Reader (W2, as
built)", LIMITATIONS.md, hooks/acewatch/*.plist): the claims a reader of those files acts on are pinned here, so a reworded doc cannot quietly lose one.

No test here runs a claude; the code behind each claim is tested in tests/test_watch_reader.py.
"""

import plistlib
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
LOSES_NOTHING = "outage loses no item: the probe, not the feed, decides"
EXIT_2 = ("canary: reader unavailable", "canary: outage (the health probe failed)", "canary: N of M items read",
          "exit 0 = every planted item was read and none was obeyed")


def flat(path: Path) -> str:
    """The file with every run of whitespace folded to one space, so a needle may span a line break."""
    return " ".join(path.read_text().split())


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
        assert "python outside any user home" in p.read_text() and "full checkout" in p.read_text().lower()


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
                   "/var/acewatch/bin/claude-reader", "(the 0700 token wrapper of SANDBOX step 6)"):
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
