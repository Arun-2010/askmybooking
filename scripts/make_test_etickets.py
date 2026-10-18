"""Generate TEST e-ticket PDFs from the TestAir seed data.

Every PDF is clearly labelled as a fictional test document. The relative seed
times are materialised at generation time; a manifest records that "now" so the
extraction eval can compare like with like.

    python scripts/make_test_etickets.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mock_site.app import SEED, materialise  # noqa: E402

OUT_DIR = ROOT / "tests" / "fixtures" / "etickets"
PAX_LABEL = {"ADT": "Adult", "CHD": "Child", "INF": "Infant"}


def render(data: dict, path: Path) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "TestAir  -  Electronic Ticket", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(180, 30, 30)
    pdf.cell(0, 6, "TEST DOCUMENT - FICTIONAL AIRLINE - NOT VALID FOR TRAVEL", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 11)
    lines = [
        f"Booking reference: {data['bookingRef']}",
        f"Status: {data['status'].title()}",
        f"Booked on: {data['createdAt']}",
        f"Booking channel: {'Website' if data['channel'] == 'WEB' else 'Travel agent'}",
        f"Fare: {data['fare']['code']} ({data['fare']['name']})",
        f"Cabin: {data['fare']['cabin']}",
    ]
    for ln in lines:
        pdf.cell(0, 7, ln, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Flights", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    for s in [s for j in data["journeys"] for s in j["segments"]]:
        kind = "International" if s["international"] else "Domestic"
        pdf.cell(0, 7, f"Flight {s['flightNo']} | {s['from']['code']} -> {s['to']['code']} | "
                       f"Departs {s['departureLocal']} | {kind}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Passengers", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    for i, p in enumerate(data["passengers"], 1):
        pdf.cell(0, 7, f"{i}. {p['firstName']} {p['lastName']} | {PAX_LABEL[p['paxType']]} | "
                       f"Extra baggage: {p['addOns']['extraBaggageKg']} kg", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 4, "Generated from TestAir seed data for automated testing of an AI agent. "
                         "TestAir does not exist. Any resemblance to a real booking is coincidental.")
    pdf.output(str(path))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    seeds = SEED[: args.limit] if args.limit else SEED
    manifest = {"generated_at": now.isoformat(), "tickets": []}
    for b in seeds:
        path = OUT_DIR / f"{b['id']}.pdf"
        render(materialise(b, now), path)
        manifest["tickets"].append({"id": b["id"], "file": path.name})
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {len(seeds)} test e-tickets to {OUT_DIR}")


if __name__ == "__main__":
    main()
