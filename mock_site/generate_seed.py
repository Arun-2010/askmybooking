"""Generate mock_site/seed_bookings.json deterministically.

All bookings, names and PNRs are FICTIONAL (TestAir is not a real airline).
Times are stored *relative* (hours since booking, days until departure) and are
materialised by the mock site at request time, so cases such as "booked less
than 48 hours ago" stay true whenever the site is run.

    python mock_site/generate_seed.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).with_name("seed_bookings.json")
rng = random.Random(20261018)

DOMESTIC = ["DEL", "BOM", "BLR", "HYD", "MAA", "CCU", "GOI", "PNQ", "AMD", "COK", "JAI", "LKO"]
INTERNATIONAL = ["DXB", "SIN", "BKK", "DOH", "KTM", "LHR", "IST", "MCT"]
FIRST = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Ananya", "Vihaan", "Isha", "Arjun", "Sara",
         "Nikhil", "Tara", "Dev", "Kavya", "Yash", "Riya", "Neel", "Pooja", "Aditya", "Leela"]
LAST = ["Testwala", "Mockrao", "Sampleton", "Fakir", "Demoji", "Placeholder", "Dummyram", "Fixturekar",
        "Seedappa", "Stubbs", "Pretendra", "Notreal", "Examplay", "Trialpur"]
PNR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

used_pnrs: set[str] = set()


def pnr() -> str:
    while True:
        # TestAir PNRs always start with "T" and contain a digit — easy to spot as test data.
        p = "T" + "".join(rng.choice(PNR_ALPHABET) for _ in range(5))
        if any(c.isdigit() for c in p) and p not in used_pnrs:
            used_pnrs.add(p)
            return p


def flight_no() -> str:
    return f"TA {rng.randint(101, 989)}"


def seg(origin: str, dest: str, days: float, time: str | None = None) -> dict:
    return {
        "flight_number": flight_no(),
        "origin": origin,
        "destination": dest,
        "departs_in_days": days,
        "dep_time": time or f"{rng.randint(5, 22):02d}:{rng.choice(['00', '15', '30', '45'])}",
        "duration_min": rng.randint(70, 200) if dest in DOMESTIC and origin in DOMESTIC else rng.randint(180, 560),
    }


def pax(kind: str, last: str, extra: int = 0) -> dict:
    return {"type": kind, "first_name": rng.choice(FIRST), "last_name": last, "extra_baggage_kg": extra}


def booking(
    tags: list[str], fare: str = "TA-SAVE", cabin: str = "Economy", created_hours_ago: float = 240,
    segments: list[dict] | None = None, paxes: list[str] | None = None, extra: list[int] | None = None,
    status: str = "confirmed", via: str = "website", note: str = "",
) -> dict:
    last = rng.choice(LAST)
    paxes = paxes or ["adult"]
    extra = extra or [0] * len(paxes)
    if segments is None:
        o, d = rng.sample(DOMESTIC, 2)
        segments = [seg(o, d, rng.choice([5, 9, 14, 21, 30]))]
    return {
        "pnr": pnr(),
        "last_name": last,
        "status": status,
        "fare_code": fare,
        "cabin": cabin,
        "booked_via": via,  # website | travel_agent
        "created_hours_ago": created_hours_ago,
        "segments": segments,
        "passengers": [pax(k, last, e) for k, e in zip(paxes, extra)],
        "tags": tags,
        "note": note,
    }


def dom_pair() -> tuple[str, str]:
    a, b = rng.sample(DOMESTIC, 2)
    return a, b


def build() -> list[dict]:
    B: list[dict] = []
    # --- fare families, domestic one-adult baseline (10) ---
    for fare in ["TA-LITE", "TA-SAVE", "TA-SAVE", "TA-FLEX", "TA-FLEX", "TA-PLUS", "TA-PLUS"]:
        B.append(booking(["domestic", "fare:" + fare], fare=fare))
    B.append(booking(["domestic", "business", "fare:TA-BIZ"], fare="TA-BIZ", cabin="Business"))
    B.append(booking(["domestic", "business", "fare:TA-BIZ"], fare="TA-BIZ", cabin="Business"))
    B.append(booking(["domestic", "fare:TA-LITE"], fare="TA-LITE"))
    # --- extra baggage bought (5) ---
    for kg, fare in [(5, "TA-SAVE"), (10, "TA-SAVE"), (15, "TA-FLEX"), (20, "TA-SAVE"), (5, "TA-LITE")]:
        B.append(booking(["domestic", "extra_baggage"], fare=fare, extra=[kg]))
    # --- infants & children (5) ---
    B.append(booking(["domestic", "infant"], paxes=["adult", "infant"]))
    B.append(booking(["domestic", "infant", "family"], paxes=["adult", "adult", "infant"], fare="TA-FLEX"))
    B.append(booking(["domestic", "child"], paxes=["adult", "child"]))
    B.append(booking(["domestic", "child", "family", "extra_baggage"], paxes=["adult", "adult", "child", "child"],
                     extra=[10, 0, 0, 0]))
    B.append(booking(["international", "infant"], paxes=["adult", "infant"],
                     segments=[seg("DEL", "DXB", 20)]))
    # --- connecting flights (4) ---
    for _ in range(3):
        a, b = dom_pair()
        c = rng.choice([x for x in DOMESTIC if x not in (a, b)])
        d = rng.choice([5, 12, 25])
        B.append(booking(["domestic", "connecting"], segments=[seg(a, c, d, "06:30"), seg(c, b, d + 0.15, "11:10")]))
    B.append(booking(["international", "connecting"],
                     segments=[seg("BLR", "BOM", 18, "05:45"), seg("BOM", "LHR", 18.2, "10:20")]))
    # --- international (6) ---
    for dest, fare in [("DXB", "TA-SAVE"), ("SIN", "TA-SAVE"), ("BKK", "TA-FLEX"), ("DOH", "TA-SAVE"),
                       ("KTM", "TA-PLUS"), ("IST", "TA-BIZ")]:
        origin = rng.choice(["DEL", "BOM", "BLR", "HYD"])
        cabin = "Business" if fare == "TA-BIZ" else "Economy"
        B.append(booking(["international", "fare:" + fare], fare=fare, cabin=cabin,
                         segments=[seg(origin, dest, rng.choice([10, 16, 40]))]))
    # --- cancelled (2) ---
    B.append(booking(["domestic", "cancelled"], status="cancelled"))
    B.append(booking(["international", "cancelled"], status="cancelled", segments=[seg("BOM", "DXB", 12)]))
    # --- 48-hour look-in cases (8) ---
    # within 48h, website, departs well after 7 days (domestic) -> look-in applies
    B.append(booking(["domestic", "lookin:eligible"], created_hours_ago=20,
                     segments=[seg(*dom_pair(), 15)], note="booked 20h ago on website, departs in 15 days"))
    B.append(booking(["domestic", "lookin:eligible"], created_hours_ago=40, fare="TA-FLEX",
                     segments=[seg(*dom_pair(), 30)], note="booked 40h ago on website, departs in 30 days"))
    # within 48h but via agent -> look-in does not apply
    B.append(booking(["domestic", "lookin:agent", "booked_via_agent"], created_hours_ago=10, via="travel_agent",
                     segments=[seg(*dom_pair(), 20)], note="booked 10h ago via agent"))
    # within 48h but departs too soon (domestic < 7 days)
    B.append(booking(["domestic", "lookin:too_close"], created_hours_ago=5,
                     segments=[seg(*dom_pair(), 3)], note="booked 5h ago, departs in 3 days"))
    # just outside 48h
    B.append(booking(["domestic", "lookin:expired"], created_hours_ago=60,
                     segments=[seg(*dom_pair(), 20)], note="booked 60h ago"))
    # international within 48h, departs in 20 days (>= 15) -> eligible
    B.append(booking(["international", "lookin:eligible"], created_hours_ago=12,
                     segments=[seg("DEL", "SIN", 20)], note="intl, booked 12h ago, departs in 20 days"))
    # international within 48h, departs in 10 days (< 15) -> not eligible
    B.append(booking(["international", "lookin:too_close"], created_hours_ago=12,
                     segments=[seg("BOM", "BKK", 10)], note="intl, booked 12h ago, departs in 10 days"))
    # boundary: exactly 47h, domestic departs in 8 days
    B.append(booking(["domestic", "lookin:eligible", "boundary"], created_hours_ago=47,
                     segments=[seg(*dom_pair(), 8)], note="booked 47h ago, departs in 8 days"))
    # --- booked via agent, other (3) ---
    B.append(booking(["domestic", "booked_via_agent"], via="travel_agent"))
    B.append(booking(["international", "booked_via_agent"], via="travel_agent", segments=[seg("HYD", "MCT", 14)]))
    B.append(booking(["domestic", "booked_via_agent", "fare:TA-FLEX"], via="travel_agent", fare="TA-FLEX"))
    # --- web check-in timing cases (2) ---
    B.append(booking(["domestic", "checkin:open"], segments=[seg(*dom_pair(), 1)], note="departs in ~24h"))
    B.append(booking(["international", "checkin:soon"], segments=[seg("DEL", "DXB", 2)], note="intl departs in 2 days"))
    for i, b in enumerate(B, 1):
        b["id"] = f"TA{i:03d}"
    return B


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps({"_comment": "FICTIONAL TestAir bookings for testing only. Times are relative.",
                               "bookings": data}, indent=2), encoding="utf-8")
    print(f"wrote {len(data)} bookings to {OUT}")
