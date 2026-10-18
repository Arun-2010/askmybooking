from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from airline_agent.booking.base import map_testair_json
from mock_site.app import app, materialise


@pytest.fixture
def client():
    return TestClient(app)


def test_seed_coverage(seed):
    assert 40 <= len(seed) <= 50
    tags = {t for b in seed for t in b["tags"]}
    for need in ["infant", "child", "connecting", "international", "cancelled", "lookin:eligible",
                 "booked_via_agent", "extra_baggage", "business"]:
        assert need in tags, need
    assert {b["fare_code"] for b in seed} == {"TA-LITE", "TA-SAVE", "TA-FLEX", "TA-PLUS", "TA-BIZ"}
    assert len({b["pnr"] for b in seed}) == len(seed)


def test_lookup_ok(client, seed):
    b = seed[0]
    r = client.post("/api/v1/bookings/lookup", json={"pnr": b["pnr"].lower(), "lastName": b["last_name"].upper()})
    assert r.status_code == 200
    assert r.json()["bookingRef"] == b["pnr"]


def test_lookup_not_found(client):
    r = client.post("/api/v1/bookings/lookup", json={"pnr": "TZZZZ9", "lastName": "Nobody"})
    assert r.status_code == 404


def test_error_switch(client, seed):
    r = client.post("/api/v1/bookings/lookup?error=500", json={"pnr": seed[0]["pnr"], "lastName": seed[0]["last_name"]})
    assert r.status_code == 500


def test_captcha_switch(client, seed):
    page = client.get("/manage?captcha=1")
    assert 'id="ta-captcha"' in page.text
    r = client.post("/api/v1/bookings/lookup?captcha=1", json={"pnr": seed[0]["pnr"], "lastName": seed[0]["last_name"]})
    assert r.status_code == 403


def test_captcha_cleared_by_human_cookie(client):
    r = client.post("/captcha/verify", data={"human": "yes", "qs": "captcha=1"}, follow_redirects=False)
    assert r.status_code == 303 and "ta_human" in r.headers.get("set-cookie", "")


def test_layout_v2_changes_markup(client):
    v1, v2 = client.get("/manage").text, client.get("/manage?layout=v2").text
    assert 'id="pnr"' in v1 and 'id="pnr"' not in v2 and 'data-qa="ref-input"' in v2


def test_no_real_airline_branding(client):
    html = client.get("/").text + client.get("/manage").text
    assert "fictional" in html.lower()
    assert "indigo" not in html.lower()


def test_relative_times_materialise(seed):
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    b = next(x for x in seed if "lookin:eligible" in x["tags"] and x["created_hours_ago"] == 20)
    data = materialise(b, now)
    created = datetime.fromisoformat(data["createdAt"])
    assert now - created == timedelta(hours=20)
    dep = datetime.fromisoformat(data["journeys"][0]["segments"][0]["departureLocal"])
    assert timedelta(days=14) < dep - now < timedelta(days=16)


def test_mapping_matches_seed_for_all_bookings(seed):
    for b in seed:
        bk = map_testair_json(materialise(b))
        assert bk.pnr_masked == b["pnr"][:2] + "****"
        assert bk.fare_type == b["fare_code"] and bk.fare_category != "Unknown"
        assert bk.status == b["status"]
        assert [f.origin for f in bk.flights] == [s["origin"] for s in b["segments"]]
        assert [p.type for p in bk.passengers] == [p["type"] for p in b["passengers"]]
        assert [p.extra_baggage_kg for p in bk.passengers] == [p["extra_baggage_kg"] for p in b["passengers"]]
        assert bk.booked_via == ("airline_website" if b["booked_via"] == "website" else "agent")
        # names never make it into the Booking
        assert b["last_name"] not in bk.model_dump_json()
