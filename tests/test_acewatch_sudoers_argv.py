"""The one sudoers line of the operator-side status line (hooks/acewatch/acewatch_check.sh, docs/watch/SANDBOX.md "The operator-side status line") grants an
exact argv, so the argv itself must be safe: `python -I -m ...` keeps the caller's working directory off the import path. Text and temp directories only."""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "hooks" / "acewatch" / "acewatch_check.sh"
SANDBOX = ROOT / "docs" / "watch" / "SANDBOX.md"
ARGV = ["-I", "-m", "ace_playbook.compile", "--config", "CFG", "watch", "status"]


def norm(words: list[str]) -> list[str]:
    """Placeholders of the three spellings (script variable, sudoers comment, runbook) folded to PY and CFG."""
    table = {'"$ACEWATCH_PY"': "PY", "<ACEWATCH_PY>": "PY", "<venv": "PY", "python>": "", '"$ACEWATCH_CONFIG"': "CFG", "<ACEWATCH_CONFIG>": "CFG", "<acewatch": "CFG",
             "config>": ""}
    return [w for w in (table.get(w, w) for w in words) if w]


def script_call() -> list[str]:
    line = next(ln for ln in SCRIPT.read_text(encoding="utf-8").splitlines() if ln.startswith("out=$(sudo"))
    return norm(line.split("-u", 1)[1].split("2>/dev/null", 1)[0].split()[1:])           # after `"$ACEWATCH_USER"`


def header_line() -> list[str]:
    line = next(ln for ln in SCRIPT.read_text(encoding="utf-8").splitlines() if "NOPASSWD:" in ln)
    return norm(line.split("NOPASSWD:", 1)[1].split())


def runbook_command() -> list[str]:
    text = " ".join(SANDBOX.read_text(encoding="utf-8").split())
    command = re.search(r"The script runs `sudo -n -u acewatch (.*?)`, so it needs ONE sudoers line", text)
    assert command, "the runbook names the command the script runs"
    return norm(command.group(1).split())


def runbook_grant_words() -> str:
    text = " ".join(SANDBOX.read_text(encoding="utf-8").split())
    return re.search(r"exact command \((.*?)\)\.", text).group(1)


def test_the_script_the_header_sudoers_line_and_the_runbook_agree_token_for_token_with_dash_I_right_after_the_interpreter():
    assert script_call() == ["PY"] + ARGV
    assert header_line() == ["PY"] + ARGV
    assert runbook_command() == ["PY"] + ARGV
    assert "`-I -m ace_playbook.compile --config`" in runbook_grant_words() and runbook_grant_words().startswith("python path, ")


def test_the_runbook_says_why_and_tells_an_old_install_to_replace_its_line_with_visudo():
    text = " ".join(SANDBOX.read_text(encoding="utf-8").split())
    why = text.split("Why `-I`:", 1)[1].split("## How to rotate the token", 1)[0]
    assert "pins the arguments of a rule, not the working directory" in why and "isolated mode" in why
    assert "replace it now with `sudo visudo -f /etc/sudoers.d/acewatch-check`" in why
    assert "lets any process of the operator run code as `acewatch`" in why
    assert '-I -c "import ace_playbook"' in text and "editable install" in text


def test_a_planted_package_in_the_working_directory_loads_without_dash_I_and_not_with_it(tmp_path):
    plant = tmp_path / "ace_playbook"
    plant.mkdir()
    marker = tmp_path / "marker"
    (plant / "__init__.py").write_text(f"open({str(marker)!r}, 'w').write('planted')\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    # the sudoers argv without -I: python -m puts the caller's cwd first, so the plant runs (its __init__ runs before the missing submodule fails)
    subprocess.run([sys.executable, "-m", "ace_playbook.compile", "--config", "x", "watch", "status"], cwd=tmp_path, env=env, capture_output=True)
    assert marker.read_text() == "planted"
    marker.unlink()
    # the same argv with -I: the plant is ignored and the real package is the one that resolves
    done = subprocess.run([sys.executable, "-I", "-c", "import ace_playbook, sys; print(ace_playbook.__file__)"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert done.returncode == 0 and Path(done.stdout.strip()) == ROOT / "ace_playbook" / "__init__.py" and not marker.exists()
    subprocess.run([sys.executable, "-I", "-m", "ace_playbook.compile", "--config", "x", "watch", "status"], cwd=tmp_path, env=env, capture_output=True)
    assert not marker.exists()
