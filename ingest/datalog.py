"""Write generated sections into docs/DATA_LOG.md between marker comments.

Each cleaning script owns one section, so re-running a script replaces its
section in place instead of appending duplicates. Hand-written text outside
the markers is left alone.
"""
from __future__ import annotations

import re
from pathlib import Path


def write_section(log_path: Path, section: str, body: str) -> None:
    """Replace (or append) the block between BEGIN/END markers for ``section``."""
    begin, end = f"<!-- BEGIN {section} (generated) -->", f"<!-- END {section} -->"
    block = f"{begin}\n{body.strip()}\n{end}"
    text = log_path.read_text(encoding="utf-8") if log_path.exists() else "# Data log\n"
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    if pattern.search(text):
        text = pattern.sub(lambda _: block, text)
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(text, encoding="utf-8")


class StepLog:
    """Collect 'step → rows remaining' lines for the data log."""

    def __init__(self, start_rows: int) -> None:
        self.rows: list[tuple[str, int, int]] = [("Loaded raw file", start_rows, 0)]
        self._last = start_rows

    def step(self, name: str, rows_now: int) -> None:
        self.rows.append((name, rows_now, self._last - rows_now))
        self._last = rows_now
        print(f"  {name:<60} {rows_now:>6} rows  (-{self.rows[-1][2]})")

    def to_markdown(self) -> str:
        lines = ["| Step | Rows after | Rows removed |", "|---|---:|---:|"]
        lines += [f"| {n} | {r:,} | {d:,} |" for n, r, d in self.rows]
        return "\n".join(lines)
