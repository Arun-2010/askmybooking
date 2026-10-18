# Evaluation report

Run: 2026-10-18T08:51:24+00:00  
Model: `claude-opus-5-5`

## Booking extraction accuracy (field-level vs seed data)

| Source | Bookings | Field accuracy | Failures |
|---|---|---|---|
| Website (Playwright, network capture) | 45 | 100.0% | 0 |
| E-ticket PDF | 45 | 100.0% | 0 |

## Failure handling (booking source)

| Switch | Expected | Got | Pass | Seconds |
|---|---|---|---|---|
| `{"captcha": "1"}` | CaptchaRequired | CaptchaRequired | ✅ | 1.1 |
| `{"error": "500"}` | SourceUnavailable | SourceUnavailable | ✅ | 1.1 |
| `{"layout": "v2"}` | ok | ok | ✅ | 1.0 |
| `{"slow": "5"}` | ok | ok | ✅ | 6.7 |
| `{"pnr": "unknown"}` | BookingNotFound | BookingNotFound | ✅ | 2.5 |

## Live policy fetch (from snapshot-store event log)

- Live fetches succeeded: 4, failed (fell back / errored): 0 → **live success rate 100.0%**
- Policy-changed events recorded: 0

## Agent answers

_Not run in this report (run with `--llm`; needs `ANTHROPIC_API_KEY`)._
