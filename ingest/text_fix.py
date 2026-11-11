"""Repair mojibake (UTF-8 text that was mis-decoded as Windows-1252).

About 1,100 BigBasket descriptions contain sequences like "â€™" (should be "’")
or "Ã‚Â" (a doubly mis-decoded non-breaking space). We re-encode each suspicious
run of characters as cp1252 and decode it as UTF-8, repeating for runs that were
mangled more than once. Runs that don't round-trip are left untouched.
"""
from __future__ import annotations

import re

# Characters that cp1252 produces from UTF-8 lead/continuation bytes.
_CP1252_EXTRA = "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ"
_RUN_RE = re.compile(f"[\u00a0-\u00ff{_CP1252_EXTRA}]{{2,}}")
# Some runs were mangled several times and lost bytes, so they can't round-trip.
# They originally held spaces or apostrophes, so after repair we scrub what's left:
# a cluster of 2+ mojibake marker characters, or a lone Â/Ã before a space.
_MARKERS = "\u00c2\u00c3\u00e2\u00c6\u00a2\u00ac\u20ac\u201a\u0192\u201e\u0161\u2026\u00a0"
# A lone "Â" never occurs in this English catalogue except as mojibake ("oilÂis"),
# and C1 control characters (\x80-\x9f) are invisible leftovers of lost bytes.
_LEFTOVER_RE = re.compile(f"[{_MARKERS}]{{2,}}|[\u00c3](?=\\s|$)|\u00c2|[\x80-\x9f]|\ufffd")


def _fix_run(run: str, max_rounds: int = 3) -> str:
    for _ in range(max_rounds):
        try:
            fixed = run.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if fixed == run:
            break
        run = fixed
    return run


def fix_mojibake(text: str) -> str:
    """Return ``text`` with mis-decoded UTF-8 runs repaired."""
    if not text:
        return text
    text = _RUN_RE.sub(lambda m: _fix_run(m.group(0)), text)
    # a lone "Â" glued between letters is dropped; clusters become a space
    text = re.sub("(?<=\\w)\u00c2(?=\\w)", "", text)
    return _LEFTOVER_RE.sub(" ", text)
