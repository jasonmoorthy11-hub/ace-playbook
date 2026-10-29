"""The newsyslog(8) template for the sandbox logs (hooks/acewatch/newsyslog.acewatch.conf) names exactly the files the two plists write."""

import plistlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / "hooks" / "acewatch"
CONF = FOLDER / "newsyslog.acewatch.conf"
SANDBOX = ROOT / "docs" / "watch" / "SANDBOX.md"
HOME = "__ACEWATCH_HOME__"


def lines() -> list[str]:
    return [ln for ln in CONF.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def plist_logs() -> set[str]:
    out = set()
    for path in sorted(FOLDER.glob("*.plist")):
        data = plistlib.loads(path.read_bytes())
        out |= {data["StandardOutPath"], data["StandardErrorPath"]}
    return out


def test_the_template_names_exactly_the_four_log_paths_the_two_plists_write():
    paths = [ln.split()[0] for ln in lines()]
    assert len(paths) == 4 and len(set(paths)) == 4 and set(paths) == plist_logs()
    assert all(p.startswith(HOME + "/logs/") for p in paths)


def test_every_line_has_seven_fields_owner_mode_600_keep_5_1024_kib_compress_no_signal():
    for ln in lines():
        path, owner, mode, count, size, when, flags = ln.split()
        assert path.startswith(HOME) and (owner, mode, count, size, when, flags) == ("acewatch:acewatch", "600", "5", "1024", "*", "JN"), ln


def test_the_comments_do_not_hide_a_second_set_of_fields():
    text = CONF.read_text(encoding="utf-8")
    assert text.count(HOME) >= 4 and not re.search(r"/Users/|/var/acewatch", "\n".join(lines()))


def test_the_sandbox_runbook_names_the_installed_file_and_the_linux_equivalent():
    text = " ".join(SANDBOX.read_text(encoding="utf-8").split())
    assert "/etc/newsyslog.d/acewatch.conf" in text and "newsyslog.acewatch.conf" in text and "logrotate" in text
    assert "sudo install -m 644" in text and "sed" in text and HOME in text
