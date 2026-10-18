"""Core schemas. Keep these stable: every booking source and the agent depend on them."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Topic = Literal["baggage", "cancellation", "web_checkin"]
BookingSourceName = Literal["mock_testair", "eticket", "live_indigo"]


class Flight(BaseModel):
    flight_number: str
    origin: str  # IATA code
    destination: str  # IATA code
    departure: datetime
    is_international: bool


class Passenger(BaseModel):
    type: Literal["adult", "child", "infant"]
    extra_baggage_kg: int = 0


class Booking(BaseModel):
    pnr_masked: str  # e.g. "AB****" — never the raw PNR
    airline: str
    booking_created_at: datetime  # needed for the 48-hour look-in rule
    status: Literal["confirmed", "cancelled"]
    fare_type: str  # raw value from source
    fare_category: str  # normalized via config/fare_mapping.yaml
    cabin: str
    flights: list[Flight]
    passengers: list[Passenger]
    booked_via: Literal["airline_website", "agent", "unknown"]
    source: BookingSourceName


class PolicySnapshot(BaseModel):
    snapshot_id: str
    airline: str
    topic: Topic
    url: str
    fetched_at: datetime
    content_hash: str
    text: str
    from_cache: bool


class AgentAnswer(BaseModel):
    answer: str
    evidence_quotes: list[str]  # exact text from the snapshot that supports the answer
    source_urls: list[str]
    checked_at: datetime
    booking_source: str
    policy_from_cache: bool
    confidence: Literal["high", "medium", "low"]
    could_not_confirm: bool
    # Not part of the original contract but useful for eval/UI; safe defaults.
    snapshot_ids: list[str] = Field(default_factory=list)
    dropped_quotes: list[str] = Field(default_factory=list)
