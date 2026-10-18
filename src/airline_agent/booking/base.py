"""BookingSource protocol, errors, and the TestAir JSON -> Booking mapping."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from airline_agent import config
from airline_agent.privacy import mask_pnr
from airline_agent.schemas import Booking, Flight, Passenger


class BookingError(Exception):
    """Base class. Messages must never contain a raw PNR or name."""


class BookingNotFound(BookingError):
    pass


class CaptchaRequired(BookingError):
    """A human verification step appeared and no human was available to complete it."""


class SourceUnavailable(BookingError):
    """Site down, HTTP 5xx, timeout, or page we could not understand."""


@runtime_checkable
class BookingSource(Protocol):
    name: str

    def get_booking(self, pnr: str, last_name: str) -> Booking: ...


PAX_TYPES = {"ADT": "adult", "CHD": "child", "INF": "infant"}
CHANNELS = {"WEB": "airline_website", "AGENCY": "agent"}


def map_testair_json(data: dict[str, Any], source: str = "mock_testair") -> Booking:
    """Map TestAir's API JSON to our Booking schema. Names are dropped here."""
    segments = [s for j in data["journeys"] for s in j["segments"]]
    fare_code = data["fare"]["code"]
    return Booking(
        pnr_masked=mask_pnr(data["bookingRef"]),
        airline="TestAir",
        booking_created_at=datetime.fromisoformat(data["createdAt"]),
        status="cancelled" if data["status"].upper() == "CANCELLED" else "confirmed",
        fare_type=fare_code,
        fare_category=config.normalize_fare("testair", fare_code),
        cabin=data["fare"]["cabin"],
        flights=[
            Flight(
                flight_number=s["flightNo"],
                origin=s["from"]["code"],
                destination=s["to"]["code"],
                departure=datetime.fromisoformat(s["departureLocal"]),
                is_international=bool(s["international"]),
            )
            for s in segments
        ],
        passengers=[
            Passenger(type=PAX_TYPES.get(p["paxType"], "adult"),
                      extra_baggage_kg=int(p.get("addOns", {}).get("extraBaggageKg", 0) or 0))
            for p in data["passengers"]
        ],
        booked_via=CHANNELS.get(data.get("channel", ""), "unknown"),
        source=source,  # type: ignore[arg-type]
    )
