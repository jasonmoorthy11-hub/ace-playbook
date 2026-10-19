"""Shared setup for the migrate tests: invented fixtures copied into a tmp tree."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ace_playbook import config as cfgmod
from ace_playbook.migrate.__main__ import main
from ace_playbook.store import DeltaLog

FIXTURES = Path(__file__).parent / "fixtures"
DENY = ["AcmeSecretClient", "acme-secret"]

# lessons-b.json: a copy of one lesson (same id, same text) plus a conflicting second text for
# another id, to prove the first occurrence wins.
LEGACY_B = {"lessons": [
    {"id": "auto-a0000013", "text": "Run the smoke test after every deploy of acme-widgets",
     "created": "2026-07-06"},
    {"id": "auto-a0000002", "text": "A different text that must lose to the first file", "created": "2026-07-09"},
]}


class Env:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.legacy_dir = root / "legacy"
        self.legacy_dir.mkdir()
        shutil.copy(FIXTURES / "legacy_sample.json", self.legacy_dir / "lessons-a.json")
        (self.legacy_dir / "lessons-b.json").write_text(json.dumps(LEGACY_B))
        self.classified = root / "classified.jsonl"
        shutil.copy(FIXTURES / "classified_sample.jsonl", self.classified)
        self.data_dir = root / "store"
        self.config = root / "config.json"
        self.config.write_text(json.dumps({"data_dir": str(self.data_dir), "deny_terms": DENY}))
        self.out = root / "out"
        self.overrides: Path | None = None

    @property
    def cfg(self):
        return cfgmod.load(self.config)

    @property
    def log(self) -> DeltaLog:
        return DeltaLog(self.cfg.deltas, tuple(self.cfg.deny_terms))

    def set_overrides(self, rows: list[dict]) -> None:
        self.overrides = self.root / "overrides.jsonl"
        self.overrides.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def set_classified(self, rows: list[dict]) -> None:
        self.classified.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def classified_rows(self) -> list[dict]:
        return [json.loads(ln) for ln in self.classified.read_text().splitlines() if ln]

    def run(self, mode: str = "--apply", *extra: str) -> int:
        argv = ["--legacy-glob", str(self.legacy_dir / "lessons-*.json"),
                "--classified", str(self.classified), "--config", str(self.config),
                "--out-dir", str(self.out), mode, *extra]
        if self.overrides:
            argv += ["--overrides", str(self.overrides)]
        return main(argv)

    # ------------------------------------------------------------ outputs
    def read(self, name: str) -> str:
        return (self.out / name).read_text()

    def id_map(self) -> dict[str, dict]:
        return {r["id"]: r for r in (json.loads(ln) for ln in self.read("id_map.jsonl").splitlines() if ln)}

    def handoff(self) -> list[dict]:
        return [json.loads(ln) for ln in self.read("handoff.jsonl").splitlines() if ln]

    def count(self, name: str) -> int:
        """Value of the first '- <name>: N' line in the report."""
        m = re.search(rf"^- {re.escape(name)}: (\d+)", self.read("MIGRATION_REPORT.md"), re.M)
        assert m, f"no '{name}' line in report"
        return int(m.group(1))

    def packet_section(self, bucket: str) -> str:
        text = self.read("review_packet.md")
        m = re.search(rf"^## {re.escape(bucket)}\b.*?(?=^## |\Z)", text, re.M | re.S)
        assert m, f"no '{bucket}' section in review packet"
        return m.group(0)

    def stored(self) -> dict[str, dict]:
        return self.log.state()

    def by_text(self, text: str) -> dict:
        hits = [les for les in self.stored().values() if les["text"] == text]
        assert len(hits) == 1, f"{len(hits)} stored lessons with text {text!r}"
        return hits[0]
