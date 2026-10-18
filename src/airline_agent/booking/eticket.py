"""Booking source: e-ticket PDF -> Booking (fallback when the website can't be used).

Parses the TestAir e-ticket layout. The PNR/last name passed to ``get_booking``
are checked against the document so a user can't read someone else's ticket by
accident.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pdfplumber

from airline_agent import config
from airline_agent.booking.base import BookingNotFound, SourceUnavailable
from airline_agent.privacy import mask_pnr
from airline_agent.schemas import Booking, Flight, Passenger

_FIELD = re.compile(r"^(Booking reference|Status|Booked on|Booking channel|Fare|Cabin):\s*(.+)$", re.M)
_FLIGHT = re.compile(
    r"^Flight\s+(?P<no>[A-Z0-9]{2}\s?\d{1,4})\s*\|\s*(?P<o>[A-Z]{3})\s*->\s*(?P<d>[A-Z]{3})\s*\|\s*"
    r"Departs\s+(?P<dep>\S+)\s*\|\s*(?P<kind>Domestic|International)", re.M)
_PAX = re.compile(r"^\d+\.\s+(?P<name>.+?)\s*\|\s*(?P<type>Adult|Child|Infant)\s*\|\s*Extra baggage:\s*(?P<kg>\d+)\s*kg",
                  re.M)


def extract_text(pdf_path: Path | str) -> str:
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as e:
        raise SourceUnavailable(f"Could not read e-ticket PDF: {type(e).__name__}") from None


def parse_eticket_text(text: str, expected_last_name: str | None = None) -> tuple[Booking, str]:
    """Return (Booking, raw_pnr). raw_pnr is only for the caller's match check."""
    fields = {k: v.strip() for k, v in _FIELD.findall(text)}
    missing = {"Booking reference", "Status", "Booked on", "Fare", "Cabin"} - fields.keys()
    if missing:
        raise SourceUnavailable(f"E-ticket layout not recognised (missing: {', '.join(sorted(missing))})")
    pax_rows = list(_PAX.finditer(text))
    if expected_last_name:
        names = [m.group("name").split()[-1].lower() for m in pax_rows]
        if expected_last_name.strip().lower() not in names:
            raise BookingNotFound("Last name does not match the uploaded e-ticket.")
    fare_code = fields["Fare"].split()[0]
    channel = fields.get("Booking channel", "").lower()
    booking = Booking(
        pnr_masked=mask_pnr(fields["Booking reference"]),
        airline="TestAir",
        booking_created_at=datetime.fromisoformat(fields["Booked on"]),
        status="cancelled" if fields["Status"].lower().startswith("cancel") else "confirmed",
        fare_type=fare_code,
        fare_category=config.normalize_fare("testair", fare_code),
        cabin=fields["Cabin"],
        flights=[
            Flight(flight_number=m.group("no"), origin=m.group("o"), destination=m.group("d"),
                   departure=datetime.fromisoformat(m.group("dep")), is_international=m.group("kind") == "International")
            for m in _FLIGHT.finditer(text)
        ],
        passengers=[Passenger(type=m.group("type").lower(), extra_baggage_kg=int(m.group("kg"))) for m in pax_rows],
        booked_via="airline_website" if "website" in channel else "agent" if "agent" in channel else "unknown",
        source="eticket",
    )
    if not booking.flights:
        raise SourceUnavailable("No flights found on the e-ticket.")
    return booking, fields["Booking reference"].upper()


class ETicketSource:
    name = "eticket"

    def __init__(self, pdf_path: Path | str):
        self.pdf_path = Path(pdf_path)

    def get_booking(self, pnr: str, last_name: str) -> Booking:
        text = extract_text(self.pdf_path)
        booking, raw_pnr = parse_eticket_text(text, expected_last_name=last_name or None)
        if pnr and raw_pnr != pnr.strip().upper():
            raise BookingNotFound("PNR does not match the uploaded e-ticket.")
        return booking
