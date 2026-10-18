"""Playwright against the running mock site. Run with:  pytest -m browser"""

import pytest

from airline_agent.booking.base import BookingNotFound, CaptchaRequired, SourceUnavailable, map_testair_json
from airline_agent.booking.mock_testair import MockTestAirSource
from mock_site.app import materialise

pytestmark = pytest.mark.browser


def _cmp(got, seed_b):
    want = map_testair_json(materialise(seed_b))
    g, w = got.model_dump(), want.model_dump()
    g.pop("booking_created_at"), w.pop("booking_created_at")  # materialised a few ms apart
    assert g == w


@pytest.mark.parametrize("switches", [{}, {"layout": "v2"}, {"slow": "1"}])
def test_extracts_via_network(mock_site_url, seed, switches):
    b = seed[21]
    src = MockTestAirSource(base_url=mock_site_url, switches=switches)
    _cmp(src.get_booking(b["pnr"], b["last_name"]), b)
    assert src.last_method == "network"


def test_dom_fallback(mock_site_url, seed):
    b = seed[3]
    src = MockTestAirSource(base_url=mock_site_url, response_timeout_ms=1)
    src.sel = {**src.sel, "booking_response_url_contains": "/never-matches"}
    got = src.get_booking(b["pnr"], b["last_name"])
    assert src.last_method == "dom"
    _cmp(got, b)


def test_not_found(mock_site_url):
    with pytest.raises(BookingNotFound):
        MockTestAirSource(base_url=mock_site_url).get_booking("TZZZZ9", "Nobody")


def test_error_500(mock_site_url, seed):
    with pytest.raises(SourceUnavailable):
        MockTestAirSource(base_url=mock_site_url, switches={"error": "500"}).get_booking(seed[0]["pnr"], seed[0]["last_name"])


def test_captcha_headless_never_bypassed(mock_site_url, seed):
    with pytest.raises(CaptchaRequired):
        MockTestAirSource(base_url=mock_site_url, switches={"captcha": "1"}).get_booking(seed[0]["pnr"], seed[0]["last_name"])


def test_site_down():
    with pytest.raises(SourceUnavailable):
        MockTestAirSource(base_url="http://127.0.0.1:9").get_booking("T12345", "X")
