"""Mechanical evidence check: every quote must appear verbatim in a fetched snapshot."""

from __future__ import annotations

import re
import unicodedata

from airline_agent.schemas import PolicySnapshot

MIN_QUOTE_CHARS = 8
_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
    " ": " ", "|": " ",
})


def normalize(s: str) -> str:
    """Whitespace/quote/dash normalisation only — words and numbers must match exactly."""
    s = unicodedata.normalize("NFKC", s).translate(_TRANSLATE)
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in_text(quote: str, text: str) -> bool:
    q = normalize(quote).strip(" .\"'")
    return len(q) >= MIN_QUOTE_CHARS and q in normalize(text)


def validate_evidence(
    evidence: list[dict[str, str]], snapshots: dict[str, PolicySnapshot]
) -> tuple[list[tuple[str, PolicySnapshot]], list[str]]:
    """Split model evidence into (verified (quote, snapshot) pairs, dropped quotes).

    A quote cited against the wrong snapshot_id is accepted if it appears in
    another fetched snapshot (the claim is still grounded) — the pair records
    the snapshot it actually came from.
    """
    verified: list[tuple[str, PolicySnapshot]] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        quote = (item.get("quote") or "").strip()
        if not quote or normalize(quote) in seen:
            continue
        seen.add(normalize(quote))
        snap = snapshots.get(item.get("snapshot_id", ""))
        if snap and quote_in_text(quote, snap.text):
            verified.append((quote, snap))
            continue
        other = next((s for s in snapshots.values() if quote_in_text(quote, s.text)), None)
        if other:
            verified.append((quote, other))
        else:
            dropped.append(quote)
    return verified, dropped


def lower(conf: str) -> str:
    return {"high": "medium", "medium": "low"}.get(conf, "low")
