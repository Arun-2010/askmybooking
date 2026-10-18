"""Streamlit demo:  streamlit run ui/app.py   (start the mock site first for TestAir bookings)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airline_agent import config  # noqa: E402
from airline_agent.agent.run import run_agent  # noqa: E402
from airline_agent.booking import make_source  # noqa: E402
from airline_agent.policy.fetcher import PolicyFetcher  # noqa: E402
from airline_agent.policy.store import SnapshotStore  # noqa: E402
from airline_agent.privacy import extract_credentials, install_log_redaction, mask_pnr  # noqa: E402

install_log_redaction()
st.set_page_config(page_title="AskMyBooking", page_icon="✈️", layout="centered")

SEED = json.loads((ROOT / "mock_site" / "seed_bookings.json").read_text(encoding="utf-8"))["bookings"]
SOURCE_LABELS = {
    "mock_testair": "TestAir website (fictional test airline)",
    "eticket": "Upload e-ticket PDF",
    "live_indigo": "Live IndiGo website (your own booking only)",
}
EXAMPLES = {
    "baggage": "How much check-in baggage can I take?",
    "cancellation": "Can I cancel my booking for free?",
    "web_checkin": "When can I do web check-in?",
}


@st.cache_resource
def get_store() -> SnapshotStore:
    return SnapshotStore(config.policy_db_path())


st.title("✈️ AskMyBooking")
st.caption("Answers airline policy questions **for your booking**, using the airline's policy pages fetched live, "
           "with quotes and a timestamp.")

with st.sidebar:
    st.header("Booking source")
    source_name = st.radio("Read the booking from", list(SOURCE_LABELS), format_func=SOURCE_LABELS.get)
    switches: dict[str, str] = {}
    pdf_path = None
    if source_name == "mock_testair":
        st.caption(f"Mock site: {config.testair_base_url()}")
        sw = st.selectbox("Failure switch (robustness demo)", ["none", "captcha=1", "layout=v2", "slow=5", "error=500"])
        if sw != "none":
            k, v = sw.split("=")
            switches[k] = v
        st.divider()
        st.subheader("Demo bookings")
        pick = st.selectbox(
            "Fill with a seeded TestAir booking", ["—"] + [b["id"] for b in SEED],
            format_func=lambda i: i if i == "—" else
            f"{i} · {mask_pnr(next(b for b in SEED if b['id'] == i)['pnr'])} · "
            f"{', '.join(next(b for b in SEED if b['id'] == i)['tags'])}",
        )
        if pick != "—" and st.session_state.get("_picked") != pick:
            b = next(x for x in SEED if x["id"] == pick)
            st.session_state["pnr"], st.session_state["last_name"] = b["pnr"], b["last_name"]
            st.session_state["_picked"] = pick
    elif source_name == "eticket":
        up = st.file_uploader("E-ticket PDF", type=["pdf"])
        if up:
            tmp = Path(tempfile.gettempdir()) / "askmybooking_upload.pdf"
            tmp.write_bytes(up.getvalue())
            pdf_path = str(tmp)
    else:
        st.warning("Your own booking only. Assisted capture: first run "
                   "`scripts\\start_chrome_for_capture.ps1`, press Ask, then retrieve your booking yourself in that "
                   "Chrome window. The agent only reads the booking data the page receives.")
    st.divider()
    st.caption(f"Model: `{config.agent_provider()}:{config.agent_model()}` · cache TTL {config.cache_ttl_hours():g} h")
    force_live = st.checkbox("Ignore cache (force live policy fetch)")

if not any(os.getenv(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY")):
    st.info("Set `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY`) in `.env` to run the agent.")

c1, c2 = st.columns(2)
pnr = c1.text_input("PNR / booking reference", key="pnr", max_chars=6)
last_name = c2.text_input("Last name", key="last_name", type="password")
topic_hint = st.radio("Example", list(EXAMPLES), horizontal=True, format_func=lambda t: t.replace("_", " "))
question = st.text_area("Your question", value=EXAMPLES[topic_hint], height=80)

if st.button("Ask", type="primary", disabled=not question):
    if not (pnr and last_name):
        p2, l2 = extract_credentials(question)
        pnr, last_name = pnr or p2, last_name or l2
    if not (pnr and last_name):
        st.error("Enter the PNR and last name.")
        st.stop()
    if source_name == "eticket" and not pdf_path:
        st.error("Upload the e-ticket PDF first.")
        st.stop()

    kwargs: dict = {}
    if source_name == "mock_testair":
        kwargs = {"switches": switches}
    elif source_name == "eticket":
        kwargs = {"pdf_path": pdf_path}
    source = make_source(source_name, **kwargs)
    fetcher = PolicyFetcher(get_store(), ttl_hours=0 if force_live else None)

    with st.status("Working on it…", expanded=True) as status:
        def on_step(kind: str, info: dict) -> None:
            if kind == "booking_start":
                status.write(f"🔎 Reading booking {mask_pnr(pnr)} from {SOURCE_LABELS[source_name]}…")
            elif kind == "booking_ok":
                b = info["booking"]
                route = " → ".join([b.flights[0].origin] + [f.destination for f in b.flights])
                status.write(f"✅ Booking: {route} · fare {b.fare_type} ({b.fare_category}) · "
                             f"{len(b.passengers)} passenger(s) · {b.status}")
            elif kind == "booking_error":
                status.write(f"⚠️ Booking not read: {info['error']}")
            elif kind == "policy_start":
                status.write(f"🌐 Fetching current {info['topic'].replace('_', ' ')} policy…")
            elif kind == "policy_ok":
                for s in info["snapshots"]:
                    how = "from cache" if s.from_cache else "live"
                    status.write(f"📄 {s.url} ({how}, {s.fetched_at:%d %b %H:%M} UTC)")
            elif kind == "policy_error":
                status.write(f"⚠️ Policy unavailable: {info['error']}")
            elif kind == "llm_call" and info.get("turn", 0) > 0:
                status.write("🧠 Matching the rule to your booking…")

        try:
            result = run_agent(question, pnr, last_name, source, fetcher, on_step=on_step)
        except Exception as e:  # keep the UI alive; never echo raw PNR
            status.update(label="Failed", state="error")
            st.error(f"Something went wrong: {type(e).__name__}")
            st.stop()
        status.update(label=f"Done in {result.latency_s:.0f}s", state="complete", expanded=False)

    a = result.answer
    if a.could_not_confirm:
        st.warning("Couldn't fully confirm this from the airline's page.")
    st.markdown(a.answer)
    badge = {"high": "🟢", "medium": "🟡", "low": "🔴"}[a.confidence]
    st.caption(f"{badge} confidence: {a.confidence} · booking source: {a.booking_source} · "
               f"policy {'from cache' if a.policy_from_cache else 'fetched live'} · checked {a.checked_at:%d %b %Y %H:%M} UTC")
    if a.evidence_quotes:
        with st.expander(f"Evidence ({len(a.evidence_quotes)} quote(s), verified against the page text)", expanded=True):
            for q in a.evidence_quotes:
                st.markdown(f"> {q}")
    if a.dropped_quotes:
        with st.expander(f"Dropped {len(a.dropped_quotes)} quote(s) that weren't found on the page"):
            for q in a.dropped_quotes:
                st.markdown(f"~~{q}~~")
    for u in a.source_urls:
        st.markdown(f"🔗 [{u}]({u})")

with st.expander("Policy snapshot history"):
    store = get_store()
    ev = store.events()
    changed = [e for e in ev if e["kind"] == "policy_changed"]
    st.write(f"{sum(e['kind'] == 'fetch_ok' for e in ev)} live fetches, "
             f"{sum(e['kind'] == 'fetch_failed' for e in ev)} failed, {len(changed)} policy change(s) detected.")
    for e in changed[-10:]:
        st.write(f"🔔 {e['at'][:16]} · {e['topic']} · {e['url']}")
