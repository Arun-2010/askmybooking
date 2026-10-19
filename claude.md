# CLAUDE.md — Live Airline Policy Agent (PNR-based)

## What this project is

An AI agent that answers airline policy questions (baggage, cancellation, change fees, web check-in)
**for a specific booking**, using **the latest information fetched live from the airline's website** —
not from pre-downloaded policy documents.

Example:
> User: "PNR ABC123, last name Reddy. How much check-in baggage can I take?"
> Agent: reads the booking (route, fare type, add-ons) → fetches the airline's current baggage page →
> answers for that exact booking, citing the source URL and the time it was checked.

This started as a document-RAG project. A recruiter challenged it: *"Instead of answering from policy
documents, can your agent go to the airline website (based on PNR + ticket details) and answer the
latest information?"* This repo is the answer to that challenge.

### The two problems we solve
1. **Generic answers** — policy depends on fare type, route, add-ons, passengers. The booking tells us which rule applies.
2. **Stale answers** — policies change. We fetch live and timestamp every answer.

## Scope (v1)
- **Airline:** IndiGo (goindigo.in) for live policies. Booking data from the mock airline "TestAir" (see below) plus 1–2 real bookings for the live demo.
- **Question types:** baggage allowance, cancellation/change fees (incl. the 48-hour look-in rule), web check-in window.
- Everything else is out of scope until v1 is evaluated.

## Architecture

```
User (PNR + last name + question)
        │
        ▼
Agent (LLM with tool calling)
   ├── get_booking(pnr, last_name, source)  → BookingSource → Booking (pydantic)
   │        sources: MockTestAirSource | ETicketSource | LiveIndiGoSource
   ├── fetch_policy(airline, topic)          → PolicySnapshot (live, cached fallback)
   └── match + answer                        → AgentAnswer (structured, grounded)
```

The agent never knows which BookingSource it is using. All sources return the same `Booking` schema.

## Tech stack
- Python 3.11+
- `playwright` — browser automation (live booking pages, JS-rendered policy pages)
- `httpx` + `trafilatura` (or `beautifulsoup4`) — fetch and clean policy page text
- `fastapi` + `jinja2` — the mock TestAir website
- `pydantic` v2 — all schemas
- `sqlite3` — policy snapshots, mock bookings, eval results
- LLM with tool calling, provider set by `AGENT_PROVIDER`: `google-genai` (Gemini, `GEMINI_MODEL`, current default) or `anthropic` SDK (Claude, `AGENT_MODEL`). Both share the same tools, prompt and evidence checks
- `pdfplumber` — e-ticket PDF parsing
- `streamlit` — demo UI
- `pytest` — tests

## Directory layout

```
airline-agent/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .env.example                # ANTHROPIC_API_KEY, AGENT_MODEL, POLICY_CACHE_TTL_HOURS
├── config/
│   ├── policy_sources.yaml     # topic → official URL (verified manually, never guessed)
│   └── fare_mapping.yaml       # fare codes on booking → policy category names
├── src/airline_agent/
│   ├── schemas.py              # Booking, Flight, Passenger, PolicySnapshot, AgentAnswer
│   ├── booking/
│   │   ├── base.py             # BookingSource protocol
│   │   ├── mock_testair.py     # Playwright against the mock site
│   │   ├── eticket.py          # PDF → Booking
│   │   └── live_indigo.py      # Playwright against the real site
│   ├── policy/
│   │   ├── fetcher.py          # live fetch → clean text → snapshot
│   │   └── store.py            # SQLite snapshot store, TTL cache, hash diff
│   ├── agent/
│   │   ├── tools.py            # tool definitions exposed to the LLM
│   │   ├── prompts.py
│   │   └── run.py              # agent loop
│   └── privacy.py              # PNR/name masking helpers
├── mock_site/                  # TestAir: FastAPI app + templates + seed data
│   ├── app.py
│   ├── seed_bookings.json
│   └── templates/
├── eval/
│   ├── cases.yaml              # question + booking + expected answer + snapshot id
│   ├── run_eval.py
│   └── results/
├── ui/app.py                   # Streamlit demo
├── scripts/
│   └── feasibility_check.py    # day-1 test: does the live Manage Booking page block automation?
└── tests/
```

## Core schemas (keep these stable)

```python
class Flight(BaseModel):
    flight_number: str
    origin: str            # IATA code
    destination: str       # IATA code
    departure: datetime
    is_international: bool

class Passenger(BaseModel):
    type: Literal["adult", "child", "infant"]
    extra_baggage_kg: int = 0

class Booking(BaseModel):
    pnr_masked: str        # e.g. "AB****" — never the raw PNR
    airline: str
    booking_created_at: datetime   # needed for the 48-hour look-in rule
    status: Literal["confirmed", "cancelled"]
    fare_type: str         # raw value from source
    fare_category: str     # normalized via config/fare_mapping.yaml
    cabin: str
    flights: list[Flight]
    passengers: list[Passenger]
    booked_via: Literal["airline_website", "agent", "unknown"]
    source: Literal["mock_testair", "eticket", "live_indigo"]

class PolicySnapshot(BaseModel):
    snapshot_id: str
    airline: str
    topic: Literal["baggage", "cancellation", "web_checkin"]
    url: str
    fetched_at: datetime
    content_hash: str
    text: str
    from_cache: bool

class AgentAnswer(BaseModel):
    answer: str
    evidence_quotes: list[str]     # exact text from the snapshot that supports the answer
    source_urls: list[str]
    checked_at: datetime
    booking_source: str
    policy_from_cache: bool
    confidence: Literal["high", "medium", "low"]
    could_not_confirm: bool
```

## Booking sources

### 1. Mock TestAir site (main evaluation bed)
- Fictional airline with its **own name and design**. Do not copy IndiGo's or any real airline's branding or layout.
- Manage Booking page: form (PNR + last name) → booking details page.
- Also expose the booking as JSON from an internal endpoint the page calls (so we practise network-response capture, same as the live site).
- 40–50 seeded bookings covering: saver/flexi/premium fares, extra baggage bought, infant, child, connecting flights, international, cancelled booking, booking made <48h ago, booking via agent.
- **Failure switches** (query param or env var): `captcha=1`, `layout=v2` (changed HTML), `slow=5` (seconds), `error=500`. Used to test robustness.
- TestAir bookings map to **IndiGo's real live policies** for evaluation (fake bookings, real rules). Document this clearly in the README.

### 2. E-ticket PDF (fallback)
- User uploads a ticket PDF → extract `Booking`. Test PDFs are generated from the TestAir seed data and labelled as test documents.

### 3. Live IndiGo (real-world proof)
- Only used with the developer's own bookings.
- Prefer capturing the site's JSON network responses over scraping DOM selectors.
- Keep selectors in config, not code.
- Run in a visible (headed) browser with a persistent profile, human-like pacing, very low volume.

## Policy fetcher
- URLs come only from `config/policy_sources.yaml`. **Never guess or construct policy URLs** — they are verified manually by opening the site.
- Fetch → clean main text → store snapshot (text, URL, timestamp, sha256 hash).
- Cache with TTL (default a few hours, `POLICY_CACHE_TTL_HOURS`). If live fetch fails, use the latest snapshot and set `from_cache=True`.
- When the hash differs from the previous snapshot, log a "policy changed" event.

## Agent rules (enforce in prompts and code)
1. Answer **only** from fetched policy text and booking data. No answers from the model's own memory of airline rules.
2. Every claim must be backed by an `evidence_quotes` entry that appears verbatim in the snapshot text. Validate this in code; if a quote isn't found, drop it and lower confidence.
3. If the rule for this booking can't be found or the match is ambiguous, set `could_not_confirm=True`, say so plainly, and give the official URL.
4. Always state which booking source and whether the policy came from cache.
5. Use `booking_created_at`, `booked_via`, and departure date when answering cancellation questions (48-hour look-in rule conditions).

## Non-negotiable rules
- **Privacy:** never log, print, or store raw PNRs or passenger names. Use `privacy.mask_pnr()` everywhere. Live-booking fixtures must be redacted before saving.
- **No CAPTCHA bypassing.** If a CAPTCHA appears, pause and hand control to the human (headed browser), then continue. Never integrate CAPTCHA-solving services.
- **Respect the sites:** low request volume, honour robots.txt for policy pages, live booking access only for our own bookings.
- **No real-airline impersonation** in the mock site or test PDFs.
- **Never use PNRs found online** (e.g. boarding-pass photos).

## Evaluation
Test set in `eval/cases.yaml`: ~30–50 questions across TestAir bookings × 3 topics, including tricky cases. Each case pins the `snapshot_id` used to write the expected answer.

Metrics (written to `eval/results/`):
| Metric | Definition |
|---|---|
| Booking extraction accuracy | field-level match vs seed data |
| Policy retrieval accuracy | correct topic page/section used |
| Answer accuracy | matches expected answer for the pinned snapshot (LLM judge + manual spot check) |
| Groundedness | % claims backed by verified evidence quotes |
| Failure handling | correct behaviour under each failure switch |
| Live success rate | live fetch succeeded vs fell back |
| Latency | p50 / p95 seconds per question |

## Build phases (update status as work progresses)
- [x] **Phase 0 — Feasibility check:** (2026-10-18: lookup page /edit-booking.html verified; CDN refuses site JS to automated browsers → BLOCKED. Decision: assisted capture, no evasion) `scripts/feasibility_check.py` opens the IndiGo Manage Booking page with Playwright, submits a fake PNR, and reports: "not found" message (automation OK) vs CAPTCHA/blocked. Record the result in README.
- [x] **Phase 1 — Policy fetcher:** (httpx → fresh headed browser per page; IndiGo stalls plain HTTP and blocks headless) verified URLs in config, fetch, clean, snapshot store, cache, hash diff.
- [x] **Phase 2 — Mock TestAir site:** FastAPI app, seed bookings, JSON endpoint, failure switches.
- [~] **Phase 3 — Booking sources:** (mock + e-ticket done, 100% extraction; live IndiGo = assisted capture via CDP, field_paths filled from first own-booking capture) `BookingSource` protocol, mock source, e-ticket parser, live IndiGo source (attempt).
- [x] **Phase 4 — Agent:** (Gemini + Claude loops; hidden `working` field; code guards for special fares, unlisted destinations, codeshare-only fees) tools, prompt, loop, structured `AgentAnswer`, evidence validation.
- [x] **Phase 5 — Evaluation:** (2026-10-19: 86.4% AI-graded on free gemini-3.5-flash-lite, could-not-confirm 100%; resumable runner with judge fallback. Next: full-size model run) cases, runner, metrics report.
- [x] **Phase 6 — Demo:** Streamlit UI with live progress steps; README with results, limitations, and the "real products use airline APIs/GDS" note.

## Working conventions
- Build one phase at a time; each phase ends with passing tests.
- Type hints everywhere; pydantic for any data crossing a boundary.
- Tests must not hit live airline sites. Use the mock site and recorded (redacted) fixtures. Live tests are marked `@pytest.mark.live` and skipped by default.
- Keep secrets in `.env` (never committed).

## Commands (fill in as they are created)
```bash
pip install -e ".[dev]" && playwright install chromium
uvicorn mock_site.app:app --reload --port 8001   # run TestAir
python mock_site/generate_seed.py                 # regenerate seed bookings
python scripts/feasibility_check.py [--pause]     # Phase 0
python scripts/refresh_policies.py [--pin]        # live policy fetch; --pin writes eval/fixtures/snapshots.json
python scripts/make_test_etickets.py              # test e-ticket PDFs
pytest                                            # unit tests (no live calls)
pytest -m browser                                 # Playwright vs mock site
pytest -m live                                    # live tests, own bookings only
python eval/run_eval.py [--llm]                   # evaluation report
streamlit run ui/app.py                           # demo
```