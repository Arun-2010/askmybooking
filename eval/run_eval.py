"""Evaluation runner. Writes eval/results/report_<timestamp>.{json,md} and latest.md.

    python eval/run_eval.py                 # no-LLM metrics: extraction, source failure handling, live policy rate
    python eval/run_eval.py --llm           # + agent answers on eval/cases.yaml (needs ANTHROPIC_API_KEY)
    python eval/run_eval.py --llm --cases B01,C01 --no-judge

The mock site is started automatically if TESTAIR_BASE_URL isn't reachable.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from airline_agent import config  # noqa: E402
from airline_agent.booking.base import BookingError, map_testair_json  # noqa: E402
from airline_agent.booking.eticket import ETicketSource  # noqa: E402
from airline_agent.booking.mock_testair import MockTestAirSource  # noqa: E402
from airline_agent.policy.fetcher import PolicyFetchError  # noqa: E402
from airline_agent.policy.store import SnapshotStore  # noqa: E402
from airline_agent.privacy import install_log_redaction  # noqa: E402
from airline_agent.schemas import PolicySnapshot  # noqa: E402
from mock_site.app import SEED, materialise  # noqa: E402

RESULTS = ROOT / "eval" / "results"
FIELDS = ["pnr_masked", "airline", "booking_created_at", "status", "fare_type", "fare_category", "cabin",
          "flights", "passengers", "booked_via"]
# Opus 5.5 list prices per MTok, for a rough cost line in the report.
PRICE_IN, PRICE_OUT = 4.0, 20.0


# --------------------------------------------------------------------------- helpers
def ensure_mock_site() -> subprocess.Popen | None:
    url = config.testair_base_url()
    try:
        if httpx.get(url + "/healthz", timeout=2).status_code == 200:
            return None
    except Exception:
        pass
    port = url.rsplit(":", 1)[-1]
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_site.app:app", "--port", port,
                             "--log-level", "warning"], cwd=str(ROOT))
    for _ in range(50):
        try:
            if httpx.get(url + "/healthz", timeout=1).status_code == 200:
                return proc
        except Exception:
            time.sleep(0.2)
    raise SystemExit("mock site did not start")


def field_matches(got: Any, want: Any, key: str) -> bool:
    if key == "booking_created_at":
        return abs((got - want).total_seconds()) < 300
    if key == "flights":
        g = [{k: v for k, v in f.items()} for f in got]
        w = [{k: v for k, v in f.items()} for f in want]
        return g == w
    return got == want


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def p(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


# --------------------------------------------------------------------------- extraction
def eval_extraction(limit: int | None) -> dict:
    seeds = SEED[:limit] if limit else SEED
    out: dict[str, Any] = {}
    # 1) website (Playwright, network capture)
    src = MockTestAirSource()
    ok = total = 0
    per_field = {f: 0 for f in FIELDS}
    failures = []
    for b in seeds:
        want = map_testair_json(materialise(b)).model_dump()
        try:
            got = src.get_booking(b["pnr"], b["last_name"]).model_dump()
        except BookingError as e:
            failures.append({"id": b["id"], "error": type(e).__name__})
            total += len(FIELDS)
            continue
        for f in FIELDS:
            m = field_matches(got[f], want[f], f)
            ok += m
            per_field[f] += m
            total += 1
    out["website"] = {"bookings": len(seeds), "field_accuracy": ok / total, "failures": failures,
                      "per_field": {k: v / len(seeds) for k, v in per_field.items()}}
    # 2) e-ticket PDFs
    from make_test_etickets import render

    now = datetime.now(timezone.utc).replace(microsecond=0)
    ok = total = 0
    failures = []
    with tempfile.TemporaryDirectory() as td:
        for b in seeds:
            data = materialise(b, now)
            pdf = Path(td) / f"{b['id']}.pdf"
            render(data, pdf)
            want = map_testair_json(data, source="eticket").model_dump()
            try:
                got = ETicketSource(pdf).get_booking(b["pnr"], b["last_name"]).model_dump()
            except BookingError as e:
                failures.append({"id": b["id"], "error": type(e).__name__})
                total += len(FIELDS)
                continue
            for f in FIELDS:
                ok += field_matches(got[f], want[f], f)
                total += 1
    out["eticket"] = {"bookings": len(seeds), "field_accuracy": ok / total, "failures": failures}
    return out


# --------------------------------------------------------------------------- source failure handling
def eval_source_failures() -> list[dict]:
    b = SEED[1]
    checks = [
        ({"captcha": "1"}, "CaptchaRequired", "headless run must stop and ask for a human, never bypass"),
        ({"error": "500"}, "SourceUnavailable", "server error surfaces as SourceUnavailable"),
        ({"layout": "v2"}, "ok", "changed HTML still extracted via network capture"),
        ({"slow": "5"}, "ok", "slow response tolerated"),
        ({"pnr": "TZZZZ9"}, "BookingNotFound", "unknown PNR reported as not found"),
    ]
    rows = []
    for sw, expected, why in checks:
        pnr = sw.pop("pnr", b["pnr"])
        t0 = time.monotonic()
        try:
            MockTestAirSource(switches=sw).get_booking(pnr, b["last_name"])
            got = "ok"
        except BookingError as e:
            got = type(e).__name__
        rows.append({"switch": sw or {"pnr": "unknown"}, "expected": expected, "got": got, "pass": got == expected,
                     "seconds": round(time.monotonic() - t0, 1), "why": why})
    return rows


# --------------------------------------------------------------------------- live policy rate
def eval_live_policy() -> dict:
    store = SnapshotStore(config.policy_db_path())
    ok = len(store.events("fetch_ok"))
    failed = len(store.events("fetch_failed"))
    changed = len(store.events("policy_changed"))
    return {"live_fetch_ok": ok, "live_fetch_failed": failed,
            "live_success_rate": ok / (ok + failed) if ok + failed else None, "policy_changed_events": changed}


# --------------------------------------------------------------------------- agent answers
class PinnedFetcher:
    def __init__(self, snaps: dict[str, PolicySnapshot], unavailable: bool = False):
        self.snaps, self.unavailable = snaps, unavailable

    def fetch_policy(self, airline: str, topic: str) -> list[PolicySnapshot]:
        if self.unavailable:
            raise PolicyFetchError("simulated: live fetch failed and no cached snapshot")
        return [s for s in self.snaps.values() if s.topic == topic]


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"correct": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["correct", "reason"], "additionalProperties": False,
}


def judge(client, question: str, expected: str, actual: str, provider: str = "anthropic",
          model: str | None = None) -> dict:
    prompt = (
        "You grade an airline-policy assistant. Compare the ACTUAL answer with the REFERENCE answer.\n"
        "Mark correct=true if the actual answer reaches the same conclusion for this booking and every number it "
        "states that also appears in the reference matches. Extra correct detail is fine. Mark false if it states a "
        "wrong number, the wrong conclusion, contradicts itself (e.g. opens with 'yes' and then explains why not), "
        "names the wrong city or airport, or confidently answers where the reference says it can't be confirmed. "
        "Ignore the final 'Booking read from ... Policy ...' provenance line.\n\n"
        f"QUESTION: {question}\n\nREFERENCE: {expected}\n\nACTUAL: {actual}"
    )
    if provider == "gemini":
        from google.genai import types

        from airline_agent.agent.gemini_loop import _clean_schema, pace

        pace()
        resp = client.models.generate_content(
            model=model or config.judge_model("gemini"), contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json",
                                               response_json_schema=_clean_schema(JUDGE_SCHEMA)),
        )
        return json.loads(resp.text)
    resp = client.messages.create(
        model=model or config.judge_model("anthropic"), max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def judge_models(provider: str) -> list[str]:
    """JUDGE_MODELS (comma list) lets grading move to another model when one hits its daily quota."""
    listed = [m.strip() for m in (os.getenv("JUDGE_MODELS") or "").split(",") if m.strip()]
    return listed or [config.judge_model(provider)]


_exhausted: set[str] = set()


def judge_with_fallback(client, provider: str, question: str, expected: str, actual: str) -> dict:
    last = None
    for m in judge_models(provider):
        if m in _exhausted:
            continue
        for attempt in range(3):
            try:
                out = judge(client, question, expected, actual, provider, model=m)
                out["judge_model"] = m
                return out
            except Exception as e:
                last = e
                if "429" in str(e) and "PerDay" in str(e):
                    _exhausted.add(m)  # daily quota gone: next model
                    break
                time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"all judge models failed: {last!r}"[:300])


def includes_ok(answer: str, groups: list[list[str]]) -> bool:
    a = " ".join(answer.lower().split())
    return all(any(alt.lower() in a for alt in g) for g in groups)


def eval_answers(case_ids: set[str] | None, use_judge: bool, resume: bool = False) -> dict:
    from airline_agent.agent.run import run_agent

    spec = yaml.safe_load((ROOT / "eval" / "cases.yaml").read_text(encoding="utf-8"))
    pinned = {d["snapshot_id"]: PolicySnapshot(**d) for d in
              json.loads((ROOT / "eval" / "fixtures" / "snapshots.json").read_text(encoding="utf-8"))["snapshots"]}
    seeds = {b["id"]: b for b in SEED}
    provider = config.agent_provider()
    if provider == "gemini":
        from google import genai

        client = genai.Client()
    else:
        import anthropic

        client = anthropic.Anthropic()
    print(f"  provider={provider} model={config.agent_model(provider)}")
    def apply_judge(row: dict, c: dict) -> None:
        try:
            j = judge_with_fallback(client, provider, c["question"], c["expected_answer"], row["answer"])
            row["judge_correct"], row["judge_reason"], row["judge_model"] = j["correct"], j["reason"], j["judge_model"]
        except Exception as e:  # grading failure is recorded, never counted as right or wrong
            row["judge_correct"], row["judge_reason"] = None, f"judge error: {e!r}"[:200]

    def run_case(c: dict) -> dict:
        b = seeds[c["booking"]]
        src = MockTestAirSource(switches={k: str(v) for k, v in (c.get("switches") or {}).items()})
        fetcher = PinnedFetcher(pinned, unavailable=c.get("policy_mode") == "unavailable")
        print(f"  {c['id']} ...", end="", flush=True)
        res = run_agent(c["question"], b["pnr"], b["last_name"], src, fetcher, client=client, provider=provider)
        a = res.answer
        row = {
            "id": c["id"], "topic": c["topic"], "tags": c.get("tags", []),
            "answer": a.answer, "confidence": a.confidence, "could_not_confirm": a.could_not_confirm,
            "topics_fetched": res.topics_fetched, "retrieval_ok": c["topic"] in res.topics_fetched
            or c.get("policy_mode") == "unavailable" or bool(c.get("switches", {}).get("error"))
            or bool(c.get("switches", {}).get("captcha")),
            "verified_quotes": len(a.evidence_quotes), "dropped_quotes": len(a.dropped_quotes),
            "includes_ok": includes_ok(a.answer, c.get("must_include") or []),
            "cnc_expected": c.get("expect_could_not_confirm"),
            "latency_s": res.latency_s, "usage": res.usage, "booking_error": res.booking_error,
            "infra_error": res.infra_error,
        }
        if res.infra_error:
            row["correct"] = None
            print(" ERROR (AI service unavailable; not scored)")
            return row
        exp = c.get("expect_could_not_confirm")
        row["cnc_ok"] = exp is None or exp == a.could_not_confirm
        if use_judge:
            apply_judge(row, c)
        row["correct"] = row["includes_ok"] and row["cnc_ok"] and row.get("judge_correct", True) is not False
        print(" ok" if row["correct"] else " WRONG", f"({row['latency_s']}s)")
        return row

    # Progress is saved after every case, so a dropped connection never loses finished work.
    # A rerun with --resume only runs cases that are missing or hit an AI-service error.
    progress_path = RESULTS / f"progress_{provider}_{config.agent_model(provider)}.json"
    done: dict[str, dict] = {}
    if resume and progress_path.exists():
        done = {k: v for k, v in json.loads(progress_path.read_text(encoding="utf-8")).items()
                if not v.get("infra_error")}
        print(f"  resuming: {len(done)} case(s) already answered, skipping them")
    RESULTS.mkdir(parents=True, exist_ok=True)

    selected = [c for c in spec["cases"] if not case_ids or c["id"] in case_ids]
    if use_judge:
        ungraded = [c for c in selected if c["id"] in done and done[c["id"]].get("judge_correct") is None]
        if ungraded:
            print(f"  grading {len(ungraded)} saved answer(s) ...")
        for c in ungraded:
            row = done[c["id"]]
            apply_judge(row, c)
            row["correct"] = row["includes_ok"] and row["cnc_ok"] and row.get("judge_correct", True) is not False
            print(f"  {c['id']} judged: {'ok' if row['correct'] else 'WRONG'}")
            progress_path.write_text(json.dumps(done, indent=1, default=str), encoding="utf-8")
    streak = 0
    for c in selected:
        if c["id"] in done:
            continue
        row = run_case(c)
        done[c["id"]] = row
        progress_path.write_text(json.dumps(done, indent=1, default=str), encoding="utf-8")
        streak = streak + 1 if row["infra_error"] else 0
        if streak >= 3:
            print("  3 AI-service errors in a row (network or quota). Stopping; finished cases are saved.\n"
                  "  When the connection is back:  python eval/run_eval.py --llm --answers-only --resume")
            break
    rows = [done[c["id"]] for c in selected if c["id"] in done]
    missing = [c["id"] for c in selected if c["id"] not in done or done[c["id"]].get("infra_error")]
    summary = summarize_answers(rows)
    summary["not_run_yet"] = missing
    return summary


def summarize_answers(all_rows: list[dict]) -> dict:
    # Cases where the AI service itself failed (quota, network) are reported, not scored.
    rows = [r for r in all_rows if not r.get("infra_error")]
    n = len(rows)
    lat = [r["latency_s"] for r in rows]
    vq = sum(r["verified_quotes"] for r in rows)
    dq = sum(r["dropped_quotes"] for r in rows)
    tin = sum(r["usage"].get("input_tokens", 0) for r in all_rows)
    tout = sum(r["usage"].get("output_tokens", 0) for r in all_rows)
    by_topic: dict[str, list[bool]] = {}
    for r in rows:
        by_topic.setdefault(r["topic"] if "failure" not in r["tags"] else "failure", []).append(r["correct"])
    judged = [r for r in rows if r.get("judge_correct") is not None]
    return {
        "cases": len(all_rows),
        "scored": n,
        "not_scored_ai_errors": len(all_rows) - n,
        "answer_accuracy": sum(r["correct"] for r in rows) / n if n else None,
        "accuracy_by_group": {k: sum(v) / len(v) for k, v in by_topic.items()},
        "judge_accuracy": sum(r["judge_correct"] for r in judged) / len(judged) if judged else None,
        "must_include_rate": sum(r["includes_ok"] for r in rows) / n if n else None,
        "could_not_confirm_correct": sum(r["cnc_ok"] for r in rows) / n if n else None,
        "policy_retrieval_accuracy": sum(r["retrieval_ok"] for r in rows) / n if n else None,
        "groundedness": vq / (vq + dq) if vq + dq else None,
        "answers_with_verified_evidence": sum(1 for r in rows if r["verified_quotes"]) / n if n else None,
        "latency_p50_s": p(lat, 0.5), "latency_p95_s": p(lat, 0.95),
        "tokens_in": tin, "tokens_out": tout, "approx_cost_usd": (round(tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT, 2)
                            if config.agent_provider() == "anthropic" else None),
        "rows": all_rows,
    }


# --------------------------------------------------------------------------- report
def write_report(report: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (RESULTS / f"report_{ts}.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md = ["# Evaluation report", "", f"Run: {report['run_at']}  ", f"Model: `{report['model']}`", ""]
    ex = report["extraction"]
    md += ["## Booking extraction accuracy (field-level vs seed data)", "",
           "| Source | Bookings | Field accuracy | Failures |", "|---|---|---|---|",
           f"| Website (Playwright, network capture) | {ex['website']['bookings']} | {pct(ex['website']['field_accuracy'])} | {len(ex['website']['failures'])} |",
           f"| E-ticket PDF | {ex['eticket']['bookings']} | {pct(ex['eticket']['field_accuracy'])} | {len(ex['eticket']['failures'])} |", ""]
    md += ["## Failure handling (booking source)", "", "| Switch | Expected | Got | Pass | Seconds |", "|---|---|---|---|---|"]
    for r in report["source_failures"]:
        md.append(f"| `{json.dumps(r['switch'])}` | {r['expected']} | {r['got']} | {'✅' if r['pass'] else '❌'} | {r['seconds']} |")
    lp = report["live_policy"]
    rate = pct(lp["live_success_rate"]) if lp["live_success_rate"] is not None else "n/a"
    md += ["", "## Live policy fetch (from snapshot-store event log)", "",
           f"- Live fetches succeeded: {lp['live_fetch_ok']}, failed (fell back / errored): {lp['live_fetch_failed']} → **live success rate {rate}**",
           f"- Policy-changed events recorded: {lp['policy_changed_events']}", ""]
    ans = report.get("answers")
    if ans:
        md += ["## Agent answers (pinned snapshots)", "",
               "| Metric | Value |", "|---|---|",
               f"| Cases (scored / not scored: AI-service errors) | {ans['cases']} ({ans['scored']} / {ans['not_scored_ai_errors']}) |",
               f"| Not run yet (rerun with --resume) | {', '.join(ans.get('not_run_yet') or []) or 'none'} |",
               f"| Answer accuracy (must-include ∧ could-not-confirm ∧ judge) | {pct(ans['answer_accuracy'])} |",
               f"| LLM-judge accuracy | {pct(ans['judge_accuracy']) if ans['judge_accuracy'] is not None else 'not run'} |",
               f"| Correct 'could not confirm' behaviour | {pct(ans['could_not_confirm_correct'])} |",
               f"| Policy retrieval accuracy (right topic fetched) | {pct(ans['policy_retrieval_accuracy'])} |",
               f"| Groundedness (verified quotes / all quotes) | {pct(ans['groundedness']) if ans['groundedness'] is not None else 'n/a'} |",
               f"| Latency p50 / p95 | {ans['latency_p50_s']}s / {ans['latency_p95_s']}s |",
               f"| Tokens in / out (≈ cost) | {ans['tokens_in']:,} / {ans['tokens_out']:,} ({'≈ $' + str(ans['approx_cost_usd']) if ans['approx_cost_usd'] is not None else 'Gemini free tier'}) |", "",
               "By group: " + ", ".join(f"{k} {pct(v)}" for k, v in ans["accuracy_by_group"].items()), "",
               "| Case | Correct | Could not confirm | Conf. | Quotes ✓/✗ | Note |", "|---|---|---|---|---|---|"]
        for r in ans["rows"]:
            note = (r.get("judge_reason") or "").replace("|", "/").replace("\n", " ")[:120]
            mark = "⚠️ error" if r.get("infra_error") else ("✅" if r["correct"] else "❌")
            md.append(f"| {r['id']} | {mark} | {r['could_not_confirm']} | {r['confidence']} | "
                      f"{r['verified_quotes']}/{r['dropped_quotes']} | {note} |")
    else:
        md += ["## Agent answers", "", "_Not run in this report (run with `--llm`; needs `ANTHROPIC_API_KEY`)._"]
    text = "\n".join(md) + "\n"
    (RESULTS / f"report_{ts}.md").write_text(text, encoding="utf-8")
    (RESULTS / "latest.md").write_text(text, encoding="utf-8")
    return RESULTS / f"report_{ts}.md"


def main() -> None:
    install_log_redaction()
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="run agent answer cases (calls the Claude API)")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--cases", help="comma-separated case ids")
    ap.add_argument("--limit", type=int, help="limit bookings for extraction eval")
    ap.add_argument("--answers-only", action="store_true", help="skip extraction/failure checks (reuse last report's)")
    ap.add_argument("--resume", action="store_true", help="skip cases already answered in the saved progress file")
    args = ap.parse_args()
    if args.llm and not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("ANTHROPIC_AUTH_TOKEN"):
        print("note: ANTHROPIC_API_KEY not set; relying on other SDK credential sources")

    proc = ensure_mock_site()
    try:
        report: dict[str, Any] = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "model": f"{config.agent_provider()}:{config.agent_model()}"}
        last = sorted(RESULTS.glob("report_*.json"))
        if args.answers_only and last:
            prev = json.loads(last[-1].read_text(encoding="utf-8"))
            report["extraction"], report["source_failures"] = prev["extraction"], prev["source_failures"]
        else:
            print("extraction ..."); report["extraction"] = eval_extraction(args.limit)
            print("source failure handling ..."); report["source_failures"] = eval_source_failures()
        report["live_policy"] = eval_live_policy()
        if args.llm:
            print("agent answers ...")
            ids = set(args.cases.split(",")) if args.cases else None
            report["answers"] = eval_answers(ids, use_judge=not args.no_judge, resume=args.resume)
        path = write_report(report)
        print(f"\nreport: {path.relative_to(ROOT)}\n")
        print((RESULTS / "latest.md").read_text(encoding="utf-8"))
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
