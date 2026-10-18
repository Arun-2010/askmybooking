from datetime import datetime, timedelta, timezone

import pytest

from airline_agent.policy.fetcher import PolicyFetcher, PolicyFetchError, UnknownPolicySource, clean_html, topic_urls
from airline_agent.policy.store import SnapshotStore

PAGE = "<html><body><nav>menu</nav><main><h1>Baggage</h1>" + "<p>Check-in baggage 15kg per person.</p>" * 30 + "</main></body></html>"
ROBOTS_OK = "User-agent: *\nDisallow: /search.html\n"


class FakeWeb:
    def __init__(self, pages=None, robots=ROBOTS_OK, fail=False):
        self.pages = pages or {}
        self.robots = robots
        self.fail = fail
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return self.robots
        if self.fail:
            raise ConnectionError("down")
        return self.pages.get(url, PAGE)


@pytest.fixture
def store():
    s = SnapshotStore(":memory:")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr("airline_agent.policy.fetcher.MIN_SECONDS_BETWEEN_REQUESTS", 0)


def make(store, web, **kw):
    return PolicyFetcher(store, ttl_hours=kw.pop("ttl", 6), http_get=web, render_js=kw.pop("render", False), **kw)


def test_urls_only_from_config():
    urls = topic_urls("indigo", "baggage")
    assert urls and all(u.startswith("https://www.goindigo.in/") for u in urls)
    with pytest.raises(UnknownPolicySource):
        topic_urls("indigo", "pets")
    with pytest.raises(UnknownPolicySource):
        topic_urls("someair", "baggage")


def test_clean_html_drops_nav_and_keeps_text():
    text = clean_html(PAGE)
    assert "15kg per person" in text


def test_live_fetch_then_fresh_cache(store):
    web = FakeWeb()
    f = make(store, web)
    s1 = f.fetch_policy("indigo", "baggage")[0]
    assert not s1.from_cache and len(s1.content_hash) == 64
    n = len(web.calls)
    s2 = f.fetch_policy("indigo", "baggage")[0]
    assert s2.from_cache and s2.snapshot_id == s1.snapshot_id
    assert len(web.calls) == n  # no new request within TTL


def test_stale_cache_refetches(store):
    web = FakeWeb()
    f = make(store, web, ttl=0)
    f.fetch_policy("indigo", "baggage")
    s2 = f.fetch_policy("indigo", "baggage")[0]
    assert not s2.from_cache


def test_live_failure_falls_back_to_cache(store):
    f = make(store, FakeWeb(), ttl=0)
    first = f.fetch_policy("indigo", "baggage")[0]
    f2 = make(store, FakeWeb(fail=True), ttl=0)
    snap = f2.fetch_policy("indigo", "baggage")[0]
    assert snap.from_cache and snap.snapshot_id == first.snapshot_id
    assert f2.stats["fallback_cache"] == 1
    assert store.events("fetch_failed")


def test_failure_without_cache_raises(store):
    with pytest.raises(PolicyFetchError):
        make(store, FakeWeb(fail=True)).fetch_policy("indigo", "baggage")


def test_robots_disallow_blocks(store):
    web = FakeWeb(robots="User-agent: *\nDisallow: /\n")
    with pytest.raises(PolicyFetchError):
        make(store, web).fetch_policy("indigo", "baggage")
    assert not any("baggage-allowance" in c for c in web.calls)


def test_hash_change_logs_event(store):
    url = topic_urls("indigo", "baggage")[0]
    f = make(store, FakeWeb(), ttl=0)
    f.fetch_policy("indigo", "baggage")
    changed = PAGE.replace("15kg", "20kg")
    make(store, FakeWeb(pages={url: changed}), ttl=0).fetch_policy("indigo", "baggage")
    ev = store.events("policy_changed")
    assert len(ev) == 1 and ev[0]["url"] == url


def test_short_page_uses_browser_render(store):
    rendered = []

    def render(url, text_only=False):
        rendered.append(url)
        return PAGE

    url = topic_urls("indigo", "baggage")[0]
    f = make(store, FakeWeb(pages={url: "<html><body>Loading…</body></html>"}), render=render)
    snap = f.fetch_policy("indigo", "baggage")[0]
    assert rendered == [url] and "15kg" in snap.text


def test_http_error_uses_browser_render(store):
    def render(url, text_only=False):
        return ROBOTS_OK if text_only else PAGE

    def http(url):
        raise TimeoutError("cdn stall")

    f = PolicyFetcher(store, ttl_hours=6, http_get=http, render_js=render)
    assert "15kg" in f.fetch_policy("indigo", "baggage")[0].text


def test_store_is_fresh(store):
    snap = store.save("indigo", "baggage", "u", "x" * 500, fetched_at=datetime.now(timezone.utc) - timedelta(hours=7))
    assert not store.is_fresh(snap, 6)
    assert store.is_fresh(snap, 8)
