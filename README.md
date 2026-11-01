# Risk Events API (v0.1)

Reale Risiko-Ereignisse für KI-Agenten, bezahlt pro Abfrage über x402 (USDC auf Base).

| Agent | Endpunkt | Liefert | Preis |
|---|---|---|---|
| A20 | `/v1/weather-alerts?state=TX` oder `?lat=..&lon=..` | Aktive US-Unwetterwarnungen (NWS), nach Schwere sortiert, Filter `min_severity`, `event` | 0.019 USDC |
| A21 | `/v1/earthquakes?min_magnitude=5&hours=48&lat=..&lon=..&radius_km=500` | Erdbeben weltweit (USGS), Entfernung zum Ort, Tsunami-Flag, PAGER-Stufe | 0.02 USDC |
| A22 | `/v1/recalls?kind=food&product=peanut&classification=I` | US-Rückrufe der FDA (Lebensmittel, Medikamente, Medizinprodukte) | 0.021 USDC |
| – | `/v1/sources`, `/docs`, `/health` | Quellenstatus, Doku | gratis |

Quellen (alle public domain): U.S. National Weather Service, U.S. Geological Survey, openFDA.
Fällt eine Quelle aus, antwortet die API mit 503 und es wird nichts abgebucht.

Start lokal: `PAY_TO=0x... uvicorn paid_app:app` · Tests: `python -m pytest -q`
