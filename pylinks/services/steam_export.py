"""services.steam_export — dodanie gier non-Steam do biblioteki Steam.

Port z wersji tkinter: binarny shortcuts.vdf (KeyValues KV1), grafiki do
userdata\\<id>\\config\\grid\\ (grid/hero/logo/icon nazwane po appid), oraz
kolekcje per platforma w cloudstorage. Windows; wymaga zamkniętego Steama.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import shutil
import struct
import time
import uuid
from datetime import datetime
from pathlib import Path

from pylinks.services import art as _art
from pylinks.services import export as _exp
from pylinks.core.naming import safe_name

# typ_key → (szablon nazwy pliku po appid, źródłowy aset steam-cache, is_icon)
STEAM_ART_MAP = [
    ("grid", "{appid}p.png", "grid", False),   # okładka pionowa 600x900
    ("hero", "{appid}_hero.png", "hero", False),
    ("logo", "{appid}_logo.png", "logo", False),
]


# ── binarny VDF ────────────────────────────────────────────────────────────
def _bvdf_dump(key: str, val) -> bytes:
    kb = key.encode("utf-8") + b"\x00"
    if isinstance(val, dict):
        out = bytearray(b"\x00" + kb)
        for k, v in val.items():
            out += _bvdf_dump(str(k), v)
        out += b"\x08"
        return bytes(out)
    if isinstance(val, bool):
        return b"\x02" + kb + struct.pack("<I", 1 if val else 0)
    if isinstance(val, int):
        return b"\x02" + kb + struct.pack("<I", val & 0xFFFFFFFF)
    return b"\x01" + kb + str(val).encode("utf-8") + b"\x00"


def _bvdf_parse_object(data: bytes, pos: int):
    result: dict = {}
    n = len(data)
    while pos < n:
        typ = data[pos]; pos += 1
        if typ == 0x08:
            return result, pos
        ke = data.index(b"\x00", pos)
        key = data[pos:ke].decode("utf-8", "replace"); pos = ke + 1
        if typ == 0x00:
            child, pos = _bvdf_parse_object(data, pos); result[key] = child
        elif typ == 0x01:
            ve = data.index(b"\x00", pos)
            result[key] = data[pos:ve].decode("utf-8", "replace"); pos = ve + 1
        elif typ == 0x02:
            result[key] = struct.unpack_from("<I", data, pos)[0]; pos += 4
        elif typ == 0x07:
            result[key] = struct.unpack_from("<Q", data, pos)[0]; pos += 8
        else:
            raise ValueError(f"nieznany typ VDF 0x{typ:02x}")
    raise ValueError("brak 0x08")


def read_shortcuts_vdf(path: Path) -> list:
    try:
        data = path.read_bytes()
        if len(data) < 2 or data[0] != 0x00:
            return []
        ke = data.index(b"\x00", 1)
        obj, _ = _bvdf_parse_object(data, ke + 1)
        return [obj[k] for k in obj] if isinstance(obj, dict) else []
    except Exception:
        return []


def write_shortcuts_vdf(entries: list) -> bytes:
    shortcuts = {str(i): e for i, e in enumerate(entries)}
    return _bvdf_dump("shortcuts", shortcuts) + b"\x08"


# ── userdata / grid ────────────────────────────────────────────────────────
def find_userdata_configs(steam_exe: str = "", override: str = "") -> list:
    """Zwraca listę katalogów config (userdata\\<id>\\config) z shortcuts.vdf."""
    if override and Path(override).is_dir():
        return [Path(override)]
    bases = []
    if steam_exe:
        bases.append(Path(steam_exe).parent / "userdata")
    bases += [Path(os.environ.get("LOCALAPPDATA", "C:/")) / "Steam" / "userdata",
              Path("C:/Program Files (x86)/Steam/userdata"),
              Path("C:/Program Files/Steam/userdata")]
    out = []
    seen = set()
    for base in bases:
        if base.is_dir():
            for ud in sorted(base.iterdir()):
                if ud.is_dir() and ud.name.isdigit():
                    cfg = ud / "config"
                    if str(cfg).lower() not in seen:
                        seen.add(str(cfg).lower())
                        out.append(cfg)
    return out


def is_steam_running() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Valve\Steam\ActiveProcess") as k:
            pid, _ = winreg.QueryValueEx(k, "pid")
            return int(pid) != 0
    except Exception:
        return False


# ── budowa wpisu ───────────────────────────────────────────────────────────
def _shortcut_appid(exe: str, name: str) -> int:
    return (binascii.crc32((exe + name).encode("utf-8")) | 0x80000000) & 0xFFFFFFFF


def _tags_for(game: dict) -> list:
    tags = ["Non-Steam"]
    src = game.get("source", "")
    if src == "rom":
        tags.append(game.get("system", "") or "ROM")
    elif src == "extra":
        tags.append("Windows")
    elif src:
        tags.append(src.capitalize())
    seen, out = set(), []
    for t in tags:
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t); out.append(t)
    return out


def _build_entry(game: dict, launch: dict, icon_path: str = ""):
    """launch = wynik export._launch_for. Zwraca (entry, appid, platform_tag)."""
    if launch["kind"] == "copy":                 # PS3 .lnk → rozwiń
        try:
            import pythoncom, win32com.client  # type: ignore
            pythoncom.CoInitialize()
            sc = win32com.client.Dispatch("WScript.Shell").CreateShortcut(launch["src"])
            exe, args, start = sc.TargetPath, sc.Arguments, sc.WorkingDirectory
            pythoncom.CoUninitialize()
        except Exception:
            return None, 0, ""
    elif launch["kind"] == "url":
        return None, 0, ""                       # protokoły (Steam/Epic) — pomijamy
    else:
        exe, args, start = launch["target"], launch.get("args", ""), launch.get("work_dir", "")
    if not exe:
        return None, 0, ""
    name = game.get("name", "")
    appid = _shortcut_appid(exe, name)
    tags = _tags_for(game)
    entry = {
        "appid": appid, "AppName": name,
        "Exe": f'"{exe}"' if not exe.startswith('"') else exe,
        "StartDir": start or (str(Path(exe).parent) if Path(exe).exists() else ""),
        "icon": icon_path, "ShortcutPath": "", "LaunchOptions": args,
        "IsHidden": 0, "AllowDesktopConfig": 1, "AllowOverlay": 1, "OpenVR": 0,
        "Devkit": 0, "DevkitGameID": "", "DevkitOverrideAppID": 0, "LastPlayTime": 0,
        "FlatpakAppID": "", "tags": {str(i): t for i, t in enumerate(tags)},
    }
    return entry, appid, (tags[1] if len(tags) > 1 else "")


# ── kolekcje (cloudstorage) ────────────────────────────────────────────────
def _new_collection_id(name: str) -> str:
    h = hashlib.md5(("pylinks::" + name).encode("utf-8")).digest()
    return "uc-" + base64.urlsafe_b64encode(h).decode("ascii").rstrip("=")[:12]


def write_collections(config_dir: Path, tag_appids: dict) -> dict:
    cs = Path(config_dir) / "cloudstorage"
    ns1 = cs / "cloud-storage-namespace-1.json"
    res = {"created": [], "updated": [], "ok": False, "error": None}
    if not ns1.exists():
        res["error"] = "brak magazynu kolekcji (otwórz raz bibliotekę Steam)"
        return res
    try:
        arr = json.loads(ns1.read_text(encoding="utf-8"))
        if not isinstance(arr, list):
            raise ValueError("format")
    except Exception as ex:
        res["error"] = f"nie można odczytać kolekcji: {ex}"; return res
    try:
        shutil.copytree(cs, cs.with_name(f"cloudstorage.bak-{datetime.now():%Y%m%d-%H%M%S}"))
    except Exception as ex:
        res["error"] = f"backup nieudany: {ex}"; return res

    ns_file = cs / "cloud-storage-namespaces.json"
    try:
        ns_list = json.loads(ns_file.read_text(encoding="utf-8"))
        if not isinstance(ns_list, list):
            ns_list = []
    except Exception:
        ns_list = []
    cur = 0
    for pr in ns_list:
        if isinstance(pr, list) and len(pr) == 2 and pr[0] == 1:
            try: cur = int(pr[1])
            except Exception: cur = 0

    name_to_item, max_ver = {}, cur
    for item in arr:
        if not (isinstance(item, list) and len(item) == 2):
            continue
        key, wrap = item
        if not (isinstance(key, str) and key.startswith("user-collections.")
                and isinstance(wrap, dict)):
            continue
        try: max_ver = max(max_ver, int(wrap.get("version", 0)))
        except Exception: pass
        if wrap.get("is_deleted") or "value" not in wrap:
            continue
        try: v = json.loads(wrap["value"])
        except Exception: continue
        if v.get("name"):
            name_to_item.setdefault(v["name"], [item, v])

    now, ver, changed = int(time.time()), max(max_ver, cur), []
    for tag, appids in tag_appids.items():
        appids = sorted({int(a) for a in appids})
        if not appids:
            continue
        if tag in name_to_item:
            item, v = name_to_item[tag]
            old = list(v.get("added", []) or [])
            merged = list(dict.fromkeys(old + appids))
            if merged == old:
                continue
            v["added"] = merged
            v["removed"] = [r for r in (v.get("removed") or []) if r not in set(appids)]
            ver += 1
            item[1]["value"] = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
            item[1]["timestamp"] = now; item[1]["version"] = str(ver)
            changed.append(item[0]); res["updated"].append(f"{tag} (+{len(set(appids)-set(old))})")
        else:
            cid = _new_collection_id(tag); key = f"user-collections.{cid}"
            v = {"id": cid, "name": tag, "added": appids, "removed": []}
            ver += 1
            arr.append([key, {"key": key, "timestamp": now,
                              "value": json.dumps(v, separators=(",", ":"), ensure_ascii=False),
                              "version": str(ver)}])
            changed.append(key); res["created"].append(f"{tag} ({len(appids)})")

    if not changed:
        res["ok"] = True; return res

    mod_file = cs / "cloud-storage-namespace-1.modified.json"
    try:
        mod = json.loads(mod_file.read_text(encoding="utf-8"))
        if not isinstance(mod, list): mod = []
    except Exception:
        mod = []
    for k in changed:
        if k not in mod: mod.append(k)
    new_ns, seen1 = [], False
    for pr in ns_list:
        if isinstance(pr, list) and len(pr) == 2 and pr[0] == 1:
            new_ns.append([1, str(ver)]); seen1 = True
        else:
            new_ns.append(pr)
    if not seen1:
        new_ns.append([1, str(ver)])

    def _atomic(p: Path, text: str):
        tmp = p.with_name(p.name + f".tmp-{uuid.uuid4().hex}")
        tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)
    try:
        _atomic(ns1, json.dumps(arr, separators=(",", ":"), ensure_ascii=False))
        _atomic(mod_file, json.dumps(mod, separators=(",", ":"), ensure_ascii=False))
        _atomic(ns_file, json.dumps(new_ns, separators=(",", ":"), ensure_ascii=False))
    except Exception as ex:
        res["error"] = f"błąd zapisu kolekcji: {ex}"; return res
    res["ok"] = True
    return res


# ── grafiki do grid/ ───────────────────────────────────────────────────────
def _copy_art(cache_dir: Path, key: str, grid_dir: Path, appid: int) -> int:
    """Kopiuje steam-cache (grid/hero/logo) do grid_dir jako <appid>*.png."""
    grid_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for asset, tpl, _src, _ico in STEAM_ART_MAP:
        data = _steam_asset_bytes(cache_dir, key, asset)
        if not data:
            continue
        try:
            (grid_dir / tpl.format(appid=appid)).write_bytes(data)
            n += 1
        except Exception:
            pass
    return n


def _steam_asset_bytes(cache_dir: Path, key: str, asset: str) -> "bytes | None":
    from pylinks.services.art import _safe_key
    d = Path(cache_dir) / "covers" / "steam"
    name = _safe_key(key) if asset == "grid" else f"{_safe_key(key)}__{asset}"
    for f in d.glob(name + ".*"):
        try:
            return f.read_bytes()
        except Exception:
            return None
    return None


# ── orkiestracja ───────────────────────────────────────────────────────────
def export_to_steam(cfg: dict, games: list, cache_dir: Path) -> dict:
    from pylinks.api import _game_key
    configs = find_userdata_configs(cfg.get("steam_exe", ""),
                                    cfg.get("steam_userdata_dir", ""))
    if not configs:
        return {"ok": False, "reason": "nie znaleziono userdata Steam"}
    config_dir = configs[0]
    vdf_path = config_dir / "shortcuts.vdf"
    grid_dir = config_dir / "grid"

    existing = read_shortcuts_vdf(vdf_path)
    by_appid = {}
    for e in existing:
        try:
            by_appid[int(e.get("appid", 0) or 0)] = e
        except Exception:
            pass

    creator = _exp.ShortcutCreator(cfg.get("steam_exe", ""))
    logo_dir = cfg.get("platform_logo_dir", "")
    spine_on = bool(cfg.get("icon_platform_spine", False))
    spine_side = cfg.get("icon_spine_side", "left")
    icons_dir = Path(cache_dir) / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)

    added, art_n, skipped = 0, 0, 0
    tag_appids: dict = {}
    for g in games:
        launch = _exp._launch_for(g, creator)
        if not launch:
            skipped += 1; continue
        key = _game_key(g)
        # ikona .ico (grzbiet dla ROM)
        fit, off = ("pad", 0.5)
        store = cfg.get("icon_crop_by_key", {}) or {}
        if isinstance(store.get(key), dict):
            fit, off = store[key].get("fit", "pad"), float(store[key].get("offset", 0.5))
        spine_sys = g.get("system", "") if (spine_on and g.get("source") == "rom") else ""
        ico = _exp.bake_ico(cache_dir, key, fit, off, spine_sys, spine_side, logo_dir)
        ico_path = ""
        if ico:
            ip = icons_dir / f"{_exp._safe_ico(key)}.ico"
            try:
                ip.write_bytes(ico); ico_path = str(ip)
            except Exception:
                pass
        entry, appid, plat = _build_entry(g, launch, ico_path)
        if not entry:
            skipped += 1; continue
        # grafiki grid/hero/logo do grid/
        art_n += _copy_art(cache_dir, key, grid_dir, appid)
        # ikona Steam też jako <appid>.ico
        if ico:
            try:
                (grid_dir / f"{appid}.ico").write_bytes(ico)
            except Exception:
                pass
        by_appid[appid] = entry
        added += 1
        if plat:
            tag_appids.setdefault(plat, []).append(appid)

    # zapis shortcuts.vdf (backup + atomowo)
    try:
        if vdf_path.exists():
            shutil.copy2(vdf_path, vdf_path.with_name(
                f"shortcuts.vdf.bak-{datetime.now():%Y%m%d-%H%M%S}"))
        blob = write_shortcuts_vdf(list(by_appid.values()))
        tmp = vdf_path.with_name(f"shortcuts.tmp-{uuid.uuid4().hex}.vdf")
        tmp.write_bytes(blob); os.replace(tmp, vdf_path)
    except Exception as ex:
        return {"ok": False, "reason": f"zapis shortcuts.vdf: {ex}"}

    coll = write_collections(config_dir, tag_appids)
    return {"ok": True, "added": added, "art": art_n, "skipped": skipped,
            "config_dir": str(config_dir), "collections": coll,
            "steam_running": is_steam_running()}
