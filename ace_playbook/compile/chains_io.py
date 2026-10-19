"""`compile keygen` (docs/design/compiler.md 7.3, "Keyed chains (as built)"): the re-seal of both chains under a new key with byte-for-byte `.bak`
copies (chains_cli.py calls it), the file helpers and `need_key`, which guards the writers. Offline only; not on the hook path.
Imports: stdlib, `..canon`, `.approvals`, `.demotions`, `.chains`.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from ..canon import GENESIS, chain_key
from . import approvals, chains, demotions
from .chains import Keyring

MODS = {"approvals": approvals, "demotions": demotions}
RESTORE = ("error: chain key missing: this install is keyed but the key is unusable: restore the chain key file (chain_key_path, default "
           "~/.config/ace-playbook/chain_key) from your backup, or move the unusable key file away, delete approvals.jsonl, demotions.jsonl and "
           "chain_heads.json, unset ACE_PLAYBOOK_KEYED in this shell, and run keygen again (every approval must then be re-made)")


def file_has_v2(data_dir, mod) -> bool:
    """`chains.has_v2` over the chain file of `mod`; False when it is missing or unreadable."""
    with contextlib.suppress(OSError):
        return chains.has_v2(chains.read_regular(Path(data_dir) / mod.FILE, 1 << 20) or b"", mod.shape)
    return False


def need_key(cfg, mod) -> Keyring | None:
    """The keyring for a writer of the chain of `mod`. A keyed install without a usable key (RESTORE line), or an unkeyed one whose file
    holds a shape-valid `v: 2` row (the keygen line, naming Repair): print it, raise KeyMissing. Else None (v1) or the ring."""
    ring = chains.key_of(cfg)
    if (ring is not None and ring.key is None) or (ring is None and file_has_v2(cfg.data_dir, mod)):
        print(RESTORE if ring is not None else chains.OPERATOR, file=sys.stderr)
        raise chains.KeyMissing(chains.MISSING)
    return ring


def _write(path: Path, data: bytes) -> str:
    """`data` in a fresh 0600 temp file beside `path` (mkstemp: O_EXCL, never a predictable name), fsynced; returns its name."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return tmp


def _lock(stack: contextlib.ExitStack, mod, data_dir):
    """The chain file of `mod` opened O_NOFOLLOW and flock'ed until `stack` closes; None when there is no file."""
    try:
        fd = os.open(Path(data_dir) / mod.FILE, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    fh = stack.enter_context(os.fdopen(fd, "r+b"))
    if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_size > mod.MAX_BYTES:
        raise OSError(f"{mod.FILE} is not a regular file of at most 1 MiB")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fh


def _stage(stack: contextlib.ExitStack, mod, raw: bytes | None, data_dir, ring: Keyring, bak: Path):
    """From the bytes `raw` of one chain file (read once under its lock by the caller, never re-read): write the byte-for-byte backup to
    `bak` (0600, O_EXCL) and stage the re-sealed content in a temp file: (path, temp, original bytes, last hash); None for no file. Rows
    that verify with the plain hash become `v: 2` (same content and order, `prev_hash` relinked); the rest is kept byte for byte."""
    if raw is None:
        return None
    path = Path(data_dir) / mod.FILE
    with os.fdopen(os.open(bak, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as out:
        out.write(raw)
        out.flush()
        os.fsync(out.fileno())
    rows = mod.verify(raw, None)[0]
    prev, sealed = GENESIS, []
    for row in rows:
        row = chains.seal({**row, "prev_hash": prev}, ring)
        prev = row["hash"]
        sealed.append(json.dumps(row, sort_keys=True).encode() + b"\n")
    tmp = _write(path, b"".join(sealed) + b"\n".join(raw.split(b"\n")[len(rows):]))
    stack.callback(Path(tmp).unlink, missing_ok=True)
    return path, tmp, raw, prev


def _reseal(cfg, ring: Keyring, key_path: Path, raws: dict) -> dict:
    """Re-seal both chains from `raws` ({name: bytes or None}, read once under the caller's locks); {name: head}. Backups and all temp
    files exist before the first `os.replace`. If anything fails the replaced files are put back from their original bytes and the
    fresh key and the backups are removed again."""
    done, baks = [], [key_path.parent / f"{mod.FILE}.bak" for mod in MODS.values()]
    with contextlib.ExitStack() as stack:
        try:
            staged = {name: _stage(stack, mod, raws[name], cfg.data_dir, ring, bak) for (name, mod), bak in zip(MODS.items(), baks)}
            for item in staged.values():
                if item:
                    os.replace(item[1], item[0])
                    done.append(item)
        except BaseException:
            for item in done:
                os.replace(_write(item[0], item[2]), item[0])
            for gone in [key_path, *baks]:
                gone.unlink(missing_ok=True)
            raise
    return {name: item[3] if item else GENESIS for name, item in staged.items()}
