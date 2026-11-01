"""A29 Sanktionslisten-Abgleich: Name/Firma gegen OFAC (SDN), UN-Sicherheitsrat, EU und SECO (Schweiz).

Alle vier Listen sind amtlich und frei abrufbar. Die Listen werden beim ersten Aufruf geladen
(danach alle 12 Stunden neu) und nur die Namen, Aliasse, Typ und Programm im Speicher gehalten.
Fällt eine Liste aus, wird mit den übrigen geprüft und das in der Antwort gesagt.
Das Ergebnis ist ein Hinweis (Namensähnlichkeit), keine Rechtsberatung und keine Identitätsprüfung.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET

import httpx
from rapidfuzz import fuzz, process

import sources as src

LISTS = {
    "OFAC": {"name": "US Treasury OFAC Specially Designated Nationals (SDN) List",
             "urls": ["https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV",
                      "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ALT.CSV"]},
    "UN": {"name": "UN Security Council Consolidated List",
           "urls": ["https://scsanctions.un.org/resources/xml/en/consolidated.xml"]},
    "EU": {"name": "EU Consolidated Financial Sanctions List",
           "urls": ["https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content"
                    "?token=dG9rZW4tMjAxNw"]},
    "SECO": {"name": "Swiss SECO Sanctions List (Gesamtliste)",
             "urls": ["https://www.sesam.search.admin.ch/sesam-search-web/pages/downloadXmlGesamtliste.xhtml"
                      "?lang=en&action=downloadXmlGesamtlisteAction"]},
}
TTL = 12 * 3600
_lock = threading.Lock()
_index: dict = {"entries": [], "names": [], "loaded": {}, "errors": {}, "at": 0.0}
DISCLAIMER = ("Name-similarity screening hint only, not legal advice and not an identity verification. "
              "Confirm any hit with the official list and further identifiers (date of birth, nationality, IDs).")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _get(url: str) -> bytes:
    r = httpx.get(url, headers={"User-Agent": src.UA["User-Agent"]}, timeout=90, follow_redirects=True)
    r.raise_for_status()
    return r.content


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ------------------------------------------------------------------ Parser
def parse_ofac(sdn: bytes, alt: bytes) -> list[dict]:
    out, by_id = [], {}
    for row in csv.reader(io.StringIO(sdn.decode("utf-8", "replace"))):
        if len(row) < 4 or not row[0].strip().isdigit():
            continue
        typ = row[2].strip().strip('"')
        e = {"list": "OFAC", "id": row[0].strip(), "name": row[1].strip(), "aliases": [],
             "type": "individual" if typ.lower() == "individual" else
                     (typ.lower() if typ not in ("-0-", "") else "entity"),
             "programs": [p.strip() for p in row[3].replace("] [", ";").strip("[] ").split(";") if p.strip()]}
        by_id[e["id"]] = e
        out.append(e)
    for row in csv.reader(io.StringIO(alt.decode("utf-8", "replace"))):
        if len(row) >= 4 and row[0].strip() in by_id and row[3].strip() not in ("", "-0-"):
            by_id[row[0].strip()]["aliases"].append(row[3].strip())
    return out


def _ends(xml: bytes, tags: set[str]):
    """Speichersparend: liefert fertige Elemente mit gesuchtem Namen und gibt sie danach frei."""
    for _, el in ET.iterparse(io.BytesIO(xml), events=("end",)):
        if _local(el.tag) in tags:
            yield _local(el.tag), el
            el.clear()


def parse_un(xml: bytes) -> list[dict]:
    out = []
    kinds = {"INDIVIDUAL": ("individual", "INDIVIDUAL_ALIAS"), "ENTITY": ("entity", "ENTITY_ALIAS")}
    for tag, el in _ends(xml, set(kinds)):
        kind, alias_tag = kinds[tag]
        if True:
            parts = [(el.findtext(t) or "").strip() for t in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")]
            name = " ".join(p for p in parts if p)
            if not name:
                continue
            aliases = [(a.findtext("ALIAS_NAME") or "").strip() for a in el.iter(alias_tag)]
            orig = (el.findtext("NAME_ORIGINAL_SCRIPT") or "").strip()
            out.append({"list": "UN", "id": (el.findtext("REFERENCE_NUMBER") or el.findtext("DATAID") or "").strip(),
                        "name": name, "aliases": [a for a in aliases + [orig] if a], "type": kind,
                        "programs": [(el.findtext("UN_LIST_TYPE") or "").strip()]})
    return out


def parse_eu(xml: bytes) -> list[dict]:
    out = []
    for _, ent in _ends(xml, {"sanctionEntity"}):
        names, typ, progs = [], "entity", set()
        for ch in ent:
            t = _local(ch.tag)
            if t == "nameAlias":
                w = (ch.get("wholeName") or " ".join(x for x in (ch.get("firstName"), ch.get("middleName"),
                                                                 ch.get("lastName")) if x)).strip()
                if w and w not in names:
                    names.append(w)
            elif t == "subjectType":
                typ = "individual" if (ch.get("code") or "").lower() == "person" else "entity"
            elif t == "regulation" and ch.get("programme"):
                progs.add(ch.get("programme"))
        if names:
            out.append({"list": "EU", "id": ent.get("euReferenceNumber") or ent.get("logicalId") or "",
                        "name": names[0], "aliases": names[1:], "type": typ, "programs": sorted(progs)})
    return out


def parse_seco(xml: bytes) -> list[dict]:
    out, progs, pending = [], {}, []
    for tag, p in _ends(xml, {"sanctions-program", "target"}):
        if tag == "sanctions-program":
            pname = next((x.text for x in p.iter() if _local(x.tag) == "program-name" and x.text), "")
            for st in p.iter():
                if _local(st.tag) == "sanctions-set" and st.get("ssid"):
                    progs[st.get("ssid")] = pname.strip()
            continue
        tgt = p
        typ, names = "entity", []
        for ch in tgt:
            if _local(ch.tag) in ("individual", "entity", "object"):
                typ = "individual" if _local(ch.tag) == "individual" else _local(ch.tag)
                for nm in ch.iter():
                    if _local(nm.tag) != "name":
                        continue
                    parts = []
                    for np_ in nm:
                        if _local(np_.tag) == "name-part":
                            v = next((x.text for x in np_ if _local(x.tag) == "value" and x.text), "")
                            parts.append((int(np_.get("order") or 0), v.strip()))
                    full = " ".join(v for _, v in sorted(parts) if v)
                    if full and full not in names:
                        names.append(full)
        if names:
            out.append({"list": "SECO", "id": tgt.get("ssid") or "", "name": names[0], "aliases": names[1:],
                        "type": typ, "programs": [tgt.get("sanctions-set-id") or ""]})
    for e in out:  # Programme können auch nach den Zielen stehen
        e["programs"] = [progs.get(e["programs"][0], "")]
    return out


PARSERS = {"OFAC": lambda b: parse_ofac(b[0], b[1]), "UN": lambda b: parse_un(b[0]),
           "EU": lambda b: parse_eu(b[0]), "SECO": lambda b: parse_seco(b[0])}


def _load_all():
    entries, loaded, errors = [], {}, {}
    old = {e["list"]: [] for e in _index["entries"]}
    for e in _index["entries"]:
        old[e["list"]].append(e)
    for key, meta in LISTS.items():
        try:
            data = [_get(u) for u in meta["urls"]]
            rows = PARSERS[key](data)
            if not rows:
                raise RuntimeError("no entries parsed")
            entries += rows
            loaded[key] = {"entries": len(rows), "loaded_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        except Exception as ex:
            errors[key] = str(ex)[:160]
            if old.get(key):  # alte Daten weiterverwenden
                entries += old[key]
                loaded[key] = dict(_index["loaded"].get(key, {}), stale=True)
    names = []
    for i, e in enumerate(entries):
        for n in [e["name"]] + e["aliases"]:
            nn = norm(n)
            if nn:
                names.append((nn, i, n))
    _index.update(entries=entries, names=names, loaded=loaded, errors=errors, at=time.time())
    src.STATUS["sanctions"] = {"ok": bool(entries), "lists": loaded, "errors": errors}


def ensure_loaded():
    if _index["entries"] and time.time() - _index["at"] < TTL:
        return
    with _lock:
        if _index["entries"] and time.time() - _index["at"] < TTL:
            return
        _load_all()
    if not _index["entries"]:
        raise src.SourceUnavailable("Sanctions lists temporarily unavailable. Please retry later.")


def check(name: str, min_score: int = 85, kind: str | None = None, lists: str | None = None,
          limit: int = 10) -> dict:
    q = norm(name)
    if len(q) < 3:
        raise ValueError("Parameter 'name' needs at least 3 letters")
    if not 50 <= min_score <= 100:
        raise ValueError("min_score must be 50-100")
    k = (kind or "").lower() or None
    if k not in (None, "individual", "entity", "vessel", "aircraft"):
        raise ValueError("type must be individual, entity, vessel or aircraft")
    want = {x.strip().upper() for x in (lists or "").split(",") if x.strip()} or set(LISTS)
    if not want <= set(LISTS):
        raise ValueError(f"lists must be a comma list of {', '.join(LISTS)}")
    ensure_loaded()
    choices = [n[0] for n in _index["names"]]
    raw = process.extract(q, choices, scorer=fuzz.WRatio, limit=400, score_cutoff=min_score - 15)
    best: dict[int, dict] = {}
    for _, score, idx in raw:
        nn, ei, orig = _index["names"][idx]
        e = _index["entries"][ei]
        if e["list"] not in want or (k and e["type"] != k):
            continue
        s = max(fuzz.token_sort_ratio(q, nn), fuzz.token_set_ratio(q, nn) - 5 if len(q.split()) > 1 else 0,
                90 if len(q.split()) == 1 and len(q) >= 5 and q in nn.split() else 0)
        if s < min_score:
            continue
        if ei not in best or s > best[ei]["score"]:
            best[ei] = {"score": round(s, 1), "matched_name": orig, "primary_name": e["name"], "list": e["list"],
                        "list_id": e["id"], "type": e["type"], "programs": [p for p in e["programs"] if p],
                        "exact": nn == q}
    hits = sorted(best.values(), key=lambda h: (-h["score"], h["list"]))[:max(1, min(limit, 50))]
    status = "potential_match" if any(h["score"] >= 95 for h in hits) else ("review" if hits else "no_match")
    return {"query": name, "normalized": q, "status": status, "matches": len(hits), "results": hits,
            "lists_checked": {kk: _index["loaded"].get(kk) for kk in sorted(want)},
            "lists_unavailable": {kk: v for kk, v in _index["errors"].items() if kk in want and kk not in _index["loaded"]},
            "min_score": min_score, "disclaimer": DISCLAIMER,
            "source": "Official lists: OFAC SDN (US Treasury), UN Security Council, EU FSF, Swiss SECO"}


def preload():
    """Im Hintergrund beim Serverstart laden, damit der erste Aufruf nicht warten muss."""
    try:
        ensure_loaded()
    except Exception:
        pass
