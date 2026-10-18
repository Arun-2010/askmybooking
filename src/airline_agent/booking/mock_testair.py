"""Booking source: Playwright against the mock TestAir site.

Prefers the JSON network response the page itself fetches; falls back to
reading the DOM if no response was captured.
"""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import urlencode

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from airline_agent import config
from airline_agent.booking._browser import first_present, human_pause, type_like_human, wait_for_human
from airline_agent.booking.base import (
    BookingNotFound,
    CaptchaRequired,
    SourceUnavailable,
    map_testair_json,
)
from airline_agent.privacy import mask_pnr
from airline_agent.schemas import Booking, Flight, Passenger

log = logging.getLogger(__name__)


class MockTestAirSource:
    name = "mock_testair"

    def __init__(
        self,
        base_url: str | None = None,
        switches: dict[str, str] | None = None,
        headless: bool = True,
        allow_human_handoff: bool = False,
        human_timeout_s: float = 120,
        response_timeout_ms: int = 15_000,
        human_pacing: bool = False,
    ):
        self.base_url = (base_url or config.testair_base_url()).rstrip("/")
        self.switches = switches or {}
        self.headless = headless
        self.allow_human_handoff = allow_human_handoff and not headless
        self.human_timeout_s = human_timeout_s
        self.response_timeout_ms = response_timeout_ms
        self.human_pacing = human_pacing
        self.sel = config.load_yaml("booking_selectors.yaml")["testair"]
        self.last_method: str | None = None  # "network" | "dom" — for eval/diagnostics

    def get_booking(self, pnr: str, last_name: str) -> Booking:
        log.info("mock_testair lookup %s", mask_pnr(pnr))
        qs = urlencode(self.switches)
        url = f"{self.base_url}{self.sel['manage_path']}" + (f"?{qs}" if qs else "")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                try:
                    page = browser.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                    self._handle_captcha(page)
                    return self._lookup(page, pnr, last_name)
                finally:
                    browser.close()
        except (BookingNotFound, CaptchaRequired, SourceUnavailable):
            raise
        except PlaywrightTimeout as e:
            raise SourceUnavailable(f"TestAir timed out: {type(e).__name__}") from None
        except PlaywrightError as e:
            raise SourceUnavailable(f"TestAir unreachable: {str(e).splitlines()[0][:200]}") from None

    # --- steps -----------------------------------------------------------
    def _handle_captcha(self, page) -> None:
        if first_present(page, self.sel["captcha_marker"]) is None:
            return
        if not self.allow_human_handoff:
            raise CaptchaRequired("TestAir showed a human verification step; run headed to complete it manually.")
        ok = wait_for_human(page, self.sel["captcha_marker"], self.human_timeout_s,
                            ">>> Verification required. Please complete it in the browser window; I'll continue after.")
        if not ok:
            raise CaptchaRequired("Human verification was not completed in time.")
        page.wait_for_load_state("domcontentloaded")

    def _lookup(self, page, pnr: str, last_name: str) -> Booking:
        pnr_sel = first_present(page, self.sel["pnr_input"], timeout_ms=5000)
        name_sel = first_present(page, self.sel["last_name_input"])
        submit_sel = first_present(page, self.sel["submit"])
        if not (pnr_sel and name_sel and submit_sel):
            raise SourceUnavailable("Manage Booking form not recognised (layout changed?)")
        type_like_human(page, pnr_sel, pnr, self.human_pacing)
        human_pause(enabled=self.human_pacing)
        type_like_human(page, name_sel, last_name, self.human_pacing)
        human_pause(enabled=self.human_pacing)

        marker = self.sel["booking_response_url_contains"]
        try:
            with page.expect_response(lambda r: marker in r.url, timeout=self.response_timeout_ms) as info:
                page.locator(submit_sel).first.click()
            resp = info.value
        except PlaywrightTimeout:
            log.warning("no booking network response captured; trying DOM fallback")
            return self._from_dom(page)

        if resp.status == 404:
            raise BookingNotFound("No booking matches this PNR and last name.")
        if resp.status == 403:
            raise CaptchaRequired("TestAir requires human verification.")
        if resp.status >= 500:
            raise SourceUnavailable(f"TestAir returned HTTP {resp.status}")
        if not resp.ok:
            raise SourceUnavailable(f"TestAir returned HTTP {resp.status}")
        try:
            data = resp.json()
        except Exception:
            return self._from_dom(page)
        self.last_method = "network"
        return map_testair_json(data, source=self.name)

    def _from_dom(self, page) -> Booking:
        d = self.sel["dom"]
        if first_present(page, d["not_found"], timeout_ms=2000):
            raise BookingNotFound("No booking matches this PNR and last name.")
        try:
            page.wait_for_selector(d["pnr"], timeout=5000)
        except PlaywrightTimeout:
            raise SourceUnavailable("Booking page not recognised (layout changed?)") from None

        def text(s: str) -> str:
            return page.locator(s).first.inner_text().strip()

        flights = [
            Flight(
                flight_number=li.get_attribute("data-flight"),
                origin=li.get_attribute("data-from"),
                destination=li.get_attribute("data-to"),
                departure=datetime.fromisoformat(li.get_attribute("data-dep")),
                is_international=li.get_attribute("data-intl") == "true",
            )
            for li in page.locator(d["segment"]).all()
        ]
        pax_map = {"ADT": "adult", "CHD": "child", "INF": "infant"}
        passengers = [
            Passenger(type=pax_map.get(li.get_attribute("data-type"), "adult"),
                      extra_baggage_kg=int(li.get_attribute("data-extra-kg") or 0))
            for li in page.locator(d["passenger"]).all()
        ]
        fare = text(d["fare_code"])
        self.last_method = "dom"
        return Booking(
            pnr_masked=mask_pnr(text(d["pnr"])),
            airline="TestAir",
            booking_created_at=datetime.fromisoformat(text(d["created"])),
            status="cancelled" if text(d["status"]).upper() == "CANCELLED" else "confirmed",
            fare_type=fare,
            fare_category=config.normalize_fare("testair", fare),
            cabin=text(d["cabin"]),
            flights=flights,
            passengers=passengers,
            booked_via={"WEB": "airline_website", "AGENCY": "agent"}.get(text(d["channel"]), "unknown"),
            source=self.name,
        )
