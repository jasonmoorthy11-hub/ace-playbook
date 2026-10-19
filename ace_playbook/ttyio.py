"""One line from the controlling terminal, for the TTY-only commands (`compile approve|label`, `store promote`).

Writing and reading go through separate handles on purpose: a single buffered `r+` text stream on
`/dev/tty` raises "File or stream is not seekable" the moment it switches from write to read.
Stdlib only, so the compile CLI (on the shadow hook's import path) stays light.
"""

from __future__ import annotations

TTY = "/dev/tty"


def ask(prompt: str, device: str | None = None) -> str:
    """Print `prompt` on the terminal and return the line typed there (without its newline); EOFError on EOF."""
    dev = device or TTY
    with open(dev, "w") as out:
        out.write(prompt)
        out.flush()
    with open(dev, "r") as inp:
        line = inp.readline()
    if not line:
        raise EOFError
    return line.rstrip("\n")
