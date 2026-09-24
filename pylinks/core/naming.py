"""core.naming — normalizacja nazw plików/tytułów.

Ownership: safe_name — zamiana tytułu gry na bezpieczną nazwę pliku skrótu.

Behavior preserved from legacy single-file module.
"""

from __future__ import annotations

import re


def safe_name(name: str) -> str:
    for ch in ["\u2122", "\u00ae", "\u2026", "\u2019", "\u2018"]:
        name = name.replace(ch, "")
    name = re.sub(r'[<>:"/\\|?*\']', "_", name)
    name = re.sub(r"[\x00-\x1f]", "", name)
    return re.sub(r"\s+", " ", name).strip().rstrip(". ")[:120]


def norm_title(name: str) -> str:
    """Znormalizowany tytu\u0142 do por\u00f3wna\u0144 duplikat\u00f3w (dedup mi\u0119dzy \u017ar\u00f3d\u0142ami).

    Interpunkcja \u2192 spacja (nie usuwanie), by nie skleja\u0107 token\u00f3w serii
    ("I & II" vs "III"). "&" \u2261 "and".
    """
    s = (name or "").lower()
    s = re.sub(r"[\u2122\u00ae]", "", s)              # \u2122 \u00ae
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9\u0105\u0107\u0119\u0142\u0144\u00f3\u015b\u017a\u017c]+", " ", s)          # interpunkcja \u2192 spacja
    return re.sub(r"\s+", " ", s).strip()
