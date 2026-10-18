"""Gemini tool-calling loop (google-genai SDK). Same contract as the Claude loop.

Gemini runs in function-calling mode "ANY" (it must call a tool every turn) and
delivers its final answer by calling ``submit_answer`` with ANSWER_SCHEMA. The
answer then goes through exactly the same evidence checks as the Claude path.
Needs GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment / .env.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

from airline_agent.agent.prompts import ANSWER_SCHEMA, SYSTEM_PROMPT
from airline_agent.agent.tools import TOOL_DEFS, ToolContext, execute_tool

log = logging.getLogger(__name__)

SUBMIT = "submit_answer"
# Free-tier quotas are per minute; back off and retry instead of failing the question.
RATE_LIMIT_RETRIES = int(os.getenv("GEMINI_RATE_LIMIT_RETRIES") or 4)
RATE_LIMIT_WAIT_S = float(os.getenv("GEMINI_RATE_LIMIT_WAIT_S") or 15)
# Minimum gap between ANY two Gemini requests in this process (agent + eval judge).
MIN_INTERVAL_S = float(os.getenv("GEMINI_MIN_INTERVAL_S") or 7)
_last_request_at = 0.0


def pace() -> None:
    """Space requests out so a free-tier per-minute quota isn't exhausted."""
    global _last_request_at
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()
GEMINI_SUFFIX = (
    "\n\nWhen you have everything you need, call submit_answer exactly once with the final answer. "
    "Never write the final answer as plain text."
)


def _clean_schema(schema: Any) -> Any:
    """Drop JSON-Schema keywords Gemini's function declarations may reject."""
    if isinstance(schema, dict):
        return {k: _clean_schema(v) for k, v in schema.items() if k not in ("additionalProperties", "strict")}
    if isinstance(schema, list):
        return [_clean_schema(v) for v in schema]
    return schema


def build_config(types: Any) -> Any:
    decls = []
    for t in TOOL_DEFS:
        params = t["input_schema"]
        kw: dict[str, Any] = {"name": t["name"], "description": t["description"]}
        if params.get("properties"):
            kw["parameters_json_schema"] = _clean_schema(params)
        decls.append(types.FunctionDeclaration(**kw))
    decls.append(types.FunctionDeclaration(
        name=SUBMIT,
        description="Submit the final answer for the customer, with evidence quotes copied verbatim from the "
                    "fetched policy snapshots. Call exactly once, last.",
        parameters_json_schema=_clean_schema(ANSWER_SCHEMA),
    ))
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT + GEMINI_SUFFIX,
        tools=[types.Tool(function_declarations=decls)],
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )


def _plain(obj: Any) -> Any:
    """Function-call args may contain SDK map/list wrappers; turn them into plain JSON types."""
    return json.loads(json.dumps(obj, default=lambda o: dict(o) if hasattr(o, "items") else list(o)))


def run_gemini_loop(
    client: Any, model: str, ctx: ToolContext, user_text: str, max_turns: int = 8
) -> tuple[dict[str, Any] | None, str | None, dict[str, int]]:
    from google.genai import errors, types

    if client is None:
        from google import genai

        client = genai.Client()  # reads GEMINI_API_KEY / GOOGLE_API_KEY
    config = build_config(types)
    contents: list[Any] = [types.Content(role="user", parts=[types.Part(text=user_text)])]
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}

    for turn in range(max_turns):
        ctx.step("llm_call", turn=turn)
        resp = None
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                pace()
                resp = client.models.generate_content(model=model, contents=contents, config=config)
                break
            except errors.APIError as e:
                code = getattr(e, "code", None)
                if code in (429, 503) and attempt < RATE_LIMIT_RETRIES:
                    wait = RATE_LIMIT_WAIT_S * (attempt + 1)
                    log.info("gemini %s; waiting %ss before retry", code, wait)
                    ctx.step("rate_limited", wait_s=wait)
                    time.sleep(wait)
                    continue
                if code == 429:
                    return None, "The AI service is rate-limited right now (Gemini quota); please retry shortly.", usage
                return None, f"The AI service returned an error (HTTP {code}).", usage
            except httpx.TransportError as e:  # network errors (DNS, connect, timeout)
                if attempt < RATE_LIMIT_RETRIES:
                    log.info("gemini request failed (%s); retrying", type(e).__name__)
                    time.sleep(RATE_LIMIT_WAIT_S)
                    continue
                log.warning("gemini request failed: %s", type(e).__name__)
                return None, "Could not reach the AI service.", usage

        um = getattr(resp, "usage_metadata", None)
        if um is not None:
            usage["input_tokens"] += int(um.prompt_token_count or 0)
            usage["output_tokens"] += int(um.candidates_token_count or 0) + int(um.thoughts_token_count or 0)
            usage["cache_read_input_tokens"] += int(um.cached_content_token_count or 0)

        if not resp.candidates or resp.candidates[0].content is None:
            reason = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
            return None, f"The model returned no answer ({reason or 'empty response'}).", usage
        cand = resp.candidates[0]
        # Keep the model turn exactly as returned (it may carry thought signatures).
        contents.append(cand.content)

        calls = resp.function_calls or []
        submit = next((c for c in calls if c.name == SUBMIT), None)
        if submit is not None:
            return _plain(submit.args or {}), None, usage
        if not calls:
            text = (resp.text or "").strip()
            try:
                return json.loads(text), None, usage
            except (json.JSONDecodeError, TypeError):
                finish = getattr(cand, "finish_reason", None)
                return None, f"The model stopped without a structured answer ({finish}).", usage

        parts = []
        for fc in calls:
            content, is_error = execute_tool(ctx, fc.name, _plain(fc.args or {}))
            parts.append(types.Part(function_response=types.FunctionResponse(
                id=fc.id, name=fc.name, response={"error": content} if is_error else {"result": content},
            )))
        contents.append(types.Content(role="user", parts=parts))

    return None, "The agent did not finish within the step limit.", usage
