"""Code-enforced applicability checks: does the fetched page actually cover this booking?

Catches the overconfident case where the AI applies a general rule to a booking the
page never mentions (a special fare, an unlisted destination). Rules live in
config/applicability.yaml.
"""

from __future__ import annotations

import re

from airline_agent import config
from airline_agent.schemas import Booking, PolicySnapshot


# A fee amount in an answer: "INR 8,999", "₹3,999", "$130", "USD 130".
_FEE_RE = re.compile(r"(?:INR|USD|Rs\.?|₹|\$)\s?\d[\d,]*", re.I)


def unconfirmed_reasons(booking: Booking, snapshots: list[PolicySnapshot], airline: str = "indigo",
                        answer: str = "") -> list[str]:
    """Plain-language reasons the fetched pages don't cover this booking (empty = no objection)."""
    rules = config.load_yaml("applicability.yaml").get(airline, {})
    reasons: list[str] = []
    if booking.status == "cancelled":
        return reasons

    canc = rules.get("cancellation", {})
    if (canc.get("fee_tables_codeshare_only") and any(s.topic == "cancellation" for s in snapshots)
            and _FEE_RE.search(answer)):
        reasons.append("the fee tables on the airline's fees page are for codeshare bookings, so the fee for "
                       "your booking isn't stated there")

    baggage_text = " ".join(s.text for s in snapshots if s.topic == "baggage").lower()
    if not baggage_text:
        return reasons

    bag = rules.get("baggage", {})
    fare = booking.fare_category
    if fare in bag.get("fares_needing_explicit_rule", []) and fare.lower() not in baggage_text:
        reasons.append(f"the airline's baggage page doesn't mention the {fare} fare, so the general allowance "
                       f"may not apply to your ticket")

    home = set(rules.get("home_airports", []))
    names = bag.get("destination_names", {})
    for f in booking.flights:
        if not f.is_international:
            continue
        abroad = f.origin if f.destination in home else f.destination
        known = names.get(abroad)
        if not known:
            reasons.append(f"I can't check whether {abroad} is covered by the baggage page")
        elif not any(n.lower() in baggage_text for n in known):
            reasons.append(f"the airline's baggage page doesn't list {known[0]} ({abroad}), so the allowance "
                           f"for this route can't be confirmed")
    return list(dict.fromkeys(reasons))
