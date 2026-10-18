"""PNR / passenger-name masking helpers.

Rule: raw PNRs and passenger names are never logged, printed, stored, or sent to
the LLM. Everything that leaves the booking source uses ``mask_pnr``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

# Airline PNRs are 6 alphanumeric characters. Require at least one digit or the
# "PNR" keyword nearby so ordinary words like "TRAVEL" aren't treated as PNRs.
_PNR_RE = re.compile(r"\b(?=[A-Z0-9]*\d)[A-Z0-9]{6}\b")
_PNR_KEYWORD_RE = re.compile(r"\b(?:PNR|booking(?:\s+ref(?:erence)?)?|ref)\s*[:#]?\s*([A-Za-z0-9]{6})\b", re.I)
_LAST_NAME_RE = re.compile(r"\blast\s*name\s*(?:is|:|=)?\s*([A-Za-z][A-Za-z'\-]{1,40})", re.I)

NAME_KEYS = {"first_name", "last_name", "firstname", "lastname", "name", "full_name", "given_name", "surname"}
PNR_KEYS = {"pnr", "record_locator", "recordlocator", "booking_reference", "pnr_code"}


def mask_pnr(pnr: str | None) -> str:
    """'ABC123' -> 'AB****'. Empty/None -> '******'."""
    if not pnr:
        return "******"
    pnr = pnr.strip().upper()
    return pnr[:2] + "*" * max(len(pnr) - 2, 4)


def mask_name(name: str | None) -> str:
    """'Reddy' -> 'R****'."""
    if not name:
        return "*****"
    name = name.strip()
    return name[:1].upper() + "*" * max(len(name) - 1, 4)


def extract_credentials(text: str) -> tuple[str | None, str | None]:
    """Pull a PNR and last name out of a free-text question, if present."""
    pnr = None
    m = _PNR_KEYWORD_RE.search(text)
    if m:
        pnr = m.group(1).upper()
    else:
        m2 = _PNR_RE.search(text.upper())
        if m2:
            pnr = m2.group(0)
    last = None
    m3 = _LAST_NAME_RE.search(text)
    if m3:
        last = m3.group(1)
    return pnr, last


def redact_text(text: str, pnr: str | None = None, names: list[str] | None = None) -> str:
    """Replace a known PNR and names (case-insensitive) in text with masked forms."""
    out = text
    if pnr:
        out = re.sub(re.escape(pnr), mask_pnr(pnr), out, flags=re.I)
    for n in names or []:
        if n and len(n) >= 2:
            out = re.sub(rf"\b{re.escape(n)}\b", mask_name(n), out, flags=re.I)
    return out


# Substrings of key names whose scalar values are personal data (live captures).
_SENSITIVE_KEY_PARTS = ("name", "email", "phone", "mobile", "contact", "address", "birth", "dob", "passport",
                        "document", "nationality", "gender", "title", "loyalty", "ffn", "frequent", "customer_id",
                        "customerid", "card", "payment", "gst", "token", "session", "otp")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s-]{8,}\d")


def _is_sensitive_key(k: Any) -> bool:
    compact = str(k).lower().replace("-", "").replace("_", "")
    return any(part in compact for part in _SENSITIVE_KEY_PARTS) and not compact.endswith(("type", "code", "count"))


def _blank(obj: Any) -> Any:
    """Redact every scalar inside a container that sits under a sensitive key."""
    if isinstance(obj, dict):
        return {k: _blank(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_blank(v) for v in obj]
    return obj if obj is None or isinstance(obj, bool) else "<redacted>"


def redact_obj(obj: Any, secrets: list[str] | None = None) -> Any:
    """Recursively redact personal data in a JSON-like structure.

    Used before saving any live-booking capture as a fixture. Masks PNR fields,
    blanks any scalar under a sensitive-looking key, scrubs emails/phone numbers
    in free text, and replaces every occurrence of the given ``secrets`` (e.g.
    the raw PNR and last name) wherever they appear.
    """
    secrets = [s for s in (secrets or []) if s and len(s) >= 2]

    def scrub(s: str) -> str:
        for sec in secrets:
            s = re.sub(re.escape(sec), "<redacted>", s, flags=re.I)
        return _PHONE_RE.sub("<phone>", _EMAIL_RE.sub("<email>", s))

    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = str(k).lower().replace("-", "_")
            if key in PNR_KEYS and isinstance(v, str):
                out[k] = mask_pnr(v)
            elif key in NAME_KEYS and isinstance(v, str):
                out[k] = mask_name(v)
            elif _is_sensitive_key(k):
                out[k] = _blank(v)
            else:
                out[k] = redact_obj(v, secrets)
        return out
    if isinstance(obj, list):
        return [redact_obj(v, secrets) for v in obj]
    if isinstance(obj, str):
        return scrub(obj)
    return obj


class PnrRedactingFilter(logging.Filter):
    """Logging filter that masks anything that looks like a PNR in log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        masked = _PNR_RE.sub(lambda m: mask_pnr(m.group(0)), msg)
        if masked != msg:
            record.msg = masked
            record.args = ()
        return True


def install_log_redaction() -> None:
    """Attach the PNR-masking filter to every root handler."""
    root = logging.getLogger()
    for h in root.handlers:
        h.addFilter(PnrRedactingFilter())
    if not root.handlers:
        logging.basicConfig(level=logging.INFO)
        for h in root.handlers:
            h.addFilter(PnrRedactingFilter())
