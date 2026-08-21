"""core.matching — heurystyki dopasowania tytułów.

Ownership: podobieństwo bigramowe (name_similarity).

Behavior preserved from legacy single-file module.
"""

from __future__ import annotations

import re


def name_similarity(a: str, b: str) -> float:
    def bigrams(s: str) -> set[str]:
        s = re.sub(r"[^a-z0-9 ]", "", s.lower())
        return set(s[i:i + 2] for i in range(len(s) - 1))
    ba, bb = bigrams(a), bigrams(b)
    if not ba and not bb:
        return 1.0
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))
