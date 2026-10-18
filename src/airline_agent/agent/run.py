"""Agent loop: LLM with tool calling -> structured, evidence-checked AgentAnswer.

    python -m airline_agent.agent.run "PNR T3K9QA, last name Testwala. How much check-in baggage can I take?"
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import anthropic

from airline_agent import config
from airline_agent.agent.evidence import lower, validate_evidence
from airline_agent.agent.prompts import ANSWER_SCHEMA, SYSTEM_PROMPT
from airline_agent.agent.tools import TOOL_DEFS, ToolContext, execute_tool
from airline_agent.booking.base import BookingSource
from airline_agent.policy.fetcher import PolicyFetcher, topic_urls
from airline_agent.privacy import mask_pnr, redact_text
from airline_agent.schemas import AgentAnswer

log = logging.getLogger(__name__)

MAX_TURNS = 8
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class RunResult:
    answer: AgentAnswer
    tool_calls: list[dict[str, Any]]
    topics_fetched: list[str]
    latency_s: float
    usage: dict[str, int] = field(default_factory=dict)
    model_output: dict[str, Any] | None = None
    booking_error: str | None = None
    # True when the AI service itself failed (quota, network, 5xx): not a judgement on the answer.
    infra_error: bool = False


INFRA_NOTES = ("The AI service is rate-limited", "Could not reach the AI service", "The AI service returned an error")


def _request(client: Any, model: str, messages: list[dict[str, Any]], effort: str) -> Any:
    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=TOOL_DEFS,
        messages=messages,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
        cache_control={"type": "ephemeral"},
    )
    # Server-side refusal fallback (Claude API only; route by refusal category).
    if model.startswith("claude-") and not model.startswith("claude-haiku"):
        kwargs["betas"] = [FALLBACK_BETA]
        kwargs["fallbacks"] = "default"
    return client.beta.messages.create(**kwargs)


def _anthropic_loop(
    client: Any, model: str, effort: str, ctx: ToolContext, user_text: str
) -> tuple[dict[str, Any] | None, str | None, dict[str, int]]:
    """Claude tool-use loop. Returns (final JSON output or None, failure note, token usage)."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_text}]
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
    output: dict[str, Any] | None = None
    note: str | None = None

    for _turn in range(MAX_TURNS):
        ctx.step("llm_call", turn=_turn)
        try:
            resp = _request(client, model, messages, effort)
        except anthropic.RateLimitError:
            note = "The AI service is rate-limited right now; please retry shortly."
            break
        except anthropic.APIStatusError as e:
            note = f"The AI service returned an error (HTTP {e.status_code})."
            break
        except anthropic.APIConnectionError:
            note = "Could not reach the AI service."
            break
        _add_usage(usage, getattr(resp, "usage", None))

        if resp.stop_reason == "refusal":
            note = "The model declined to answer this request."
            break
        if resp.stop_reason == "max_tokens":
            note = "The answer was cut off before it was complete."
            break
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue
        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if resp.stop_reason == "tool_use" and tool_uses:
            results = []
            for tu in tool_uses:
                args = tu.input if isinstance(tu.input, dict) else {}
                content, is_error = execute_tool(ctx, tu.name, args)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content, "is_error": is_error})
            messages.append({"role": "user", "content": results})
            continue
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
        try:
            output = json.loads(text)
        except json.JSONDecodeError:
            note = "The model's final answer was not valid structured output."
        break
    else:
        note = "The agent did not finish within the step limit."

    return output, note, usage


def run_agent(
    question: str,
    pnr: str,
    last_name: str,
    source: BookingSource,
    fetcher: PolicyFetcher,
    *,
    client: Any | None = None,
    model: str | None = None,
    effort: str = "medium",
    now: datetime | None = None,
    on_step: Callable[[str, dict[str, Any]], None] | None = None,
    policy_airline: str = "indigo",
    provider: str | None = None,
) -> RunResult:
    t0 = time.monotonic()
    provider = provider or config.agent_provider()
    model = model or config.agent_model(provider)
    ctx = ToolContext(source=source, pnr=pnr, last_name=last_name, fetcher=fetcher,
                      policy_airline=policy_airline, now=now or datetime.now(timezone.utc), on_step=on_step)

    # The model never sees the raw PNR or last name.
    safe_q = redact_text(question, pnr, [last_name])
    user_text = (f"Booking reference: {mask_pnr(pnr)} (credentials attached to get_booking)\n\n"
                 f"Customer question: {safe_q}")
    if provider == "gemini":
        from airline_agent.agent.gemini_loop import run_gemini_loop

        output, note, usage = run_gemini_loop(client, model, ctx, user_text, max_turns=MAX_TURNS)
    else:
        output, note, usage = _anthropic_loop(client or anthropic.Anthropic(), model, effort, ctx, user_text)

    answer = finalize(output, ctx, note)
    ctx.step("done", answer=answer)
    return RunResult(answer=answer, tool_calls=ctx.tool_calls, topics_fetched=ctx.topics_fetched,
                     latency_s=round(time.monotonic() - t0, 2), usage=usage, model_output=output,
                     booking_error=ctx.booking_error,
                     infra_error=output is None and bool(note) and note.startswith(INFRA_NOTES))


def finalize(output: dict[str, Any] | None, ctx: ToolContext, note: str | None = None) -> AgentAnswer:
    """Enforce the agent rules in code: verified quotes only, honest provenance fields."""
    snaps = ctx.snapshots
    official = _official_urls(ctx)
    if output is None:
        urls = sorted({s.url for s in snaps.values()}) or official
        return AgentAnswer(
            answer=(note or "I couldn't complete this answer.") + _official_pointer(urls),
            evidence_quotes=[], source_urls=urls, checked_at=ctx.now,
            booking_source=ctx.source.name, policy_from_cache=any(s.from_cache for s in snaps.values()),
            confidence="low", could_not_confirm=True, snapshot_ids=list(snaps),
        )

    verified, dropped = validate_evidence(output.get("evidence", []), snaps)
    confidence = output.get("confidence", "low")
    could_not_confirm = bool(output.get("could_not_confirm", False))
    answer_text = output.get("answer", "").strip()
    if dropped:
        confidence = lower(confidence)
        log.info("dropped %d unverifiable quote(s)", len(dropped))
    if not verified and not could_not_confirm:
        could_not_confirm = True
        confidence = "low"
        answer_text += ("\n\nNote: I could not verify this against the airline's page text, so please treat it "
                        "as unconfirmed.")

    used = list({s.snapshot_id: s for _, s in verified}.values())
    if not used:
        used = list(snaps.values())
    urls = sorted({s.url for s in used}) or official
    if could_not_confirm and urls and "http" not in answer_text:
        answer_text += _official_pointer(urls)
    from_cache = any(s.from_cache for s in used)
    checked_at = min((s.fetched_at for s in used), default=ctx.now)

    answer_text += "\n\n" + provenance_line(ctx.source.name, from_cache, checked_at, has_policy=bool(used),
                                            booking_ok=ctx.booking is not None)
    return AgentAnswer(
        answer=answer_text,
        evidence_quotes=[q for q, _ in verified],
        source_urls=urls,
        checked_at=checked_at,
        booking_source=ctx.source.name,
        policy_from_cache=from_cache,
        confidence=confidence,  # type: ignore[arg-type]
        could_not_confirm=could_not_confirm,
        snapshot_ids=[s.snapshot_id for s in used],
        dropped_quotes=dropped,
    )


def provenance_line(source: str, from_cache: bool, checked_at: datetime, has_policy: bool = True,
                    booking_ok: bool = True) -> str:
    label = {"mock_testair": "TestAir website (test airline)", "eticket": "uploaded e-ticket",
             "live_indigo": "IndiGo website"}.get(source, source)
    when = checked_at.astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    booking = f"Booking read from: {label}." if booking_ok else f"Booking source: {label} (could not be read)."
    if not has_policy:
        return f"{booking} Policy: not available."
    policy = f"from a saved copy of the official page, checked {when}" if from_cache else f"fetched live at {when}"
    return f"{booking} Policy {policy}."


def _official_urls(ctx: ToolContext) -> list[str]:
    urls: list[str] = []
    for t in ctx.topics_failed:
        try:
            urls += topic_urls(ctx.policy_airline, t)
        except Exception:
            pass
    return sorted(set(urls))


def _official_pointer(urls: list[str]) -> str:
    return ("\n\nPlease check the official page: " + ", ".join(urls)) if urls else ""


def _add_usage(acc: dict[str, int], u: Any) -> None:
    if u is None:
        return
    for k in acc:
        acc[k] += int(getattr(u, k, 0) or 0)


def main() -> None:
    from airline_agent.booking import make_source
    from airline_agent.policy.store import SnapshotStore
    from airline_agent.privacy import extract_credentials, install_log_redaction

    install_log_redaction()
    ap = argparse.ArgumentParser(description="Ask a policy question about a booking.")
    ap.add_argument("question")
    ap.add_argument("--pnr")
    ap.add_argument("--last-name")
    ap.add_argument("--source", default="mock_testair", choices=["mock_testair", "eticket", "live_indigo"])
    ap.add_argument("--pdf", help="e-ticket PDF path (for --source eticket)")
    args = ap.parse_args()
    pnr, last = args.pnr, args.last_name
    if not (pnr and last):
        p2, l2 = extract_credentials(args.question)
        pnr, last = pnr or p2, last or l2
    if not (pnr and last):
        ap.error("Provide --pnr and --last-name (or include them in the question).")
    kwargs = {"pdf_path": args.pdf} if args.source == "eticket" else {}
    source = make_source(args.source, **kwargs)
    fetcher = PolicyFetcher(SnapshotStore(config.policy_db_path()))
    result = run_agent(args.question, pnr, last, source, fetcher,
                       on_step=lambda k, i: print(f"  · {k}", {x: v for x, v in i.items() if x in ('topic', 'error')}))
    a = result.answer
    print("\n" + a.answer)
    print(f"\nconfidence={a.confidence} could_not_confirm={a.could_not_confirm} latency={result.latency_s}s")
    for q in a.evidence_quotes:
        print(f'  evidence: "{q}"')


if __name__ == "__main__":
    main()
