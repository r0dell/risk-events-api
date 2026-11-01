"""Risk Events API mit x402-Bezahlung (USDC auf Base).

Umgebungsvariablen:
  PAY_TO              Wallet-Adresse des Bündels (0x...), hierhin gehen die Zahlungen. Pflicht.
  X402_NETWORK        "testnet" (Base Sepolia, Standard) oder "mainnet" (Base).
  CDP_API_KEY_ID      Coinbase-Developer-Platform-Key. Für mainnet und Bazaar-Listing nötig.
  CDP_API_KEY_SECRET  dazugehöriges Secret.

Start:  uvicorn paid_app:app --host 0.0.0.0 --port 8000
Gratis:  /v1/sources, /health, /docs
Bezahlt: /v1/weather-alerts (A20), /v1/earthquakes (A21), /v1/recalls (A22)
Jeder Agent hat einen eigenen Preis; daran erkennt der Einnahmen-Monitor, wer verkauft hat.
"""
import os
import re

from x402 import x402ResourceServer
from x402.extensions.bazaar import (
    OutputConfig,
    bazaar_resource_server_extension,
    declare_discovery_extension,
)
from x402.http import FacilitatorConfig, HTTPFacilitatorClient
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import PaymentOption, RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme

from app import app

NETWORKS = {"testnet": "eip155:84532", "mainnet": "eip155:8453"}
TESTNET_FACILITATOR = os.environ.get("FACILITATOR_URL", "https://x402.org/facilitator")

PAY_TO = os.environ.get("PAY_TO", "").strip()
if not re.fullmatch(r"0x[0-9a-fA-F]{40}", PAY_TO):
    raise SystemExit("PAY_TO fehlt oder ist keine gültige Wallet-Adresse (0x + 40 Hex-Zeichen).")

MODE = os.environ.get("X402_NETWORK", "testnet").lower()
if MODE not in NETWORKS:
    raise SystemExit("X402_NETWORK muss 'testnet' oder 'mainnet' sein.")
NETWORK = NETWORKS[MODE]

CDP_ID = os.environ.get("CDP_API_KEY_ID", "").strip().strip('"\'').strip()
CDP_SECRET = os.environ.get("CDP_API_KEY_SECRET", "").strip().strip('"\'').strip()
if "BEGIN" in CDP_SECRET:
    CDP_SECRET = CDP_SECRET.replace("\\n", "\n")  # PEM aus einzeiligem Feld


def _describe_cdp_keys() -> str:
    """Format der Keys beschreiben, ohne Werte preiszugeben."""
    id_kind = ("uuid" if re.fullmatch(r"[0-9a-fA-F-]{36}", CDP_ID)
               else "organizations-path" if CDP_ID.startswith("organizations/") else "other")
    if "BEGIN EC PRIVATE KEY" in CDP_SECRET:
        sec_kind = "ec-pem"
    else:
        try:
            import base64
            sec_kind = f"base64-{len(base64.b64decode(CDP_SECRET, validate=True))}bytes"
        except Exception:
            sec_kind = "unparseable"
    return f"CDP key: id={id_kind} (len {len(CDP_ID)}), secret={sec_kind} (len {len(CDP_SECRET)})"


if CDP_ID and CDP_SECRET:
    from cdp.x402 import create_facilitator_config
    print(_describe_cdp_keys(), flush=True)
    facilitator_cfg = create_facilitator_config(CDP_ID, CDP_SECRET)
elif MODE == "mainnet":
    raise SystemExit("Für mainnet werden CDP_API_KEY_ID und CDP_API_KEY_SECRET benötigt.")
else:
    facilitator_cfg = FacilitatorConfig(url=TESTNET_FACILITATOR)

server = x402ResourceServer(HTTPFacilitatorClient(facilitator_cfg))
server.register(NETWORK, ExactEvmServerScheme())
server.register_extension(bazaar_resource_server_extension)

SVC = "Risk Events API"


def pay(price: str) -> PaymentOption:
    return PaymentOption(scheme="exact", pay_to=PAY_TO, price=price, network=NETWORK)


def route(price, desc, tags, inp, schema, required, example):
    return RouteConfig(
        accepts=pay(price), description=desc, mime_type="application/json", service_name=SVC, tags=tags,
        extensions=declare_discovery_extension(
            input=inp, input_schema={"properties": schema, "required": required},
            output=OutputConfig(example=example)))


S = lambda d: {"type": "string", "description": d}

# Preise (je Agent verschieden, für die Zuordnung im Monitor)
A20, A21, A22 = "$0.0385", "$0.0405", "$0.0425"
A29 = "$0.0445"  # Sanktionslisten, neu 1.11.2026
routes = {
    "GET /v1/weather-alerts": route(
        A20, "Returns active US severe-weather alerts (National Weather Service) for a state or a lat/lon point: "
             "event (hurricane, tornado, flood, heat, winter storm...), severity, urgency, area, effective and "
             "expiry times, instructions. Sorted by severity.",
        ["weather", "alerts", "risk", "usa", "logistics"], {"state": "FL", "min_severity": "Severe"},
        {"state": S("Two-letter US state code, e.g. TX"), "lat": S("Latitude (alternative to state)"),
         "lon": S("Longitude"), "min_severity": S("Extreme, Severe, Moderate or Minor"),
         "event": S("Filter by event name, e.g. Hurricane, Tornado, Flood"), "limit": S("Max alerts (1-100)")},
        [], {"state": "FL", "active_alerts": 2, "by_severity": {"Severe": 2},
             "alerts": [{"event": "Hurricane Warning", "severity": "Severe", "area": "Coastal Miami-Dade",
                         "expires": "2026-09-14T18:00:00-04:00"}]}),
    "GET /v1/earthquakes": route(
        A21, "Returns earthquakes worldwide (USGS) above a magnitude within the last hours, optionally within a "
             "radius of a point (port, plant, city) with distance, depth, tsunami flag and PAGER alert level.",
        ["earthquake", "natural-hazards", "risk", "supply-chain", "geo"],
        {"min_magnitude": "5", "hours": "48", "lat": "35.68", "lon": "139.69", "radius_km": "500"},
        {"min_magnitude": S("Minimum magnitude, default 4.5"), "hours": S("Look-back window in hours (1-720)"),
         "lat": S("Center latitude (optional)"), "lon": S("Center longitude (optional)"),
         "radius_km": S("Radius around the center in km (default 500)"), "limit": S("Max events (1-200)")},
        [], {"count": 1, "strongest": {"magnitude": 6.0, "place": "58 km SSE of Shima, Japan",
                                       "depth_km": 352.4, "tsunami_flag": False, "distance_km": 311.2}}),
    "GET /v1/recalls": route(
        A22, "Returns US FDA recalls (enforcement reports) for food, drugs or medical devices, filtered by firm, "
             "product text, class (I, II, III), US state, status and report date range.",
        ["recalls", "fda", "food-safety", "compliance", "usa"], {"kind": "food", "product": "peanut"},
        {"kind": S("food, drug or device"), "firm": S("Recalling firm name"), "product": S("Text in product "
         "description"), "classification": S("I, II or III"), "since": S("YYYY-MM-DD, default 90 days ago"),
         "until": S("YYYY-MM-DD, default today"), "state": S("US state of the firm"),
         "ongoing_only": S("true = only ongoing recalls"), "limit": S("Max results (1-100)")},
        [], {"kind": "food", "total_matches": 3, "recalls": [{"classification": "Class I", "status": "Ongoing",
             "firm": "Example Foods LLC", "reason": "Undeclared peanut", "reported": "2026-10-07"}]}),
    "GET /v1/sanctions/check": route(
        A29, "Screens a person, company, vessel or aircraft name against the official sanctions lists of the US "
             "(OFAC SDN), the UN Security Council, the EU (consolidated financial sanctions) and Switzerland (SECO) "
             "with fuzzy matching on names and aliases (accents, word order, transliterations). Returns score, list, "
             "list ID, type and sanctions programme for every hit. For KYC/KYB, payments, procurement and "
             "compliance agents. Screening hint only, not legal advice.",
        ["compliance", "sanctions", "kyc", "aml", "ofac", "screening"], {"name": "Vladimir Putin"},
        {"name": S("Person or company name"), "min_score": S("Similarity threshold 50-100, default 85"),
         "type": S("Optional: individual, entity, vessel, aircraft"),
         "lists": S("Optional comma list: OFAC, UN, EU, SECO (default all)"), "limit": S("Max results, default 10")},
        ["name"], {"query": "Vladimir Putin", "status": "potential_match", "matches": 3,
                   "results": [{"score": 100.0, "matched_name": "Vladimir Putin", "list": "SECO", "type": "individual",
                                "programs": ["Ukraine"]}]}),
}

app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
app.title = f"Risk Events API ({MODE})"
