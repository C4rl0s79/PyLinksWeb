"""services.collections — kolekcje serii (gry z różnych platform w 1 folderze).

Port z wersji tkinter: LINKS/Kolekcje/<nazwa>/ z kopiami skrótów .lnk/.url z
poszczególnych platform. Te same tytuły dostają NIEWIDZIALNY sufiks (zero-width),
więc pliki mają różne nazwy, ale identyczny podpis pod ikoną.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pylinks.core.naming import safe_name

_ZW = ("​", "‌", "‍")   # ZWSP / ZWNJ / ZWJ — niewidzialne


def zw_suffix(i: int) -> str:
    """Deterministyczny niewidzialny sufiks dla indeksu i (0 = brak)."""
    if i <= 0:
        return ""
    i -= 1
    s = _ZW[i % 3]; i //= 3
    while i > 0:
        s += _ZW[i % 3]; i //= 3
    return s


def enumerate_link_games(links_dir: Path, collections_name: str = "Kolekcje") -> list:
    """Wszystkie skróty LINKS/<platforma>/*.lnk|.url (bez folderu Kolekcje)."""
    out = []
    if not Path(links_dir).is_dir():
        return out
    for pd in sorted(Path(links_dir).iterdir()):
        if not pd.is_dir() or pd.name.lower() in (collections_name.lower(), "_collections"):
            continue
        for f in pd.iterdir():
            if f.suffix.lower() in (".lnk", ".url"):
                # stem bywa z doklejoną spacją (sortowanie serii w Explorerze) —
                # w kolekcjach trzymamy i pokazujemy nazwę bez niej
                out.append({"name": f.stem.rstrip(), "platform": pd.name,
                            "ext": f.suffix.lower()})
    out.sort(key=lambda m: (m["name"].lower(), m["platform"].lower()))
    return out


def generate_collection(links_dir: Path, cname: str, members: list) -> dict:
    """Buduje LINKS/Kolekcje/<cname>/ z kopii skrótów. Zwraca {made, missing}."""
    coll_dir = Path(links_dir) / "Kolekcje" / safe_name(cname)
    coll_dir.mkdir(parents=True, exist_ok=True)
    for old in list(coll_dir.glob("*.lnk")) + list(coll_dir.glob("*.url")):
        try:
            old.unlink()
        except Exception:
            pass
    by_name: dict = {}
    for m in members:
        by_name.setdefault(m["name"], []).append(m)
    made, missing = 0, []
    for base, group in by_name.items():
        group = sorted(group, key=lambda m: m["platform"].lower())
        for i, m in enumerate(group):
            src = None
            # plik może mieć doklejoną spację (sortowanie serii) — sprawdzamy obie formy
            for stem in (m["name"], m["name"] + " "):
                for ext in (".lnk", ".url"):
                    p = Path(links_dir) / m["platform"] / f"{stem}{ext}"
                    if p.exists():
                        src = p; break
                if src:
                    break
            if src is None:
                missing.append(f"{m['platform']}/{m['name']}")
                continue
            dst = coll_dir / f"{m['name']}{zw_suffix(i)}{src.suffix}"
            try:
                shutil.copy2(src, dst); made += 1
            except Exception as ex:
                missing.append(f"{m['platform']}/{m['name']} ({ex})")
    return {"made": made, "missing": missing, "path": str(coll_dir)}
