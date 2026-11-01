# Risk Events API (v0.1)

Reale Risiko-Ereignisse für KI-Agenten, bezahlt pro Abfrage über x402 (USDC auf Base).

| Agent | Endpunkt | Liefert | Preis |
|---|---|---|---|
| A20 | `/v1/weather-alerts?state=TX` oder `?lat=..&lon=..` | Aktive US-Unwetterwarnungen (NWS), nach Schwere sortiert, Filter `min_severity`, `event` | 0.0385 USDC |
| A21 | `/v1/earthquakes?min_magnitude=5&hours=48&lat=..&lon=..&radius_km=500` | Erdbeben weltweit (USGS), Entfernung zum Ort, Tsunami-Flag, PAGER-Stufe | 0.0405 USDC |
| A22 | `/v1/recalls?kind=food&product=peanut&classification=I` | US-Rückrufe der FDA (Lebensmittel, Medikamente, Medizinprodukte) | 0.0425 USDC |
| A29 | `/v1/sanctions/check?name=..&type=&lists=OFAC,EU` | Name/Firma gegen OFAC-SDN, UN, EU und SECO prüfen (unscharfer Abgleich inkl. Aliasse), mit Trefferwert und Programm | 0.0445 USDC |
| – | `/v1/sources`, `/docs`, `/health` | Quellenstatus, Doku | gratis |

Quellen (alle public domain bzw. amtlich frei): U.S. National Weather Service, U.S. Geological Survey, openFDA,
OFAC (US Treasury), UN-Sicherheitsrat, EU-Finanzsanktionsliste, SECO. Sanktionsabgleich = Hinweis, keine Rechtsberatung.
Fällt eine Quelle aus, antwortet die API mit 503 und es wird nichts abgebucht.

Start lokal: `PAY_TO=0x... uvicorn paid_app:app` · Tests: `python -m pytest -q`
