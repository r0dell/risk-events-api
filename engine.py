"""Rechenkern der Risk Events API.

A20  US-Unwetterwarnungen (NWS)      /v1/weather-alerts
A21  Erdbeben-Monitor (USGS)         /v1/earthquakes
A22  US-Produktrückrufe (openFDA)    /v1/recalls
"""
from __future__ import annotations

import datetime as dt
import math
import re
from urllib.parse import quote

import sources as src

UTC = dt.timezone.utc

US_STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
                "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR GU VI AS MP".split())
SEVERITIES = ["Extreme", "Severe", "Moderate", "Minor", "Unknown"]


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def _latlon(lat, lon) -> tuple[float, float]:
    if lat is None or lon is None:
        raise ValueError("Give both lat and lon")
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat must be -90..90 and lon -180..180")
    return lat, lon


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(h))


# ================================================================== A20 Unwetterwarnungen
def weather_alerts(state: str | None = None, lat: float | None = None, lon: float | None = None,
                   min_severity: str | None = None, event: str | None = None, limit: int = 25) -> dict:
    params: dict = {}
    if state:
        st = state.upper().strip()
        if st not in US_STATES:
            raise ValueError(f"Unknown US state code '{state}'. Use two letters, e.g. TX, FL, CA")
        params["area"] = st
        where = {"state": st}
    elif lat is not None or lon is not None:
        la, lo = _latlon(lat, lon)
        params["point"] = f"{la:.4f},{lo:.4f}"
        where = {"lat": la, "lon": lo}
    else:
        raise ValueError("Give a US state (state=TX) or a point (lat=..&lon=..)")
    if min_severity:
        ms = min_severity.strip().capitalize()
        if ms not in SEVERITIES[:-1]:
            raise ValueError("min_severity must be one of Extreme, Severe, Moderate, Minor")
    limit = max(1, min(int(limit), 100))
    data = src.nws_alerts(params)
    out = []
    for f in data.get("features", []):
        p = f.get("properties", {})
        sev = p.get("severity") or "Unknown"
        if min_severity and (sev not in SEVERITIES or SEVERITIES.index(sev) > SEVERITIES.index(ms)):
            continue
        if event and event.lower() not in (p.get("event") or "").lower():
            continue
        out.append({
            "event": p.get("event"), "severity": sev, "urgency": p.get("urgency"), "certainty": p.get("certainty"),
            "headline": p.get("headline"), "area": p.get("areaDesc"),
            "effective": p.get("effective"), "onset": p.get("onset"),
            "expires": p.get("expires"), "ends": p.get("ends"),
            "sender": p.get("senderName"), "instruction": (p.get("instruction") or None),
            "id": p.get("id"),
        })
    out.sort(key=lambda a: (SEVERITIES.index(a["severity"]) if a["severity"] in SEVERITIES else 9,
                            a["effective"] or ""))
    counts: dict[str, int] = {}
    for a in out:
        counts[a["severity"]] = counts.get(a["severity"], 0) + 1
    return {**where, "active_alerts": len(out), "by_severity": counts, "alerts": out[:limit],
            "checked_at": _now().isoformat(timespec="seconds"),
            "source": "U.S. National Weather Service (api.weather.gov), public domain"}


# ================================================================== A21 Erdbeben
def earthquakes(min_magnitude: float = 4.5, hours: int = 24, lat: float | None = None, lon: float | None = None,
                radius_km: float | None = None, limit: int = 50) -> dict:
    min_magnitude = float(min_magnitude)
    if not (0 <= min_magnitude <= 10):
        raise ValueError("min_magnitude must be 0..10")
    hours = int(hours)
    if not (1 <= hours <= 24 * 30):
        raise ValueError("hours must be 1..720 (30 days)")
    limit = max(1, min(int(limit), 200))
    start = (_now() - dt.timedelta(hours=hours)).replace(microsecond=0)
    params = {"starttime": start.strftime("%Y-%m-%dT%H:%M:%S"), "minmagnitude": min_magnitude,
              "orderby": "time", "limit": limit}
    center = None
    if lat is not None or lon is not None or radius_km is not None:
        center = _latlon(lat, lon)
        radius_km = float(radius_km or 500)
        if not (1 <= radius_km <= 20000):
            raise ValueError("radius_km must be 1..20000")
        params.update(latitude=center[0], longitude=center[1], maxradiuskm=radius_km)
    data = src.usgs_events(params)
    out = []
    for f in data.get("features", []):
        p, g = f.get("properties", {}), (f.get("geometry") or {}).get("coordinates") or [None, None, None]
        q = {
            "magnitude": p.get("mag"), "magnitude_type": p.get("magType"), "place": p.get("place"),
            "time_utc": dt.datetime.fromtimestamp(p["time"] / 1000, UTC).isoformat(timespec="seconds")
            if p.get("time") else None,
            "lat": g[1], "lon": g[0], "depth_km": g[2],
            "tsunami_flag": bool(p.get("tsunami")), "pager_alert": p.get("alert"),
            "felt_reports": p.get("felt"), "type": p.get("type"), "url": p.get("url"), "id": f.get("id"),
        }
        if center and g[0] is not None:
            q["distance_km"] = round(km_between(center, (g[1], g[0])), 1)
        out.append(q)
    strongest = max(out, key=lambda q: q["magnitude"] or 0) if out else None
    res = {"min_magnitude": min_magnitude, "hours": hours, "since_utc": start.isoformat(),
           "count": len(out), "strongest": strongest, "tsunami_flags": sum(q["tsunami_flag"] for q in out),
           "events": out, "source": "U.S. Geological Survey Earthquake Hazards Program, public domain"}
    if center:
        res.update(center={"lat": center[0], "lon": center[1]}, radius_km=radius_km)
    return res


# ================================================================== A22 Rückrufe
KINDS = {"food": "food", "drug": "drug", "device": "device", "medication": "drug", "medical-device": "device"}


def _fda_date(s: str | None) -> str | None:
    if s and re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _ymd(s: str, name: str) -> str:
    try:
        return dt.date.fromisoformat(s).strftime("%Y%m%d")
    except ValueError:
        raise ValueError(f"{name} must be YYYY-MM-DD")


def _term(s: str) -> str:
    s = re.sub(r'["\\\[\]():^~*?]', " ", s).strip()
    if not s:
        raise ValueError("search text is empty")
    return '"' + s + '"'


def recalls(kind: str = "food", firm: str | None = None, product: str | None = None, classification: str | None = None,
            since: str | None = None, until: str | None = None, state: str | None = None,
            ongoing_only: bool = False, limit: int = 20) -> dict:
    k = KINDS.get((kind or "").lower().strip())
    if not k:
        raise ValueError("kind must be food, drug or device")
    limit = max(1, min(int(limit), 100))
    terms = []
    if firm:
        terms.append("recalling_firm:" + _term(firm))
    if product:
        terms.append("product_description:" + _term(product))
    if classification:
        c = classification.upper().replace("CLASS", "").strip()
        if c not in ("I", "II", "III", "1", "2", "3"):
            raise ValueError("classification must be I, II or III")
        c = {"1": "I", "2": "II", "3": "III"}.get(c, c)
        terms.append(f'classification:"Class {c}"')
    if state:
        if state.upper() not in US_STATES:
            raise ValueError("state must be a two-letter US state code")
        terms.append(f'state:"{state.upper()}"')
    if ongoing_only:
        terms.append('status:"Ongoing"')
    d_until = _ymd(until, "until") if until else _now().strftime("%Y%m%d")
    d_since = _ymd(since, "since") if since else (_now() - dt.timedelta(days=90)).strftime("%Y%m%d")
    if d_since > d_until:
        raise ValueError("since must be before until")
    terms.append(f"report_date:[{d_since}+TO+{d_until}]")
    qs = "search=" + "+AND+".join(quote(t, safe='+:[]"') for t in terms) + f"&sort=report_date:desc&limit={limit}"
    data = src.fda_recalls(k, qs)
    out = [{
        "recall_number": r.get("recall_number"), "classification": r.get("classification"),
        "status": r.get("status"), "firm": r.get("recalling_firm"),
        "firm_location": ", ".join(x for x in (r.get("city"), r.get("state"), r.get("country")) if x),
        "product": r.get("product_description"), "quantity": r.get("product_quantity"),
        "reason": r.get("reason_for_recall"), "distribution": r.get("distribution_pattern"),
        "voluntary_mandated": r.get("voluntary_mandated"),
        "recall_initiated": _fda_date(r.get("recall_initiation_date")),
        "classified": _fda_date(r.get("center_classification_date")),
        "reported": _fda_date(r.get("report_date")), "code_info": r.get("code_info"),
    } for r in data.get("results", [])]
    total = ((data.get("meta") or {}).get("results") or {}).get("total", len(out))
    return {"kind": k, "from": _fda_date(d_since), "to": _fda_date(d_until), "total_matches": total,
            "returned": len(out), "recalls": out,
            "classes": {"Class I": "reasonable probability of serious health consequences or death",
                        "Class II": "temporary or medically reversible health consequences",
                        "Class III": "unlikely to cause adverse health consequences"},
            "source": "openFDA Enforcement Reports (U.S. FDA). Not for medical decisions; data is not validated "
                      "by FDA for this use."}
