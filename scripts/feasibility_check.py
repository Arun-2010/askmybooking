"""Phase 0: can the live IndiGo Manage Booking page be used with browser automation?

Opens goindigo.in in a VISIBLE browser, navigates to Manage Booking, submits a
deliberately fake PNR + an obviously fake surname, and reports what happened:

    AUTOMATION_OK      the site answered with a normal "not found" style message
    CAPTCHA            a verification challenge appeared (we never solve it)
    BLOCKED            CDN/bot-protection error page or refused navigation
    FORM_NOT_FOUND     couldn't find the PNR form (selectors need updating)
    UNKNOWN            something else; see the screenshot

One run = one lookup. Writes eval/results/feasibility.json and a screenshot.
Never use a PNR you found online; the default PNR is random-looking and the
surname is fake, so it cannot match a real booking.

    python scripts/feasibility_check.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright  # noqa: E402

from airline_agent import config  # noqa: E402
from airline_agent.booking._browser import first_present, human_pause, type_like_human  # noqa: E402
from airline_agent.booking.live_indigo import PROFILE_DIR, open_manage_booking  # noqa: E402

FAKE_PNR = "Q9Z7X2"
FAKE_LAST_NAME = "Feasibilitytest"
BLOCK_MARKERS = ["something went wrong", "access denied", "request blocked", "please contact our customer support"]
OUT_DIR = ROOT / "eval" / "results"


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--pause", action="store_true",
                    help="if the form isn't found automatically, wait for you to open it by hand (records the URL)")
    args = ap.parse_args()
    sel = config.load_yaml("booking_selectors.yaml")["live_indigo"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result: dict = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "start_url": sel["manage_url"], "steps": []}

    def step(msg: str) -> None:
        print("·", msg, flush=True)
        result["steps"].append(msg)

    xhr: list[str] = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False, slow_mo=50)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("response", lambda r: xhr.append(f"{r.status} {r.url.split('?')[0]}")
                if r.request.resource_type in ("xhr", "fetch") else None)
        verdict = "UNKNOWN"
        try:
            try:
                page.goto(sel["manage_url"], wait_until="domcontentloaded", timeout=60_000)
            except Exception as e:
                step(f"navigation failed: {str(e).splitlines()[0][:160]}")
                verdict = "BLOCKED"
                raise StopIteration
            human_pause(3, 5)
            body = page.locator("body").inner_text().lower()
            if any(m in body for m in BLOCK_MARKERS) and len(body) < 400:
                step("homepage served an error/block page")
                verdict = "BLOCKED"
                raise StopIteration
            step("homepage loaded")
            if first_present(page, sel["captcha_marker"]):
                verdict = "CAPTCHA"
                step("verification challenge on homepage")
                raise StopIteration
            found = open_manage_booking(page, sel)
            if not found and args.pause:
                print(">>> Please open the Manage Booking / PNR form yourself in the browser (don't submit). Waiting 120 s...")
                found = first_present(page, sel["pnr_input"], timeout_ms=120_000) is not None
                if not found:
                    inputs = page.evaluate("() => [...document.querySelectorAll('input')].filter(e=>e.offsetParent)"
                                           ".map(e => ({name:e.name,id:e.id,placeholder:e.placeholder}))")
                    result["visible_inputs"] = inputs[:20]
            if not found:
                verdict = "FORM_NOT_FOUND"
                step("PNR form not found via entry links; update entry_link_text / pnr_input in config")
                raise StopIteration
            step(f"manage booking form found at {page.url.split('?')[0]}")
            result["manage_url_observed"] = page.url.split("?")[0]
            pnr_sel = first_present(page, sel["pnr_input"])
            name_sel = first_present(page, sel["last_name_input"], timeout_ms=3000)
            submit_sel = first_present(page, sel["submit"], timeout_ms=3000)
            result["selectors_matched"] = {"pnr": pnr_sel, "last_name": name_sel, "submit": submit_sel}
            if not (pnr_sel and name_sel and submit_sel):
                verdict = "FORM_NOT_FOUND"
                step("form fields/submit not all matched")
                raise StopIteration
            type_like_human(page, pnr_sel, FAKE_PNR)
            human_pause(1, 2)
            type_like_human(page, name_sel, FAKE_LAST_NAME)
            human_pause(1, 2)
            page.locator(submit_sel).first.click()
            step("submitted fake PNR")
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except Exception:
                pass
            time.sleep(3)
            body = page.locator("body").inner_text().lower()
            if first_present(page, sel["captcha_marker"]):
                verdict = "CAPTCHA"
                step("verification challenge after submit")
            elif any(t in body for t in sel["not_found_text"]):
                verdict = "AUTOMATION_OK"
                step("site responded with a not-found style message")
            elif any(m in body for m in BLOCK_MARKERS):
                verdict = "BLOCKED"
                step("error/block page after submit")
        except StopIteration:
            pass
        finally:
            shot = OUT_DIR / "feasibility.png"
            try:
                page.screenshot(path=str(shot))
                result["screenshot"] = str(shot.relative_to(ROOT))
            except Exception:
                pass
            ctx.close()
    result["verdict"] = verdict
    result["booking_like_responses"] = [x for x in xhr if any(k in x.lower() for k in ("booking", "itinerary", "retrieve"))][:20]
    (OUT_DIR / "feasibility.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nVERDICT: {verdict}   (details: eval/results/feasibility.json)")


if __name__ == "__main__":
    main()
