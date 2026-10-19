# Evaluation report

Run: 2026-10-19T06:01:49+00:00  
Model: `gemini:gemini-3.5-flash-lite`

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

- Live fetches succeeded: 5, failed (fell back / errored): 0 → **live success rate 100.0%**
- Policy-changed events recorded: 0

## Agent answers (pinned snapshots)

| Metric | Value |
|---|---|
| Cases (scored / not scored: AI-service errors) | 44 (44 / 0) |
| Not run yet (rerun with --resume) | none |
| Answer accuracy (must-include ∧ could-not-confirm ∧ judge) | 86.4% |
| LLM-judge accuracy | 86.4% |
| Correct 'could not confirm' behaviour | 100.0% |
| Policy retrieval accuracy (right topic fetched) | 100.0% |
| Groundedness (verified quotes / all quotes) | 95.9% |
| Latency p50 / p95 | 15.4s / 45.42s |
| Tokens in / out (≈ cost) | 351,965 / 19,299 (Gemini free tier) |

By group: baggage 94.1%, cancellation 84.6%, web_checkin 66.7%, failure 100.0%

| Case | Correct | Could not confirm | Conf. | Quotes ✓/✗ | Note |
|---|---|---|---|---|---|
| B01 | ✅ | False | high | 1/0 | The actual answer correctly identifies the 15 kg check-in baggage allowance for the domestic flight. While it does not m |
| B02 | ✅ | False | high | 1/0 | The actual answer reaches the same conclusion and includes the correct breakdown of the baggage allowance (15 kg domesti |
| B03 | ✅ | False | high | 1/0 | The actual answer correctly identifies both the free allowance and the pre-purchased weight, correctly summing them to 3 |
| B04 | ✅ | False | high | 2/0 | The actual answer correctly includes all baggage specifications (7 kg, 115 cm, NIL check-in, and one stroller/pram) as s |
| B05 | ✅ | False | medium | 1/1 | The actual answer correctly identifies the 30 kg baggage allowance for the adult and the NIL allowance for the infant, w |
| B06 | ✅ | False | high | 1/0 | The actual answer correctly identifies the 30kg baggage allowance and correctly maps the airport codes HYD and DXB to th |
| B07 | ✅ | False | high | 1/0 | The actual answer correctly identifies the 30kg allowance for the Mumbai to Singapore route, matching the reference. |
| B08 | ✅ | False | high | 2/0 | The actual answer correctly identifies the checked baggage allowance as 20 kg per person and includes additional helpful |
| B09 | ❌ | True | low | 1/0 | The actual answer fails to state the base baggage allowance of 20 kg for Kathmandu, which was provided in the reference  |
| B10 | ✅ | True | low | 2/0 | The actual answer correctly identifies that the information cannot be confirmed and avoids guessing, aligning with the r |
| B11 | ✅ | True | low | 1/0 | The actual answer correctly identifies that the information cannot be confirmed and refers the user to the official webs |
| B12 | ✅ | True | low | 2/0 | The actual answer correctly concludes that the baggage allowance for London cannot be confirmed based on the provided po |
| B13 | ✅ | False | high | 1/0 | The actual answer accurately identifies the standard allowance for all passengers and correctly adds the extra 10kg to t |
| B14 | ✅ | False | high | 3/0 | The actual answer correctly identifies the check-in baggage allowance of 15 kg per person for the domestic flight as spe |
| B15 | ✅ | True | low | 1/0 | The actual answer correctly identifies that the business/Stretch fare baggage allowance cannot be confirmed from the pol |
| B16 | ✅ | False | high | 3/0 | The actual answer correctly identifies that the booking is cancelled and provides the correct baggage allowance of 15 kg |
| B17 | ✅ | False | high | 1/0 | The actual answer correctly confirms the policy and includes all the correct weight and dimension constraints as specifi |
| C01 | ✅ | False | high | 2/0 | The actual answer reaches the same conclusion, includes all necessary details, and accurately reflects the provided numb |
| C02 | ❌ | False | high | 2/0 | The actual answer incorrectly states that the 48-hour look-in window does not apply, contradicting the reference which c |
| C03 | ✅ | False | high | 1/0 | The actual answer correctly identifies that the cancellation policy does not apply because the booking was made through  |
| C04 | ✅ | False | high | 2/0 | The actual answer correctly concludes that the cancellation cannot be made free of charge and provides accurate, consist |
| C05 | ✅ | False | high | 2/0 | The actual answer reaches the same correct conclusion and provides the matching number of 60 hours as found in the refer |
| C06 | ✅ | False | high | 2/0 | The actual answer reaches the same correct conclusion, matches the booking time and flight distance criteria, and provid |
| C07 | ✅ | False | high | 2/0 | The actual answer correctly concludes that the cancellation is not free. It correctly identifies the 15-day exclusion wi |
| C08 | ❌ | False | high | 2/0 | The actual answer is contradictory and confusing; it opens by stating the user cannot cancel, then concludes the user *d |
| C09 | ✅ | True | low | 2/0 | The actual answer correctly identifies that the cancellation fee cannot be confirmed from the provided policy pages, con |
| C10 | ✅ | True | low | 2/0 | The actual answer correctly identifies that the fee cannot be confirmed based on the provided policy and points the user |
| C11 | ✅ | False | high | 2/0 | The actual answer correctly identifies that the booking is already cancelled, matching the reference. The extra details  |
| C12 | ✅ | False | high | 3/0 | The actual answer correctly identifies the 4-hour window for international flight changes and cancellations. While it ad |
| C13 | ✅ | False | high | 2/0 | The actual answer correctly identifies that there is no change fee due to the 48-hour window and specifies that the fare |
| W01 | ✅ | False | high | 1/0 | The actual answer correctly identifies the check-in window (48 hours to 60 minutes) and matches the reference provided. |
| W02 | ✅ | False | high | 1/0 | The actual answer accurately reflects the policy provided in the reference and matches the numerical values for the chec |
| W03 | ✅ | False | high | 1/0 | The actual answer correctly concludes that online check-in is available and provides the matching policy window of 48 ho |
| W04 | ❌ | False | high | 1/0 | The actual answer incorrectly states that the user can check in now and that 55.2 hours is within the 24-hour window, wh |
| W05 | ❌ | False | high | 2/0 | The actual answer incorrectly states that the user cannot check in for both flights, contradicting the reference which c |
| W06 | ✅ | False | high | 1/0 | The actual answer correctly identifies the check-in windows for both legs of the trip and matches the numbers provided i |
| W07 | ✅ | False | high | 1/0 | The actual answer correctly identifies the 60-minute cutoff for domestic flights and includes the context of the flight  |
| W08 | ✅ | False | high | 1/0 | The actual answer correctly identifies the check-in closing time of 75 minutes and accurately addresses the flight to Mu |
| W09 | ❌ | False | high | 1/0 | The actual answer fails to correctly state the conclusion that there is nothing to check in for due to the cancellation, |
| F01 | ✅ | True | low | 7/1 | The model correctly identified that it could not retrieve specific booking details due to a system error and provided th |
| F02 | ✅ | True | medium | 0/1 | The actual answer correctly identifies that the booking could not be read due to a human verification requirement and di |
| F03 | ✅ | False | high | 1/0 | The actual answer matches the reference weight of 15 kg and correctly identifies the flight as domestic (BOM to BLR). |
| F04 | ✅ | False | high | 1/0 | The actual answer correctly identifies the check-in baggage allowance as 30kg, which matches the reference. |
| F05 | ✅ | True | low | 0/0 | The actual answer correctly identifies that it cannot provide specific baggage information due to a technical issue and  |
