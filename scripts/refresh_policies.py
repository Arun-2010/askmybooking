"""Fetch every configured policy page live (once), store snapshots, optionally pin them for eval.

    python scripts/refresh_policies.py            # live refresh into the snapshot DB
    python scripts/refresh_policies.py --pin      # ...and write eval/fixtures/snapshots.json

Pinned snapshots are what eval/cases.yaml expected answers were written against.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airline_agent import config  # noqa: E402
from airline_agent.policy.fetcher import PolicyFetcher, PolicyFetchError  # noqa: E402
from airline_agent.policy.store import SnapshotStore  # noqa: E402

PIN_PATH = ROOT / "eval" / "fixtures" / "snapshots.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--airline", default="indigo")
    ap.add_argument("--pin", action="store_true", help="write the fetched snapshots to eval/fixtures/snapshots.json")
    args = ap.parse_args()

    store = SnapshotStore(config.policy_db_path())
    fetcher = PolicyFetcher(store)
    pinned = []
    for topic in config.policy_sources()[args.airline]["topics"]:
        try:
            snaps = fetcher.fetch_policy(args.airline, topic, force_live=True)
        except PolicyFetchError as e:
            print(f"[{topic}] FAILED: {e}")
            continue
        for s in snaps:
            state = "cached (live failed)" if s.from_cache else "live"
            print(f"[{topic}] {state:22s} {s.snapshot_id}  {len(s.text):6d} chars  {s.url}")
            pinned.append(json.loads(s.model_dump_json()))
    changed = store.events("policy_changed")
    print(f"\nstats: {fetcher.stats}  policy_changed events so far: {len(changed)}")
    if args.pin:
        if any(p["from_cache"] for p in pinned):
            print("refusing to pin: some snapshots are cached, not live")
            sys.exit(1)
        PIN_PATH.parent.mkdir(parents=True, exist_ok=True)
        for p in pinned:
            p["from_cache"] = False
        PIN_PATH.write_text(json.dumps({"snapshots": pinned}, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"pinned {len(pinned)} snapshots -> {PIN_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
