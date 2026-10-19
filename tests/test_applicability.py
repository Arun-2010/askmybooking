"""Code-enforced 'couldn't confirm' guards, against the real pinned IndiGo baggage page."""

from datetime import datetime, timezone

import pytest

from airline_agent.agent.applicability import unconfirmed_reasons
from airline_agent.schemas import Booking, Flight, Passenger

DEP = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


def booking(fare="Saver", legs=(("DEL", "BLR", False),), status="confirmed"):
    return Booking(
        pnr_masked="TA****", airline="TestAir", booking_created_at=DEP, status=status, fare_type="X",
        fare_category=fare, cabin="Economy",
        flights=[Flight(flight_number="TA 1", origin=o, destination=d, departure=DEP, is_international=i)
                 for o, d, i in legs],
        passengers=[Passenger(type="adult")], booked_via="airline_website", source="mock_testair",
    )


@pytest.fixture
def bag(pinned_snapshots):
    return [s for s in pinned_snapshots.values() if s.topic == "baggage"]


def test_standard_domestic_fare_passes(bag):
    assert unconfirmed_reasons(booking(), bag) == []


@pytest.mark.parametrize("fare", ["Lite", "Stretch", "Super 6E"])
def test_special_fare_not_on_page_is_flagged(bag, fare):
    reasons = unconfirmed_reasons(booking(fare=fare), bag)
    assert reasons and fare in reasons[0]


def test_listed_international_destination_passes(bag):
    assert unconfirmed_reasons(booking(legs=[("DEL", "DXB", True)]), bag) == []


def test_return_flight_checks_the_foreign_end(bag):
    assert unconfirmed_reasons(booking(legs=[("DXB", "DEL", True)]), bag) == []


def test_unlisted_destination_is_flagged(bag):
    reasons = unconfirmed_reasons(booking(legs=[("BLR", "BOM", False), ("BOM", "LHR", True)]), bag)
    assert len(reasons) == 1 and "London" in reasons[0]


@pytest.fixture
def canc(pinned_snapshots):
    return [s for s in pinned_snapshots.values() if s.topic == "cancellation"]


def test_codeshare_fee_quoted_is_flagged(canc):
    reasons = unconfirmed_reasons(booking(fare="Stretch"), canc, answer="The change fee is INR 8,999 ($130).")
    assert reasons and "codeshare" in reasons[0]


def test_lookin_answer_without_fee_amount_passes(canc):
    assert unconfirmed_reasons(booking(), canc, answer="Yes, you can cancel free within 48 hours.") == []


def test_no_baggage_page_or_cancelled_booking_skips(bag):
    assert unconfirmed_reasons(booking(fare="Lite"), []) == []
    assert unconfirmed_reasons(booking(fare="Lite", status="cancelled"), bag) == []
