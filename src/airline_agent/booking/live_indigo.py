"""Booking source: the real IndiGo Manage Booking page (developer's OWN bookings only).

Design rules (from CLAUDE.md):
- headed browser, persistent profile, human-like pacing, very low volume
- prefer captured JSON network responses over DOM selectors
- selectors and JSON field paths live in config/booking_selectors.yaml
- CAPTCHA -> pause and hand control to the human; never solve or bypass it
- anything saved to disk is redacted first (privacy.redact_obj)

The JSON field mapping can only be completed from a real (redacted) capture of
the developer's own booking. Until ``field_paths`` are filled in, this source
captures + redacts the responses and raises ``SourceUnavailable`` with a hint.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from airline_agent import config
from airline_agent.booking._browser import (
    dismiss_cookie_banner,
    first_present,
    human_pause,
    type_like_human,
    wait_for_human,
)
from airline_agent.booking.base import BookingNotFound, CaptchaRequired, SourceUnavailable
from airline_agent.privacy import mask_pnr, redact_obj
from airline_agent.schemas import Booking, Flight, Passenger

log = logging.getLogger(__name__)

PROFILE_DIR = config.PROJECT_ROOT / ".browser_profile"
CAPTURE_DIR = config.PROJECT_ROOT / "tests" / "fixtures" / "live"
INDIAN_AIRPORTS_HINT = {
    "DEL", "BOM", "BLR", "HYD", "MAA", "CCU", "GOI", "GOX", "PNQ", "AMD", "COK", "JAI", "LKO", "TRV", "IXC",
    "ATQ", "GAU", "BBI", "NAG", "IDR", "VNS", "PAT", "SXR", "IXB", "CJB", "IXE", "VTZ", "RPR", "BDQ", "STV",
}


def dig(obj: Any, path: str | None) -> Any:
    """Follow a dotted path ('a.b.0.c') into nested JSON. None if missing."""
    if not path:
        return None
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def open_manage_booking(page, sel: dict[str, Any]) -> bool:
    """If the PNR form isn't on the current page, click the first matching entry link."""
    dismiss_cookie_banner(page, sel.get("cookie_dismiss_text", []))
    if first_present(page, sel["pnr_input"], timeout_ms=3000):
        return True
    for text in sel.get("entry_link_text", []):
        loc = page.get_by_text(text, exact=False).first
        try:
            if loc.is_visible():
                human_pause(0.8, 1.6)
                loc.click()
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
                if first_present(page, sel["pnr_input"], timeout_ms=10_000):
                    return True
        except Exception:
            continue
    return False


def capture_booking_json(
    cdp_url: str,
    start_url: str | None,
    patterns: list[str],
    timeout_s: float = 300,
    settle_s: float = 6,
    done: Any = None,
    message: str | None = None,
) -> list[dict[str, Any]]:
    """Assisted capture: attach to the user's OWN running Chrome and read booking JSON.

    The person does the navigation and the lookup themselves in a normal browser.
    We only listen to network responses in that browser (all tabs) and never
    type, click or submit anything. Returns captured JSON bodies (raw: redact
    before saving). ``done(captured)`` can end the wait early.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    pats = [p.lower() for p in patterns]
    captured: list[dict[str, Any]] = []
    pending: list[Any] = []

    def on_response(resp) -> None:
        # No I/O inside event handlers with the sync API: just queue it.
        if any(p in resp.url.lower() for p in pats):
            pending.append(resp)

    def drain() -> None:
        while pending:
            resp = pending.pop(0)
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    captured.append({"url": resp.url.split("?")[0], "status": resp.status, "body": resp.json()})
            except Exception:
                log.debug("could not read a matching response body", exc_info=True)

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url, timeout=10_000)
        except PlaywrightError:
            raise SourceUnavailable(
                f"No Chrome listening at {cdp_url}. Start Chrome with remote debugging "
                "(scripts/start_chrome_for_capture.ps1) and try again."
            ) from None
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        ctx.on("response", on_response)
        tab = ctx.new_page()
        if start_url:
            tab.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
        print(message or ">>> In the Chrome window, look up your booking (PNR + last name). I am only reading "
                         "the booking data the page receives and will continue automatically.", flush=True)
        deadline = time.monotonic() + timeout_s
        first_at: float | None = None
        while time.monotonic() < deadline:
            drain()
            if captured and first_at is None:
                first_at = time.monotonic()
            if captured and done is not None and done(captured):
                break
            if first_at is not None and time.monotonic() - first_at > settle_s:
                break
            # The sync API only delivers events while Playwright is running, so wait
            # through Playwright rather than time.sleep().
            try:
                tab.wait_for_timeout(500)
            except PlaywrightError:  # the person closed our tab; keep listening
                tab = ctx.pages[0] if ctx.pages else ctx.new_page()
        drain()
        # Leaving this block only disconnects Playwright; the user's Chrome stays open.
    return captured


class LiveIndiGoSource:
    """Live IndiGo booking source.

    mode="attach" (default): assisted capture from the user's own Chrome (see
        capture_booking_json). IndiGo's CDN refuses its own page scripts to
        automation-launched browsers, so the PNR form never renders there.
    mode="auto": launches a headed browser and fills the form itself. Kept for
        when the site allows it; currently blocked (see README, Phase 0).
    """

    name = "live_indigo"

    def __init__(self, human_timeout_s: float = 300, save_redacted_capture: bool = True,
                 mode: str | None = None, cdp_url: str | None = None):
        self.sel = config.load_yaml("booking_selectors.yaml")["live_indigo"]
        self.human_timeout_s = human_timeout_s
        self.save_redacted_capture = save_redacted_capture
        self.mode = mode or os.getenv("LIVE_INDIGO_MODE", "attach")
        self.cdp_url = cdp_url or os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222")

    def get_booking(self, pnr: str, last_name: str) -> Booking:
        log.info("live_indigo lookup %s (own booking only, mode=%s)", mask_pnr(pnr), self.mode)
        if self.mode == "attach":
            captured = capture_booking_json(
                self.cdp_url, self.sel["manage_url"], self.sel["booking_response_url_contains"],
                timeout_s=self.human_timeout_s, done=self._mappable,
                message=f">>> In the Chrome window, retrieve booking {mask_pnr(pnr)} yourself on the Edit Booking "
                        "page. I am only reading the booking data the page receives.",
            )
            if not captured:
                raise SourceUnavailable("No booking data seen in Chrome before the timeout.")
            if self.save_redacted_capture:
                self._save_capture(captured, [pnr, last_name])
            return self._map(captured, mask_pnr(pnr))
        return self._get_booking_auto(pnr, last_name)

    def _mappable(self, captured: list[dict[str, Any]]) -> bool:
        try:
            self._map(captured, "******")
            return True
        except Exception:
            return False

    def _get_booking_auto(self, pnr: str, last_name: str) -> Booking:
        from playwright.sync_api import sync_playwright

        captured: list[dict[str, Any]] = []
        pending: list[Any] = []
        patterns = [p.lower() for p in self.sel["booking_response_url_contains"]]

        def on_response(resp) -> None:
            # No I/O inside event handlers with the sync API: queue and read later.
            if any(p in resp.url.lower() for p in patterns):
                pending.append(resp)

        def drain() -> None:
            while pending:
                resp = pending.pop(0)
                try:
                    if "json" in (resp.headers.get("content-type") or ""):
                        captured.append({"url": resp.url.split("?")[0], "status": resp.status, "body": resp.json()})
                except Exception:
                    log.debug("could not read a matching response body", exc_info=True)

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False, slow_mo=50)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.on("response", on_response)
                page.goto(self.sel["manage_url"], wait_until="domcontentloaded", timeout=60_000)
                human_pause(2, 4)
                self._maybe_handoff(page)
                open_manage_booking(page, self.sel)

                pnr_sel = first_present(page, self.sel["pnr_input"], timeout_ms=15_000)
                name_sel = first_present(page, self.sel["last_name_input"], timeout_ms=5_000)
                submit_sel = first_present(page, self.sel["submit"], timeout_ms=5_000)
                if not (pnr_sel and name_sel and submit_sel):
                    raise SourceUnavailable("IndiGo Manage Booking form not recognised; update config selectors.")
                type_like_human(page, pnr_sel, pnr)
                human_pause(0.8, 1.6)
                type_like_human(page, name_sel, last_name)
                human_pause(0.8, 1.6)
                page.locator(submit_sel).first.click()
                page.wait_for_load_state("networkidle", timeout=45_000)
                human_pause(1.5, 3)
                self._maybe_handoff(page)
                page.wait_for_load_state("networkidle", timeout=45_000)
                drain()

                body_text = page.locator("body").inner_text().lower()
                if not captured and any(t in body_text for t in self.sel["not_found_text"]):
                    raise BookingNotFound("IndiGo did not find a booking for these details.")
            finally:
                ctx.close()

        if not captured:
            raise SourceUnavailable("No booking JSON captured from IndiGo (site flow may have changed).")
        if self.save_redacted_capture:
            self._save_capture(captured, [pnr, last_name])
        return self._map(captured, mask_pnr(pnr))

    # --- helpers ---------------------------------------------------------
    def _maybe_handoff(self, page) -> None:
        if first_present(page, self.sel["captcha_marker"]) is None:
            return
        ok = wait_for_human(page, self.sel["captcha_marker"], self.human_timeout_s,
                            ">>> IndiGo is showing a verification challenge. Please complete it yourself in the "
                            "browser window. The agent will wait and continue afterwards.")
        if not ok:
            raise CaptchaRequired("Verification challenge not completed in time.")

    def _save_capture(self, captured: list[dict[str, Any]], secrets: list[str]) -> None:
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        path = CAPTURE_DIR / f"indigo_capture_{datetime.now():%Y%m%d_%H%M%S}.redacted.json"
        path.write_text(json.dumps(redact_obj(captured, secrets=secrets), indent=2, default=str), encoding="utf-8")
        log.info("saved redacted capture to %s", path.name)

    def _map(self, captured: list[dict[str, Any]], pnr_masked: str) -> Booking:
        fp = self.sel["field_paths"]
        if not fp.get("segments") or not fp.get("passengers"):
            raise SourceUnavailable(
                "Captured IndiGo booking JSON, but field_paths in config/booking_selectors.yaml are not set yet. "
                "Inspect the redacted capture in tests/fixtures/live/ and fill them in."
            )
        for c in captured:
            body = c["body"]
            segs = dig(body, fp["segments"])
            paxes = dig(body, fp["passengers"])
            if not isinstance(segs, list) or not isinstance(paxes, list):
                continue
            fare = str(dig(body, fp.get("fare_type")) or "UNKNOWN")
            flights = []
            for s in segs:
                o, d = str(dig(s, fp["seg_origin"])), str(dig(s, fp["seg_destination"]))
                flights.append(Flight(
                    flight_number=str(dig(s, fp["seg_flight_number"])),
                    origin=o, destination=d,
                    departure=datetime.fromisoformat(str(dig(s, fp["seg_departure"]))),
                    is_international=not (o in INDIAN_AIRPORTS_HINT and d in INDIAN_AIRPORTS_HINT),
                ))
            pax_map = {"ADT": "adult", "ADULT": "adult", "CHD": "child", "CHILD": "child", "INF": "infant", "INFANT": "infant"}
            created = dig(body, fp.get("created_at"))
            return Booking(
                pnr_masked=pnr_masked,
                airline="IndiGo",
                booking_created_at=datetime.fromisoformat(str(created)) if created else datetime.min,
                status="cancelled" if "cancel" in str(dig(body, fp.get("status")) or "").lower() else "confirmed",
                fare_type=fare,
                fare_category=config.normalize_fare("indigo", fare),
                cabin=str(dig(body, fp.get("cabin")) or "Economy"),
                flights=flights,
                passengers=[Passenger(type=pax_map.get(str(dig(p, fp["pax_type"])).upper(), "adult"),
                                      extra_baggage_kg=int(dig(p, fp.get("pax_extra_baggage_kg")) or 0)) for p in paxes],
                booked_via="unknown",
                source="live_indigo",
            )
        raise SourceUnavailable("Captured JSON did not match configured field_paths.")
