"""Recover calls the board missed — even ones cut short or dropped by the tunnel.

Retell records every call on its side, including calls where our WebSocket never
connected (tunnel down) or the caller hung up early. This pulls recent Retell calls,
and for any inbound call that isn't already on the board, it either:
  - runs the transcript through Nemotron to open a proper ticket (if the caller said
    anything), or
  - opens a "missed call, needs callback" ticket noting the number and why it dropped.

Recovered tickets are recorded on the board but NOT re-dispatched (no texts/calls fire
for old calls). Pass --dispatch to also run dispatch on recovered emergencies.

    python backfill_calls.py            # catch up the last 20 calls
    python backfill_calls.py --limit 50
    python backfill_calls.py --loop     # keep checking every 30s (safety net during a demo)
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

import settings
import tickets

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

RETELL = "https://api.retellai.com"


def list_recent_calls(limit: int) -> list[dict]:
    h = {"Authorization": f"Bearer {settings.RETELL_API_KEY}"}
    r = httpx.post(f"{RETELL}/v2/list-calls", headers=h,
                   json={"limit": limit, "sort_order": "descending"}, timeout=20)
    r.raise_for_status()
    return r.json()


def transcript_text(call: dict) -> str:
    """Best available transcript text for a call."""
    obj = call.get("transcript_object") or []
    if obj:
        return "\n".join(
            f"{'Agent' if u.get('role') == 'agent' else 'User'}: {u.get('content', '')}"
            for u in obj if u.get("content")
        )
    return call.get("transcript") or ""


def caller_said_anything(call: dict) -> bool:
    obj = call.get("transcript_object") or []
    if obj:
        return any(u.get("role") != "agent" and (u.get("content") or "").strip() for u in obj)
    # plain transcript: assume the caller contributed if there's any text
    return bool((call.get("transcript") or "").strip())


def recover_one(call: dict, knowledge: str, do_dispatch: bool) -> str | None:
    call_id = call.get("call_id", "")
    if not call_id or call.get("direction") != "inbound":
        return None
    if call.get("call_status") not in ("ended", "error"):
        return None  # still ongoing; let the normal path finish it first
    if tickets.find_by_source(call_id):
        return None  # already on the board (normal path handled it)

    from_number = call.get("from_number", "") or ""
    reason = call.get("disconnection_reason", "")

    if caller_said_anything(call):
        text = transcript_text(call)
        try:
            triage = tickets.triage_request(text, "phone", knowledge)
        except Exception as exc:
            print(f"  {call_id[:16]}: triage failed ({exc}); recording as a missed call")
            triage = None
        if triage and triage.get("is_request", True):
            t = tickets.new_ticket(triage, "phone", transcript=text, source_ref=call_id, caller_phone=from_number)
            t.log_action(f"recovered from Retell (call cut short / board missed it; reason: {reason or 'unknown'})")
            tickets.save(t)
            if do_dispatch and t.is_emergency:
                tickets.dispatch(t, knowledge)
            return f"{t.id}  {t.urgency}  {t.category}  unit {t.unit or '?'}  (recovered, had transcript)"
        if triage and not triage.get("is_request", True):
            return None  # spam / not a request

    # no usable transcript — record a bare "missed call" so it's still on the board
    t = tickets.new_ticket(
        {"category": "other", "urgency": "urgent",
         "issue": "Call did not complete (dropped or no audio). Tenant needs a callback.",
         "summary": "Missed/dropped inbound call recovered from Retell",
         "tenant_name": "", "phone": from_number, "building": "", "unit": ""},
        "phone", transcript=transcript_text(call), source_ref=call_id, caller_phone=from_number,
    )
    t.missing = [m for m in ["tenant_name", "unit"] ]
    t.log_action(f"recovered from Retell as a missed call (reason: {reason or 'unknown'}); call back {from_number or 'unknown'}")
    tickets.save(t)
    return f"{t.id}  MISSED CALL  callback {from_number or '?'}  (reason: {reason or 'unknown'})"


def run_once(limit: int, do_dispatch: bool, since_minutes: float) -> int:
    knowledge = settings.load_business_knowledge(settings.PROPERTY_PROFILE)
    calls = list_recent_calls(limit)
    cutoff_ms = (time.time() - since_minutes * 60) * 1000 if since_minutes else 0
    recovered = 0
    for call in calls:
        if cutoff_ms and (call.get("start_timestamp") or 0) < cutoff_ms:
            continue  # older than the window; skip (keeps old test calls off the board)
        line = recover_one(call, knowledge, do_dispatch)
        if line:
            print("  recovered:", line)
            recovered += 1
    return recovered


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=20, help="how many recent calls to scan")
    ap.add_argument("--dispatch", action="store_true", help="also dispatch recovered emergencies")
    ap.add_argument("--loop", action="store_true", help="keep checking every 30s")
    ap.add_argument("--since-minutes", type=float, default=15, help="only recover calls newer than this many minutes (0 = all)")
    args = ap.parse_args()

    if not settings.RETELL_API_KEY:
        print("RETELL_API_KEY is empty; nothing to recover from."); return 1

    if args.loop:
        print("Backfill watchdog: scanning Retell every 30s for missed calls. Ctrl-C to stop.")
        while True:
            try:
                n = run_once(args.limit, args.dispatch, args.since_minutes)
                if n:
                    print(f"  -> recovered {n} call(s) onto the board")
            except Exception as exc:
                print(f"  scan error: {exc}")
            time.sleep(30)
    else:
        n = run_once(args.limit, args.dispatch, args.since_minutes)
        print(f"\n{n} call(s) recovered. Board now has {len(tickets.load_all())} ticket(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
