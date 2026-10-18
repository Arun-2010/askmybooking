"""Tools exposed to the LLM, and their executors.

The raw PNR and last name live only inside ``ToolContext``. The model calls
``get_booking`` with no arguments, so it never sees or repeats the PNR.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from airline_agent.booking.base import BookingError, BookingNotFound, BookingSource, CaptchaRequired
from airline_agent.policy.fetcher import PolicyFetcher, PolicyFetchError, topic_urls
from airline_agent.schemas import Booking, PolicySnapshot

log = logging.getLogger(__name__)

TOPICS = ["baggage", "cancellation", "web_checkin"]

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "get_booking",
        "description": (
            "Read the customer's booking from the booking source (the customer's credentials are already "
            "attached). Returns route, flights, fare type and normalized fare category, cabin, passengers "
            "with any extra baggage bought, booking channel, booking creation time, status, plus computed "
            "time facts (hours since booking, hours/days until each departure) relative to the current time. "
            "Call this once, before answering."
        ),
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "fetch_policy",
        "description": (
            "Fetch the airline's CURRENT official policy page(s) for one topic, live from the airline website "
            "(or the latest cached snapshot if the live fetch fails). Returns one or more snapshots, each with "
            "snapshot_id, url, fetched_at, from_cache and the full cleaned page text. Topics: 'baggage' "
            "(hand/check-in allowance, infants, excess), 'cancellation' (cancellation and change fees, "
            "48-hour look-in), 'web_checkin' (when online check-in opens/closes). Call once per topic needed."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string", "enum": TOPICS}},
            "required": ["topic"],
            "additionalProperties": False,
        },
    },
]


@dataclass
class ToolContext:
    source: BookingSource
    pnr: str
    last_name: str
    fetcher: PolicyFetcher
    policy_airline: str = "indigo"
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    on_step: Callable[[str, dict[str, Any]], None] | None = None
    # filled during the run
    booking: Booking | None = None
    booking_error: str | None = None
    snapshots: dict[str, PolicySnapshot] = field(default_factory=dict)
    topics_fetched: list[str] = field(default_factory=list)
    topics_failed: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def step(self, kind: str, **info: Any) -> None:
        if self.on_step:
            try:
                self.on_step(kind, info)
            except Exception:  # UI callbacks must never break the agent
                log.debug("on_step callback failed", exc_info=True)


def booking_facts(b: Booking, now: datetime) -> dict[str, Any]:
    """Booking JSON plus pre-computed time arithmetic (so the model doesn't do date maths)."""

    def hours(a: datetime, b_: datetime) -> float:
        return round((a - b_).total_seconds() / 3600, 1)

    created = b.booking_created_at
    flights = []
    for f in b.flights:
        h = hours(f.departure, now)
        flights.append({**json.loads(f.model_dump_json()), "hours_until_departure": h,
                        "days_until_departure": round(h / 24, 1)})
    first_dep = min((f.departure for f in b.flights), default=None)
    data = json.loads(b.model_dump_json())
    data["flights"] = flights
    data["computed"] = {
        "now_utc": now.isoformat(timespec="minutes"),
        "hours_since_booking": hours(now, created),
        "hours_between_booking_and_first_departure": hours(first_dep, created) if first_dep else None,
        "days_between_booking_and_first_departure": round(hours(first_dep, created) / 24, 1) if first_dep else None,
        "any_international_flight": any(f.is_international for f in b.flights),
        "passenger_counts": {t: sum(1 for p in b.passengers if p.type == t) for t in ("adult", "child", "infant")},
    }
    return data


def execute_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Run one tool. Returns (content_for_model, is_error)."""
    ctx.tool_calls.append({"name": name, "args": args})
    if name == "get_booking":
        return _get_booking(ctx)
    if name == "fetch_policy":
        topic = args.get("topic")
        if topic not in TOPICS:
            return f"Unknown topic {topic!r}. Use one of {TOPICS}.", True
        return _fetch_policy(ctx, topic)
    return f"Unknown tool {name!r}", True


def _get_booking(ctx: ToolContext) -> tuple[str, bool]:
    ctx.step("booking_start", source=ctx.source.name)
    if ctx.booking is None:
        try:
            ctx.booking = ctx.source.get_booking(ctx.pnr, ctx.last_name)
        except BookingNotFound as e:
            ctx.booking_error = "not_found"
            ctx.step("booking_error", error=str(e))
            return f"BOOKING_NOT_FOUND: {e}", True
        except CaptchaRequired as e:
            ctx.booking_error = "captcha"
            ctx.step("booking_error", error=str(e))
            return f"HUMAN_VERIFICATION_REQUIRED: {e}", True
        except BookingError as e:
            ctx.booking_error = "unavailable"
            ctx.step("booking_error", error=str(e))
            return f"BOOKING_SOURCE_UNAVAILABLE: {e}", True
    ctx.step("booking_ok", booking=ctx.booking)
    return json.dumps(booking_facts(ctx.booking, ctx.now), indent=1), False


def _fetch_policy(ctx: ToolContext, topic: str) -> tuple[str, bool]:
    ctx.step("policy_start", topic=topic)
    try:
        snaps = ctx.fetcher.fetch_policy(ctx.policy_airline, topic)  # type: ignore[arg-type]
    except PolicyFetchError as e:
        ctx.step("policy_error", topic=topic, error=str(e))
        if topic not in ctx.topics_failed:
            ctx.topics_failed.append(topic)
        try:
            official = ", ".join(topic_urls(ctx.policy_airline, topic))
        except Exception:
            official = "(not configured)"
        return (f"POLICY_UNAVAILABLE: could not fetch live or cached policy for {topic}. "
                f"Official page(s): {official}"), True
    for s in snaps:
        ctx.snapshots[s.snapshot_id] = s
    if topic not in ctx.topics_fetched:
        ctx.topics_fetched.append(topic)
    ctx.step("policy_ok", topic=topic, snapshots=snaps)
    payload = [
        {"snapshot_id": s.snapshot_id, "url": s.url, "fetched_at": s.fetched_at.isoformat(timespec="minutes"),
         "from_cache": s.from_cache, "text": s.text}
        for s in snaps
    ]
    return json.dumps(payload, ensure_ascii=False), False
