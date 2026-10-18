"""Assisted capture of YOUR OWN IndiGo booking (no LLM, no API key needed).

1. powershell -ExecutionPolicy Bypass -File scripts\\start_chrome_for_capture.ps1
2. python scripts/capture_live_booking.py
   - you're asked for your PNR and last name (hidden input). They are used ONLY
     to scrub themselves out of the saved file; they're never printed or stored.
   - in the Chrome window, retrieve your booking on the Edit Booking page.
3. The script saves a REDACTED capture to tests/fixtures/live/ and prints its
   structure (key paths and value types only) to help fill in field_paths.

Nothing is typed, clicked or submitted by the script; it only reads the
booking data the page receives in your browser.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airline_agent import config  # noqa: E402
from airline_agent.booking.base import SourceUnavailable  # noqa: E402
from airline_agent.booking.live_indigo import CAPTURE_DIR, capture_booking_json  # noqa: E402
from airline_agent.privacy import mask_pnr, redact_obj  # noqa: E402

# Broader than the agent's patterns on purpose: we don't yet know which call carries the booking.
EXTRA_PATTERNS = ["pnr", "journey", "passenger", "trip", "manage", "api"]


def outline(obj, path: str = "", out: list[str] | None = None, depth: int = 0) -> list[str]:
    """Key paths with value types only (lists show their first element)."""
    out = [] if out is None else out
    if depth > 8 or len(out) > 400:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            outline(v, f"{path}.{k}" if path else str(k), out, depth + 1)
    elif isinstance(obj, list):
        out.append(f"{path}  [list x{len(obj)}]")
        if obj:
            outline(obj[0], f"{path}.0", out, depth + 1)
    else:
        out.append(f"{path}  ({type(obj).__name__})")
    return out


def main() -> None:
    sel = config.load_yaml("booking_selectors.yaml")["live_indigo"]
    print("This captures YOUR OWN booking only. Inputs are hidden and used only for redaction.")
    pnr = getpass.getpass("PNR (hidden): ").strip()
    last = getpass.getpass("Last name (hidden): ").strip()
    if not pnr or not last:
        sys.exit("PNR and last name are required for redaction.")
    cdp = os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222")
    try:
        captured = capture_booking_json(
            cdp, sel["manage_url"], list(sel["booking_response_url_contains"]) + EXTRA_PATTERNS,
            timeout_s=600, settle_s=15,
            message=f">>> In Chrome, retrieve booking {mask_pnr(pnr)} on the Edit Booking tab I just opened. "
                    "I'll stop listening 15 s after the first booking-like response (max 10 min).",
        )
    except SourceUnavailable as e:
        sys.exit(str(e))
    if not captured:
        sys.exit("No JSON responses seen. Did the booking page load?")

    red = redact_obj(captured, secrets=[pnr, last])
    raw_dump = json.dumps(red, default=str)
    leaked = [label for label, s in (("PNR", pnr), ("last name", last)) if s.lower() in raw_dump.lower()]
    if leaked:
        sys.exit(f"Refusing to save: {', '.join(leaked)} still present after redaction.")

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = CAPTURE_DIR / f"indigo_capture_{datetime.now():%Y%m%d_%H%M%S}.redacted.json"
    path.write_text(json.dumps(red, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved {len(red)} redacted response(s) -> {path.relative_to(ROOT)}\n")
    for i, c in enumerate(red):
        print(f"[{i}] {c['status']} {c['url']}")
        for line in outline(c["body"])[:60]:
            print("     ", line)


if __name__ == "__main__":
    main()
