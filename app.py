"""HTTP-API: Risk Events API (US-Unwetterwarnungen, Erdbeben, US-Produktrückrufe)."""
from typing import Optional

from fastapi import FastAPI, HTTPException, Query

import engine as e
import sanctions
import sources as src

app = FastAPI(
    title="Risk Events API",
    version="0.1.0",
    description=("Real-world risk events for agents: active US severe-weather alerts (NWS) by state or point, "
                 "worldwide earthquakes (USGS) by magnitude, time window and distance, and US product recalls "
                 "(FDA food, drug, device enforcement reports), plus sanctions screening against the OFAC, UN, EU "
                 "and Swiss SECO lists. Public data, every response names its source."),
)


@app.on_event("startup")
def _preload_sanctions():
    import os
    import threading
    if os.environ.get("SANCTIONS_PRELOAD", "1") == "1":
        threading.Thread(target=sanctions.preload, daemon=True).start()


def _run(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except KeyError as ex:
        raise HTTPException(status_code=404, detail=str(ex).strip("'\""))
    except src.SourceUnavailable as ex:
        raise HTTPException(status_code=503, detail=str(ex))
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))


# ---------------------------------------------------------------- A20
@app.get("/v1/weather-alerts", summary="Active US weather alerts for a state or a point")
def weather_alerts(state: Optional[str] = Query(None, examples=["TX"]), lat: Optional[float] = None,
                   lon: Optional[float] = None,
                   min_severity: Optional[str] = Query(None, examples=["Severe"]),
                   event: Optional[str] = Query(None, examples=["Hurricane"]), limit: int = 25):
    return _run(e.weather_alerts, state, lat, lon, min_severity, event, limit)


# ---------------------------------------------------------------- A21
@app.get("/v1/earthquakes", summary="Earthquakes worldwide above a magnitude, optionally near a point")
def earthquakes(min_magnitude: float = Query(4.5, examples=[5]), hours: int = Query(24, examples=[48]),
                lat: Optional[float] = None, lon: Optional[float] = None, radius_km: Optional[float] = None,
                limit: int = 50):
    return _run(e.earthquakes, min_magnitude, hours, lat, lon, radius_km, limit)


# ---------------------------------------------------------------- A22
@app.get("/v1/recalls", summary="US FDA recalls (food, drug, device) by firm, product, class and date")
def recalls(kind: str = Query("food", examples=["food"]), firm: Optional[str] = None,
            product: Optional[str] = Query(None, examples=["peanut"]), classification: Optional[str] = None,
            since: Optional[str] = Query(None, examples=["2026-09-01"]), until: Optional[str] = None,
            state: Optional[str] = None, ongoing_only: bool = False, limit: int = 20):
    return _run(e.recalls, kind, firm, product, classification, since, until, state, ongoing_only, limit)


# ---------------------------------------------------------------- frei
@app.get("/v1/sanctions/check", summary="A29: Screen a person or company name against OFAC, UN, EU and SECO lists")
def sanctions_check(name: str = Query(..., examples=["Vladimir Putin"]), min_score: int = Query(85, ge=50, le=100),
                    type: Optional[str] = Query(None, description="individual, entity, vessel or aircraft"),
                    lists: Optional[str] = Query(None, examples=["OFAC,EU"]), limit: int = Query(10, ge=1, le=50)):
    return _run(sanctions.check, name, min_score, type, lists, limit)


@app.get("/v1/sources", summary="Data source status (free)")
def sources():
    return {"sources": {
        "nws": "https://api.weather.gov (U.S. National Weather Service)",
        "usgs": "https://earthquake.usgs.gov (U.S. Geological Survey)",
        "openfda": "https://open.fda.gov (U.S. Food and Drug Administration)",
        "sanctions": {k: v["name"] for k, v in sanctions.LISTS.items()}}, "status": src.STATUS}


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True}
