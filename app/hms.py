"""Plain-text descriptions for Bambu Lab HMS / print error codes.

Bambu publishes the same tables Bambu Studio uses at e.bambulab.com. They are downloaded on
first use per language, cached on disk for a week, and used offline after that.
"""

import json
import os
import threading
import time
import urllib.request

from common import data_dir

URL = "https://e.bambulab.com/query.php?lang=%s"
MAX_AGE = 7 * 86400
LANGS = ("en", "de", "fr", "es", "it", "nl", "sv", "cs", "pl", "pt", "hu", "ja", "ko", "zh", "ru", "tr", "uk")

_lock = threading.Lock()
_tables = {}       # lang -> {"hms": {ecode: text}, "err": {ecode: text}}
_loading = set()


def _cache_path(lang):
    return os.path.join(data_dir(), "cache", "hms_%s.json" % lang)


def _parse(raw, lang):
    data = raw.get("data", {})

    def table(key):
        block = data.get(key, {})
        rows = block.get(lang) or next((v for k, v in block.items() if k != "ver" and isinstance(v, list)), [])
        return {r["ecode"].upper(): r.get("intro", "") for r in rows if r.get("ecode")}

    return {"hms": table("device_hms"), "err": table("device_error")}


def _load(lang):
    path = _cache_path(lang)
    raw = None
    if os.path.isfile(path) and time.time() - os.path.getmtime(path) < MAX_AGE:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    else:
        try:
            req = urllib.request.Request(URL % lang, headers={"User-Agent": "NoCali"})
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = json.loads(r.read().decode("utf-8"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f)
        except Exception:
            if os.path.isfile(path):  # offline: use stale cache
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
    table = _parse(raw, lang) if raw else {"hms": {}, "err": {}}
    with _lock:
        _tables[lang] = table
        _loading.discard(lang)


def _table(lang):
    lang = lang if lang in LANGS else "en"
    with _lock:
        t = _tables.get(lang)
        if t is None and lang not in _loading:
            _loading.add(lang)
            threading.Thread(target=_load, args=(lang,), daemon=True).start()
    return t or {"hms": {}, "err": {}}


def wiki_url(code):
    return "https://wiki.bambulab.com/en/x1/troubleshooting/hmscode/" + code


def describe_hms(items, lang="en"):
    """items: the report's "hms" list of {"attr": int, "code": int}."""
    t = _table(lang)
    out = []
    for it in items or []:
        try:
            attr, code = int(it.get("attr", 0)), int(it.get("code", 0))
        except (TypeError, ValueError):
            continue
        ecode = "%08X%08X" % (attr, code)
        pretty = "%04X_%04X_%04X_%04X" % (attr >> 16, attr & 0xFFFF, code >> 16, code & 0xFFFF)
        level = (code >> 16) & 0xFFFF  # 1 fatal, 2 serious, 3 common, 4 info
        out.append({"code": pretty, "text": t["hms"].get(ecode) or "", "level": level, "url": wiki_url(pretty)})
    return out


def describe_error(print_error, lang="en"):
    try:
        val = int(print_error or 0)
    except (TypeError, ValueError):
        return None
    if not val:
        return None
    ecode = "%08X" % val
    pretty = "%04X_%04X" % (val >> 16, val & 0xFFFF)
    return {"code": pretty, "text": _table(lang)["err"].get(ecode) or "", "url": wiki_url(pretty)}
