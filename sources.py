"""Datenquellen der Risk Events API (alle frei nutzbar, US Government Work / public domain).

- A20  National Weather Service (api.weather.gov): aktive Unwetterwarnungen
- A21  USGS Earthquake Hazards Program (earthquake.usgs.gov): Erdbebenkatalog
- A22  openFDA (api.fda.gov): Rückrufe (Enforcement Reports) für Lebensmittel, Medikamente, Medizinprodukte

Abrufe werden kurz zwischengespeichert. Fällt eine Quelle aus, werden die letzten Daten
weiterverwendet; gibt es keine, antwortet die API mit 503 (der Agent zahlt dann nichts).
"""
from __future__ import annotations

import datetime as dt
import threading
import time

import httpx

UA = {"User-Agent": "RiskEventsAPI/0.1 (contact: data-api@users.noreply.github.com)",
      "Accept": "application/geo+json, application/json"}
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()
STATUS: dict[str, dict] = {}


class SourceUnavailable(RuntimeError):
    pass


def cached(key: str, ttl: int, loader):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        val = loader()
        with _lock:
            _cache[key] = (now, val)
            if len(_cache) > 2000:  # Speicher begrenzen (Gratis-Server)
                for k in sorted(_cache, key=lambda k: _cache[k][0])[:500]:
                    _cache.pop(k, None)
        STATUS[key.split("|")[0]] = {"ok": True, "loaded_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        return val
    except Exception as e:  # alte Daten weiterverwenden, falls vorhanden
        STATUS[key.split("|")[0]] = {"ok": False, "error": str(e)[:200],
                                     "at": dt.datetime.now(dt.timezone.utc).isoformat()}
        if hit:
            return hit[1]
        raise SourceUnavailable(f"Data source temporarily unavailable ({key.split('|')[0]}). Please retry later.")


def get_json(url: str, params: dict | None = None, timeout: float = 25, allow_404: bool = False):
    r = httpx.get(url, params=params, headers=UA, timeout=timeout, follow_redirects=True)
    if allow_404 and r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


NWS = "https://api.weather.gov/alerts/active"
USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"
FDA = "https://api.fda.gov/{kind}/enforcement.json"


def nws_alerts(params: dict) -> dict:
    key = "nws|" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return cached(key, 120, lambda: get_json(NWS, params))


def usgs_events(params: dict) -> dict:
    key = "usgs|" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return cached(key, 120, lambda: get_json(USGS, {"format": "geojson", **params}))


def fda_recalls(kind: str, qs: str) -> dict:
    """qs ist der fertig kodierte Query-String (openFDA braucht '+AND+' unkodiert)."""
    url = FDA.format(kind=kind) + "?" + qs
    return cached(f"openfda|{kind}|{qs}", 1800, lambda: get_json(url, allow_404=True)
                  or {"meta": {"results": {"total": 0}}, "results": []})
