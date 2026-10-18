"""TestAir — a FICTIONAL airline website used as the evaluation bed.

Manage Booking flow:  GET /manage  ->  POST /manage  ->  booking page whose JS
calls  POST /api/v1/bookings/lookup  (JSON) and renders the result.

Failure switches (query param on /manage, carried through to the API call, or
globally via the TESTAIR_FAILURE env var, e.g. "captcha=1&slow=3"):
    captcha=1   show a human verification step before the form
    layout=v2   alternative HTML markup (different ids/classes)
    slow=N      API responds after N seconds
    error=500   API returns HTTP 500

Run:  uvicorn mock_site.app:app --reload --port 8001
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
app = FastAPI(title="TestAir (fictional)", docs_url=None, redoc_url=None)

AIRPORT_TZ = {
    **{c: 5.5 for c in ["DEL", "BOM", "BLR", "HYD", "MAA", "CCU", "GOI", "PNQ", "AMD", "COK", "JAI", "LKO"]},
    "DXB": 4, "SIN": 8, "BKK": 7, "DOH": 3, "KTM": 5.75, "LHR": 0, "IST": 3, "MCT": 4,
}
INDIA = {c for c, off in AIRPORT_TZ.items() if off == 5.5}
CITY = {
    "DEL": "New Delhi", "BOM": "Mumbai", "BLR": "Bengaluru", "HYD": "Hyderabad", "MAA": "Chennai",
    "CCU": "Kolkata", "GOI": "Goa", "PNQ": "Pune", "AMD": "Ahmedabad", "COK": "Kochi", "JAI": "Jaipur",
    "LKO": "Lucknow", "DXB": "Dubai", "SIN": "Singapore", "BKK": "Bangkok", "DOH": "Doha",
    "KTM": "Kathmandu", "LHR": "London", "IST": "Istanbul", "MCT": "Muscat",
}
FARE_NAMES = {"TA-LITE": "CabinOnly", "TA-SAVE": "SaveSmart", "TA-FLEX": "FlexFree",
              "TA-PLUS": "ComfortPlus", "TA-BIZ": "TestAir Business"}
PAX_CODES = {"adult": "ADT", "child": "CHD", "infant": "INF"}


def load_seed() -> list[dict]:
    data = json.loads((HERE / "seed_bookings.json").read_text(encoding="utf-8"))
    return data["bookings"]


SEED = load_seed()
INDEX = {(b["pnr"].upper(), b["last_name"].lower()): b for b in SEED}


def reference_now() -> datetime:
    """'Now' for materialising relative times. TESTAIR_NOW pins it (ISO) for reproducible runs."""
    v = os.getenv("TESTAIR_NOW")
    return datetime.fromisoformat(v) if v else datetime.now(timezone.utc)


def materialise(b: dict, now: datetime | None = None) -> dict:
    """Seed record -> the JSON shape TestAir's own API returns (deliberately NOT our Booking schema)."""
    now = now or reference_now()
    created = (now - timedelta(hours=b["created_hours_ago"])).replace(microsecond=0)
    segments = []
    for s in b["segments"]:
        tz = timezone(timedelta(hours=AIRPORT_TZ[s["origin"]]))
        day = (now + timedelta(days=s["departs_in_days"])).astimezone(tz).date()
        hh, mm = map(int, s["dep_time"].split(":"))
        dep = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
        arr = dep + timedelta(minutes=s["duration_min"])
        segments.append({
            "flightNo": s["flight_number"],
            "from": {"code": s["origin"], "city": CITY[s["origin"]]},
            "to": {"code": s["destination"], "city": CITY[s["destination"]]},
            "departureLocal": dep.isoformat(),
            "arrivalLocal": arr.astimezone(timezone(timedelta(hours=AIRPORT_TZ[s["destination"]]))).isoformat(),
            "international": not (s["origin"] in INDIA and s["destination"] in INDIA),
        })
    return {
        "bookingRef": b["pnr"],
        "status": b["status"].upper(),
        "createdAt": created.isoformat(),
        "channel": "WEB" if b["booked_via"] == "website" else "AGENCY",
        "fare": {"code": b["fare_code"], "name": FARE_NAMES[b["fare_code"]], "cabin": b["cabin"]},
        "journeys": [{"segments": segments}],
        "passengers": [
            {"paxType": PAX_CODES[p["type"]], "firstName": p["first_name"], "lastName": p["last_name"],
             "addOns": {"extraBaggageKg": p["extra_baggage_kg"]}}
            for p in b["passengers"]
        ],
        "_disclaimer": "TestAir is a fictional airline. This is test data.",
    }


def switches(request: Request) -> dict[str, str]:
    env = dict(parse_qsl(os.getenv("TESTAIR_FAILURE", "")))
    env.update({k: v for k, v in request.query_params.items() if k in {"captcha", "layout", "slow", "error"}})
    return env


def human_verified(request: Request) -> bool:
    return request.cookies.get("ta_human") == "1"


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {})


@app.get("/manage", response_class=HTMLResponse)
def manage_form(request: Request):
    sw = switches(request)
    qs = urlencode(sw)
    if sw.get("captcha") == "1" and not human_verified(request):
        return templates.TemplateResponse(request, "captcha.html", {"qs": qs, "layout": sw.get("layout", "v1")})
    tpl = "manage_v2.html" if sw.get("layout") == "v2" else "manage.html"
    return templates.TemplateResponse(request, tpl, {"qs": qs, "error": None})


@app.post("/captcha/verify")
def captcha_verify(human: str = Form(""), qs: str = Form("")):
    # The checkbox must be ticked by a person in a headed browser. Automation in
    # this project never clicks it: it pauses and hands control to the human.
    target = "/manage" + (f"?{qs}" if qs else "")
    resp = RedirectResponse(target, status_code=303)
    if human == "yes":
        resp.set_cookie("ta_human", "1", max_age=3600)
    return resp


@app.post("/manage", response_class=HTMLResponse)
def manage_submit(request: Request, pnr: str = Form(...), last_name: str = Form(...), qs: str = Form("")):
    sw = dict(parse_qsl(qs))
    if sw.get("captcha") == "1" and not human_verified(request):
        return RedirectResponse("/manage?" + qs, status_code=303)
    tpl = "booking_v2.html" if sw.get("layout") == "v2" else "booking.html"
    return templates.TemplateResponse(
        request, tpl,
        {"lookup": json.dumps({"pnr": pnr.strip().upper(), "lastName": last_name.strip()}), "qs": qs},
    )


@app.post("/api/v1/bookings/lookup")
async def api_lookup(request: Request):
    sw = switches(request)
    if sw.get("slow"):
        await asyncio.sleep(float(sw["slow"]))
    if sw.get("error") == "500":
        return JSONResponse({"error": "internal_error", "message": "Something went wrong"}, status_code=500)
    if sw.get("captcha") == "1" and not human_verified(request):
        return JSONResponse({"error": "verification_required"}, status_code=403)
    body = await request.json()
    key = (str(body.get("pnr", "")).strip().upper(), str(body.get("lastName", "")).strip().lower())
    b = INDEX.get(key)
    if not b:
        return JSONResponse({"error": "not_found", "message": "No booking matches these details."}, status_code=404)
    return materialise(b)


@app.get("/healthz")
def healthz():
    return {"ok": True, "bookings": len(SEED)}
