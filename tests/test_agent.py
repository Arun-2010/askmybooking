"""Agent loop + evidence rules, with a scripted fake Claude client (no API calls)."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from airline_agent.agent.evidence import quote_in_text, validate_evidence
from airline_agent.agent.run import run_agent
from airline_agent.booking.base import BookingNotFound, map_testair_json
from mock_site.app import materialise

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


class FakeSource:
    name = "mock_testair"

    def __init__(self, booking=None, error=None):
        self.booking, self.error, self.calls = booking, error, []

    def get_booking(self, pnr, last_name):
        self.calls.append((pnr, last_name))
        if self.error:
            raise self.error
        return self.booking


class PinnedFetcher:
    def __init__(self, snaps):
        self.snaps = snaps

    def fetch_policy(self, airline, topic):
        return [s for s in self.snaps.values() if s.topic == topic]


class FakeClient:
    """Plays back a list of responses; records the requests it received."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(json.loads(json.dumps(kw, default=lambda o: getattr(o, "__dict__", str(o)))))
        return self.responses.pop(0)


def tool_use(name, args, id_="t1"):
    return NS(stop_reason="tool_use", content=[NS(type="tool_use", name=name, input=args, id=id_)], usage=None)


def final(obj):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps(obj))], usage=None)


@pytest.fixture
def booking(seed):
    b = next(x for x in seed if x["fare_code"] == "TA-SAVE" and "domestic" in x["tags"] and len(x["passengers"]) == 1)
    return b, map_testair_json(materialise(b, NOW))


def test_happy_path_drops_fake_quote(booking, pinned_snapshots):
    raw, bk = booking
    bag = next(s for s in pinned_snapshots.values() if s.topic == "baggage")
    client = FakeClient([
        tool_use("get_booking", {}),
        tool_use("fetch_policy", {"topic": "baggage"}, "t2"),
        final({
            "answer": "You can check in 15 kg.",
            "evidence": [
                {"snapshot_id": bag.snapshot_id, "quote": "15kg allowance per person effective Oct 1st, 2020."},
                {"snapshot_id": bag.snapshot_id, "quote": "You may check in 40 kg for free."},
            ],
            "confidence": "high", "could_not_confirm": False, "topics_used": ["baggage"],
        }),
    ])
    src = FakeSource(bk)
    res = run_agent(f"PNR {raw['pnr']} last name {raw['last_name']}: baggage?", raw["pnr"], raw["last_name"],
                    src, PinnedFetcher(pinned_snapshots), client=client, now=NOW)
    a = res.answer
    assert a.evidence_quotes == ["15kg allowance per person effective Oct 1st, 2020."]
    assert a.dropped_quotes == ["You may check in 40 kg for free."]
    assert a.confidence == "medium"  # lowered because a quote was dropped
    assert not a.could_not_confirm
    assert a.source_urls == [bag.url]
    assert a.booking_source == "mock_testair" and a.policy_from_cache is False
    assert "Booking read from" in a.answer
    assert res.topics_fetched == ["baggage"]
    # privacy: the raw PNR and last name never reach the model
    sent = json.dumps(client.requests)
    assert raw["pnr"] not in sent and raw["last_name"] not in sent
    assert src.calls == [(raw["pnr"], raw["last_name"])]


def test_no_verified_quotes_forces_could_not_confirm(booking, pinned_snapshots):
    raw, bk = booking
    client = FakeClient([
        tool_use("get_booking", {}),
        tool_use("fetch_policy", {"topic": "baggage"}, "t2"),
        final({"answer": "20 kg.", "evidence": [{"snapshot_id": "x", "quote": "invented sentence here"}],
               "confidence": "high", "could_not_confirm": False, "topics_used": ["baggage"]}),
    ])
    a = run_agent("baggage?", raw["pnr"], raw["last_name"], FakeSource(bk), PinnedFetcher(pinned_snapshots),
                  client=client, now=NOW).answer
    assert a.could_not_confirm and a.confidence == "low" and a.evidence_quotes == []
    assert "goindigo.in" in a.answer


def test_booking_not_found_is_reported_to_model(pinned_snapshots):
    client = FakeClient([
        tool_use("get_booking", {}),
        final({"answer": "I couldn't find that booking.", "evidence": [], "confidence": "low",
               "could_not_confirm": True, "topics_used": []}),
    ])
    res = run_agent("baggage?", "TZZZZ9", "Nobody", FakeSource(error=BookingNotFound("no match")),
                    PinnedFetcher(pinned_snapshots), client=client, now=NOW)
    assert res.booking_error == "not_found" and res.answer.could_not_confirm
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] and "BOOKING_NOT_FOUND" in tool_result["content"]


def test_refusal_is_handled(booking, pinned_snapshots):
    raw, bk = booking
    client = FakeClient([NS(stop_reason="refusal", content=[], usage=None)])
    a = run_agent("q", raw["pnr"], raw["last_name"], FakeSource(bk), PinnedFetcher(pinned_snapshots),
                  client=client, now=NOW).answer
    assert a.could_not_confirm and a.confidence == "low"


def test_request_shape(booking, pinned_snapshots):
    raw, bk = booking
    client = FakeClient([final({"answer": "x", "evidence": [], "confidence": "low", "could_not_confirm": True,
                                "topics_used": []})])
    run_agent("q", raw["pnr"], raw["last_name"], FakeSource(bk), PinnedFetcher(pinned_snapshots), client=client,
              now=NOW, model="claude-opus-5-5")
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in req and "tool_choice" not in req
    assert {t["name"] for t in req["tools"]} == {"get_booking", "fetch_policy"}
    assert all(t["strict"] for t in req["tools"])


def test_booking_facts_time_maths(booking):
    from airline_agent.agent.tools import booking_facts

    _, bk = booking
    facts = booking_facts(bk, NOW)
    assert facts["computed"]["hours_since_booking"] > 0
    assert facts["flights"][0]["hours_until_departure"] > 0


def test_quote_normalisation():
    text = "IndiGo’s website.  During this period, Customers may cancel"
    assert quote_in_text("IndiGo's website. During this period", text)
    assert not quote_in_text("IndiGo's site", text)
    assert not quote_in_text("short", text)


def test_quote_against_wrong_snapshot_still_grounded(pinned_snapshots):
    bag = next(s for s in pinned_snapshots.values() if s.topic == "baggage")
    other = next(s for s in pinned_snapshots.values() if s.topic != "baggage")
    ok, dropped = validate_evidence([{"snapshot_id": other.snapshot_id, "quote": "One stroller or baby pram per infant is allowed without any charge."}],
                                    pinned_snapshots)
    assert dropped == [] and ok[0][1].snapshot_id == bag.snapshot_id
