"""Gemini loop with a fake client built from real google-genai response types (no API calls)."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from google.genai import errors, types

from airline_agent import config
from airline_agent.agent import gemini_loop
from airline_agent.agent.run import run_agent
from airline_agent.booking.base import map_testair_json
from mock_site.app import materialise
from test_agent import FakeSource, PinnedFetcher

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


def call(name, args, id_="c1"):
    fc = types.FunctionCall(name=name, args=args, id=id_)
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(function_call=fc)]), finish_reason="STOP")])


class FakeGemini:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.models = NS(generate_content=self._gen)

    def _gen(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": [c.model_dump(mode="json") for c in contents],
                              "config": config})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def booking(seed):
    b = next(x for x in seed if x["fare_code"] == "TA-SAVE" and "domestic" in x["tags"] and len(x["passengers"]) == 1)
    return b, map_testair_json(materialise(b, NOW))


def test_gemini_full_loop(booking, pinned_snapshots):
    raw, bk = booking
    bag = next(s for s in pinned_snapshots.values() if s.topic == "baggage")
    client = FakeGemini([
        call("get_booking", {}),
        call("fetch_policy", {"topic": "baggage"}, "c2"),
        call("submit_answer", {
            "answer": "15 kg.", "confidence": "high", "could_not_confirm": False, "topics_used": ["baggage"],
            "evidence": [{"snapshot_id": bag.snapshot_id, "quote": "15kg allowance per person effective Oct 1st, 2020."},
                         {"snapshot_id": bag.snapshot_id, "quote": "made-up sentence not on page"}],
        }, "c3"),
    ])
    res = run_agent("baggage?", raw["pnr"], raw["last_name"], FakeSource(bk), PinnedFetcher(pinned_snapshots),
                    client=client, provider="gemini", model="gemini-test", now=NOW)
    a = res.answer
    assert a.evidence_quotes == ["15kg allowance per person effective Oct 1st, 2020."]
    assert a.dropped_quotes == ["made-up sentence not on page"] and a.confidence == "medium"
    assert res.topics_fetched == ["baggage"]
    # tool results are sent back as function responses with matching ids
    last = client.requests[-1]["contents"][-1]
    assert last["parts"][0]["function_response"]["id"] == "c2"
    # privacy: raw PNR / last name never sent
    sent = json.dumps([r["contents"] for r in client.requests])
    assert raw["pnr"] not in sent and raw["last_name"] not in sent
    cfg = client.requests[0]["config"]
    names = [d.name for d in cfg.tools[0].function_declarations]
    assert names == ["get_booking", "fetch_policy", "submit_answer"]
    assert cfg.tool_config.function_calling_config.mode == "ANY"


def test_gemini_rate_limit_retries(booking, pinned_snapshots, monkeypatch):
    monkeypatch.setattr(gemini_loop, "RATE_LIMIT_WAIT_S", 0)
    raw, bk = booking
    err = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    client = FakeGemini([err, call("submit_answer", {"answer": "x", "evidence": [], "confidence": "low",
                                                      "could_not_confirm": True, "topics_used": []})])
    a = run_agent("q", raw["pnr"], raw["last_name"], FakeSource(bk), PinnedFetcher(pinned_snapshots),
                  client=client, provider="gemini", model="m", now=NOW).answer
    assert a.could_not_confirm and len(client.requests) == 2


def test_gemini_hard_error_is_reported(booking, pinned_snapshots):
    raw, bk = booking
    err = errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}})
    a = run_agent("q", raw["pnr"], raw["last_name"], FakeSource(bk), PinnedFetcher(pinned_snapshots),
                  client=FakeGemini([err]), provider="gemini", model="m", now=NOW).answer
    assert a.could_not_confirm and "HTTP 400" in a.answer


def test_provider_autodetect(monkeypatch):
    for k in ("AGENT_PROVIDER", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert config.agent_provider() == "gemini"
    monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
    assert config.agent_provider() == "anthropic"


def test_schema_cleaned_for_gemini():
    from airline_agent.agent.prompts import ANSWER_SCHEMA

    assert "additionalProperties" not in json.dumps(gemini_loop._clean_schema(ANSWER_SCHEMA))
