"""Small Playwright helpers shared by browser-based booking sources."""

from __future__ import annotations

import random
import time

from playwright.sync_api import Page


def first_present(page: Page, selectors: list[str], timeout_ms: int = 0) -> str | None:
    """Return the first selector that matches a visible element, or None."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for sel in selectors:
            try:
                if page.locator(sel).first.is_visible():
                    return sel
            except Exception:
                continue
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.2)


def human_pause(lo: float = 0.4, hi: float = 1.2, enabled: bool = True) -> None:
    if enabled:
        time.sleep(random.uniform(lo, hi))


def type_like_human(page: Page, selector: str, text: str, enabled: bool = True) -> None:
    loc = page.locator(selector).first
    loc.click()
    if enabled:
        loc.press_sequentially(text, delay=random.randint(60, 140))
    else:
        loc.fill(text)


def wait_for_human(page: Page, captcha_selectors: list[str], timeout_s: float, message: str) -> bool:
    """Headed browser: hand control to the person until the challenge disappears.

    Never interacts with the challenge itself. Returns True once it's gone.
    """
    print(message, flush=True)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if first_present(page, captcha_selectors) is None:
            return True
        time.sleep(1.0)
    return False


def dismiss_cookie_banner(page: Page, texts: list[str]) -> bool:
    """Click the essential-only option of a cookie banner, if one is showing."""
    for t in texts:
        try:
            btn = page.get_by_text(t, exact=True).first
            if btn.is_visible():
                btn.click()
                return True
        except Exception:
            continue
    return False
