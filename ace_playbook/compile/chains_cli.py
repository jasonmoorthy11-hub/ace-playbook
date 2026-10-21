"""`compile keygen` and `keygen --rehead` (docs/design/compiler.md 7.3, "Keyed chains (as built)"). keygen runs on a NEVER-keyed install only
(no key file, no heads file, no `ACE_PLAYBOOK_KEYED=1`): it lists the rows of both chains, asks y/N, makes the key, keeps `.bak` copies,
re-seals the chains and writes the signed heads; on any keyed install it refuses (no resume, no overwrite) and the recovery is manual
(hooks/INSTALL.md section 7, Repair). `--rehead` rewrites the heads only after the operator has seen, per chain, what differs from the OLD
heads and answered y; there is no `--yes`. Not on the hook path. Imports: stdlib, `..canon`, `.chains`, `.chains_io`.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import sys
from pathlib import Path

from .. import atomic, config as cfgmod
from ..canon import GENESIS, chain_key
from . import chains, chains_io
from .chains import Keyring


def show(first: int, part: list[dict]) -> list[str]:
    """One line per row: number (from `first`), ts, proposal id, mode (approvals) or to_mode (demotions)."""
    return [f"  row {i}  {r['ts']}  {r['proposal_id']}  {r.get('mode') or r['to_mode']}" for i, r in enumerate(part, first)]


def _diff(name: str, rows: list[dict], old: str | None) -> list[str]:
    """What the operator must inspect before a head moves: the rows added after the old head, or what the chain ends with now."""
    hashes, tail = [GENESIS] + [r["hash"] for r in rows], (max(1, len(rows) - 2), rows[-3:])
    if old is None:
        return [f"{name}: the old heads file is missing or invalid; the chain ends with:", *show(*tail)]
    if old not in hashes:
        return [f"{name}: old head not found: rows were removed or replaced after row {len(rows)} (they cannot be listed: compare with a "
                f"backup before answering y); the chain ends with:", *show(*tail)]
    k = hashes.index(old)
    return [f"{name}: " + ("rows added after the old head:" if k < len(rows) else "the old head is the last row"), *show(k + 1, rows[k:])]


def reheads(cfg, path: Path, ask) -> int:
    if (key := chain_key(path)) is None:
        print("error: no usable chain key (run keygen first)", file=sys.stderr)
        return 1
    ring, heads, lines = Keyring(key, path.parent / chains.HEADS), {}, []
    old = ring.heads()
    with contextlib.ExitStack() as stack:                       # both chain locks, approvals first like keygen, around the read and the update
        for mod in chains_io.MODS.values():
            chains_io._lock(stack, mod, cfg.data_dir)
        for name, mod in chains_io.MODS.items():
            rows, broken = mod.read_chain(cfg.data_dir, Keyring(key))      # without the heads file: it is what is being rewritten
            if broken is not None:
                print(f"error: {mod.FILE} chain broken {chains.where(broken, mod.BASE)}; truncate it to its last complete line first",
                      file=sys.stderr)
                return 1
            heads[name] = rows[-1]["hash"] if rows else GENESIS
            lines += _diff(name, rows, old and old[name])
        print("\n".join(lines))
        if ask is None or ask("Write these heads? Inspect the listed rows first. [y/N] ").lower() not in ("y", "yes"):
            print("error: heads not written", file=sys.stderr)
            return 1
        ring.update(**heads)
    print("\n".join(f"{name}: {head}" for name, head in heads.items()))
    return 0


def _refusal(cfg, path: Path) -> str | None:
    """Why keygen must not run: the install is keyed (key file, heads file or the flag); None for a never-keyed install."""
    heads = path.parent / chains.HEADS
    if not (os.path.lexists(path) or os.path.lexists(heads) or os.environ.get(chains.ENV) == "1"):
        return None
    if (key := chain_key(path)) is None:
        return chains_io.RESTORE
    if Keyring(key, heads).heads() is None:
        broken, tail = [], "nothing was written; see hooks/INSTALL.md section 7, Repair"
        for mod in chains_io.MODS.values():
            try:
                if mod.read_chain(cfg.data_dir, Keyring(key))[1] is not None:
                    broken.append(mod)
            except (OSError, ValueError):                       # a symlink, a non-file, or over 1 MiB
                return f"error: {mod.FILE} chain broken (unreadable, not a regular file or over 1 MiB) and chain_heads.json is missing; {tail}"
        if not broken:                                          # both chains verify under the key: only the heads are gone
            return ("error: the chain key exists but chain_heads.json is missing or invalid, and both chains verify under the key: run: "
                    "uv run python -m ace_playbook.compile keygen --rehead (it lists what changed and asks); nothing was written")
        for mod in broken:                                      # still v1 and byte-equal to the keygen backup: it was killed between the replaces
            with contextlib.suppress(OSError):
                bak = chains.read_regular(path.parent / f"{mod.FILE}.bak", mod.MAX_BYTES)
                if bak is not None and bak == chains.read_regular(Path(cfg.data_dir) / mod.FILE, mod.MAX_BYTES):
                    return (f"error: the chain key exists but chain_heads.json is missing and {mod.FILE} is still the plain v1 file (keygen was "
                            f"killed between the replaces): {tail}, interrupted keygen")
        return (f"error: the chain key exists but chain_heads.json is missing or invalid and {broken[0].FILE} does not verify under the key and is "
                f"not the .bak copy keygen left (it was altered after keygen): {tail} (truncate it to its last complete line or restore the key "
                f"file from your backup, then run keygen --rehead)")
    return "error: the chain key file already exists; nothing was written"


def keygen(cfg, need_tty, rehead: bool = False, ask=None) -> int:
    """`compile keygen`: never-keyed installs only; refuses (exit 1) on every keyed one. Lists the rows of both chains and asks y/N (`ask`,
    the TTY prompt; there is no `--yes`), then makes 32 `secrets.token_bytes` as 64 hex characters in `cfg.chain_key_path` (0600, O_EXCL,
    synced; a missing parent is made 0700), keeps `approvals.jsonl.bak` and `demotions.jsonl.bak` beside it, re-seals both chains, writes
    the heads; prints only the path. Exit 2 without a terminal or a path. `rehead` (with `ask`): see `reheads` below. Without `chain_key_path`
    the key goes beside the config file (`cfg.source`; the prompt says where) and, on y, the line naming it is written to that file first."""
    if not need_tty("keygen"):
        return 2
    src = None if cfg.chain_key_path or rehead else getattr(cfg, "source", None)
    if not (cfg.chain_key_path or src):
        print("error: chain_key_path is not set in the configuration", file=sys.stderr)
        return 2
    path = Path(cfg.chain_key_path) if cfg.chain_key_path else src.parent / cfgmod.SUGGESTED_KEY
    new_conf = cfgmod.with_chain_key(src, path) if src else None                # ValueError: not an object, or a null (off) to leave alone
    if rehead:
        return reheads(cfg, path, ask)
    if src and os.path.lexists(path):
        print(f"error: {cfgmod.tilde(path)} exists but {src.name} does not name it: add chain_key_path there or move it away", file=sys.stderr)
        return 1
    if why := _refusal(cfg, path):
        print(why, file=sys.stderr)
        return 1
    if any(os.path.lexists(path.parent / f"{mod.FILE}.bak") for mod in chains_io.MODS.values()):
        print("error: a .bak backup of a chain is already in the key directory; move it away first", file=sys.stderr)
        return 1
    with contextlib.ExitStack() as stack:                       # both chain locks (approvals first) from the one read to the last replace
        raws = {}
        for name, mod in chains_io.MODS.items():
            raws[name] = fh.read() if (fh := chains_io._lock(stack, mod, cfg.data_dir)) else None
            rows, broken = mod.verify(raws[name] or b"", None)
            print(f"{name}: {len(rows)} row(s) to re-seal", *show(1, rows), sep="\n")
            if broken is not None:
                print(f"{name}: row {broken + mod.BASE} onward does not verify and will be kept as is (see hooks/INSTALL.md section 7, Repair)")
        where = f" at {cfgmod.tilde(path)} and name it in {src.name}" if src else ""
        if ask is None or ask(f"Re-seal these rows under a new chain key{where}? [y/N] ").lower() not in ("y", "yes"):
            print("error: nothing was written", file=sys.stderr)
            return 1
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if new_conf:                                            # before the key: a failure leaves nothing but an unkeyed install
            atomic.write_private(Path(os.path.realpath(src)), new_conf)
        key = secrets.token_bytes(32)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            print("error: the chain key file already exists; nothing was written", file=sys.stderr)
            return 1
        with os.fdopen(fd, "w") as fh:
            fh.write(key.hex())
            fh.flush()
            os.fsync(fh.fileno())
        ring = Keyring(key, path.parent / chains.HEADS)
        ring.update(**chains_io._reseal(cfg, ring, path, raws))                     # crash after the replaces: INSTALL section 7, Repair (restore the .bak files)
    print("kept: approvals.jsonl.bak and demotions.jsonl.bak (the chains as they were before keygen) in the key directory", file=sys.stderr)
    print(path)
    return 0
