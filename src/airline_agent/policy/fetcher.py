"""Live policy fetcher: official URL (from config only) -> clean text -> snapshot.

URLs come exclusively from ``config/policy_sources.yaml``. This module never
builds or guesses a policy URL.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Callable
from urllib import robotparser
from urllib.parse import urlparse

import httpx

from airline_agent import config
from airline_agent.policy.store import SnapshotStore
from airline_agent.schemas import PolicySnapshot, Topic

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 AirlinePolicyAgent/0.1 (+low-volume research; respects robots.txt)"
)
MIN_TEXT_CHARS = 400  # below this, assume the page is JS-rendered and retry with a browser
MIN_SECONDS_BETWEEN_REQUESTS = float(os.getenv("POLICY_MIN_INTERVAL_S") or 8)
HTTP_TIMEOUT_S = 12


class PolicyFetchError(Exception):
    pass


class RobotsDisallowed(PolicyFetchError):
    pass


class UnknownPolicySource(PolicyFetchError):
    pass


def topic_urls(airline: str, topic: str) -> list[str]:
    """Verified URLs for (airline, topic). Raises if none are configured."""
    sources = config.policy_sources()
    entry = sources.get(airline.lower(), {}).get("topics", {}).get(topic)
    if not entry:
        raise UnknownPolicySource(f"No verified policy URL configured for {airline}/{topic}")
    return [u["url"] if isinstance(u, dict) else u for u in entry]


def clean_html(html: str, url: str = "") -> str:
    """Extract readable main text. trafilatura first, BeautifulSoup fallback."""
    text = ""
    try:
        import trafilatura

        text = trafilatura.extract(
            html, url=url or None, include_tables=True, include_comments=False,
            include_links=False, favor_recall=True,
        ) or ""
    except Exception as e:  # pragma: no cover - library edge cases
        log.debug("trafilatura failed: %s", e)
    if len(text) < MIN_TEXT_CHARS:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form", "iframe"]):
            tag.decompose()
        bs_text = soup.get_text("\n")
        if len(bs_text) > len(text):
            text = bs_text
    return normalize_text(text)


def normalize_text(text: str) -> str:
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if not ln and (not out or not out[-1]):
            continue
        out.append(ln)
    return "\n".join(out).strip()


class PolicyFetcher:
    """Fetch policy pages live with a TTL cache and cached fallback on failure."""

    def __init__(
        self,
        store: SnapshotStore,
        ttl_hours: float | None = None,
        http_get: Callable[[str], str] | None = None,
        render_js: Callable[[str], str] | None = None,
        check_robots: bool = True,
    ):
        self.store = store
        self.ttl_hours = config.cache_ttl_hours() if ttl_hours is None else ttl_hours
        self._http_get = http_get or self._default_http_get
        self._render_js = render_js if render_js is not None else BrowserRenderer()
        self.check_robots = check_robots
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self._last_request_at = 0.0
        self.stats = {"live_ok": 0, "cache_fresh": 0, "fallback_cache": 0, "failed": 0}

    # --- public ----------------------------------------------------------
    def fetch_policy(self, airline: str, topic: Topic, force_live: bool = False) -> list[PolicySnapshot]:
        """One snapshot per configured URL for the topic.

        Order of preference: fresh cache (< TTL) -> live fetch -> stale cache
        (from_cache=True). Raises PolicyFetchError only when there is nothing.
        """
        snaps: list[PolicySnapshot] = []
        errors: list[str] = []
        try:
            for url in topic_urls(airline, topic):
                try:
                    snaps.append(self._fetch_one(airline, topic, url, force_live))
                except PolicyFetchError as e:
                    errors.append(f"{url}: {e}")
        finally:
            close = getattr(self._render_js, "close", None)
            if callable(close):
                close()
        if not snaps:
            raise PolicyFetchError("; ".join(errors) or "no policy sources")
        return snaps

    # --- internals -------------------------------------------------------
    def _fetch_one(self, airline: str, topic: str, url: str, force_live: bool) -> PolicySnapshot:
        cached = self.store.latest(airline, topic, url)
        if cached and not force_live and self.store.is_fresh(cached, self.ttl_hours):
            self.stats["cache_fresh"] += 1
            self.store.log_event("cache_hit", airline, topic, url, cached.snapshot_id)
            # Within TTL the snapshot is still "the latest we checked"; we report
            # it honestly as coming from cache.
            return cached
        try:
            text = self._fetch_live_text(url)
            snap = self.store.save(airline, topic, url, text)
            self.stats["live_ok"] += 1
            self.store.log_event("fetch_ok", airline, topic, url, snap.snapshot_id)
            return snap
        except Exception as e:
            self.stats["failed"] += 1
            self.store.log_event("fetch_failed", airline, topic, url, repr(e)[:500])
            log.warning("live fetch failed for %s: %s", url, e)
            if cached:
                self.stats["fallback_cache"] += 1
                return cached
            raise PolicyFetchError(str(e)) from e

    def _fetch_live_text(self, url: str) -> str:
        if self.check_robots and not self._robots_allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        self._pace()
        try:
            text = clean_html(self._http_get(url), url)
        except Exception as e:
            # Many airline sites sit behind a CDN that stalls non-browser
            # clients. Fall back to an ordinary browser (no stealth tricks).
            log.info("plain HTTP fetch failed (%s); trying browser: %s", type(e).__name__, url)
            if not self._render_js:
                raise
            text = ""
        if len(text) < MIN_TEXT_CHARS and self._render_js:
            log.info("page text short (%d chars), rendering with browser: %s", len(text), url)
            self._pace()
            text = clean_html(self._render_js(url), url)
        if len(text) < MIN_TEXT_CHARS:
            raise PolicyFetchError(f"extracted text too short ({len(text)} chars)")
        return text

    def _pace(self) -> None:
        wait = MIN_SECONDS_BETWEEN_REQUESTS - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _robots_allowed(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = robotparser.RobotFileParser()
            body = None
            try:
                body = self._http_get(base + "/robots.txt")
            except Exception:
                if self._render_js:
                    try:
                        body = self._render_js(base + "/robots.txt", text_only=True)
                    except Exception:
                        body = None
            if body is None:
                # Could not read robots.txt at all -> be conservative and treat
                # the site as unreachable rather than assuming permission.
                raise PolicyFetchError("robots.txt unreachable")
            rp.parse(body.splitlines())
            self._robots[base] = rp
        return rp.can_fetch(USER_AGENT, url)

    @staticmethod
    def _default_http_get(url: str) -> str:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"},
            follow_redirects=True, timeout=HTTP_TIMEOUT_S,
        ) as c:
            r = c.get(url)
            r.raise_for_status()
            return r.text


class BrowserRenderer:
    """Renders a page in a real Chromium, one fresh browser per page.

    Headed by default (POLICY_BROWSER_HEADLESS=1 to change): some airline CDNs
    serve an error page to headless browsers, and refuse repeat navigations in
    one session. We do not use stealth plugins, fingerprint spoofing or CAPTCHA
    solvers; if the site still refuses, the fetch fails and the cached snapshot
    is used.
    """

    def __init__(self, headless: bool | None = None):
        self.headless = os.getenv("POLICY_BROWSER_HEADLESS", "0") == "1" if headless is None else headless

    def __call__(self, url: str, text_only: bool = False) -> str:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            try:
                page = browser.new_context(locale="en-IN").new_page()
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                if resp is not None and resp.status >= 400:
                    raise PolicyFetchError(f"HTTP {resp.status}")
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass
                return page.inner_text("body") if text_only else page.content()
            finally:
                browser.close()

    def close(self) -> None:  # kept for API symmetry; nothing persistent to close
        pass
