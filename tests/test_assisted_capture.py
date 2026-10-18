"""Assisted capture (attach to a running browser over CDP), exercised on the mock site.

A separate Playwright connection plays the human: it types the PNR into the page.
The code under test only listens. Run with:  pytest -m browser
"""

import socket
import subprocess
import threading
import time

import httpx
import pytest

from airline_agent.booking.base import SourceUnavailable
from airline_agent.booking.live_indigo import capture_booking_json

pytestmark = pytest.mark.browser


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def cdp_browser(tmp_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        exe = p.chromium.executable_path
    port = _free_port()
    proc = subprocess.Popen([exe, f"--remote-debugging-port={port}", f"--user-data-dir={tmp_path}",
                             "--headless=new", "--no-first-run", "about:blank"])
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            httpx.get(url + "/json/version", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    yield url
    proc.terminate()
    proc.wait(timeout=10)


def _human(cdp_url: str, site: str, pnr: str, last_name: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(cdp_url)
        ctx = b.contexts[0]
        pump = ctx.pages[0] if ctx.pages else ctx.new_page()
        page = None
        for _ in range(60):  # wait for the agent to open its tab
            page = next((pg for pg in ctx.pages if "/manage" in pg.url), None)
            if page:
                break
            pump.wait_for_timeout(500)  # sync API: events only arrive while Playwright runs
        assert page, "agent never opened the manage tab"
        page.wait_for_selector("#pnr")
        page.fill("#pnr", pnr)
        page.fill("#last_name", last_name)
        page.click("#find-booking")
        time.sleep(3)


def test_capture_reads_what_the_human_retrieves(cdp_browser, mock_site_url, seed):
    b = seed[5]
    t = threading.Thread(target=_human, args=(cdp_browser, mock_site_url, b["pnr"], b["last_name"]))
    t.start()
    got = capture_booking_json(cdp_browser, mock_site_url + "/manage", ["/api/v1/bookings/lookup"],
                               timeout_s=30, settle_s=1, message="(test)")
    t.join()
    assert got and got[0]["status"] == 200 and got[0]["body"]["bookingRef"] == b["pnr"]


def test_capture_times_out_cleanly(cdp_browser):
    assert capture_booking_json(cdp_browser, None, ["/nothing"], timeout_s=1, message="(test)") == []


def test_no_browser_listening():
    with pytest.raises(SourceUnavailable):
        capture_booking_json("http://127.0.0.1:9", None, ["x"], timeout_s=1)
