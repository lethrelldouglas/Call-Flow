"""Pretend to be Retell on an outbound dispatcher call: the "contractor" says yes, the ticket flips.

Creates a throwaway emergency ticket in the store, opens the dispatcher WebSocket the way Retell
would for an outbound call, plays the contractor, and checks the ticket was accepted.
Needs NEBIUS_API_KEY. The tenant confirmation text goes wherever texts go (DEMO_PHONE, or dry run).

    python tests/dispatch_smoke.py
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import tickets  # noqa: E402
import voice_server  # noqa: E402

CONTRACTOR_TURNS = [
    "Uh, yeah, this is Rapid Flow. Water through the ceiling, is that still going?",
    "Okay. Yeah, I can take it, I'll head over now.",
]


def main() -> int:
    ticket = tickets.Ticket(
        id="NG-DSMOKE", created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), channel="phone",
        tenant_name="Amara Osei", phone="+14165550199", unit="412", building="Northgate Tower", category="plumbing",
        urgency="emergency", emergency_flags=["flooding_or_active_leak"], issue="Water pouring through the bathroom ceiling.",
        details="Still leaking. Cat in the unit, permission to enter given.",
    )
    ticket.ack = {"state": "calling", "chain": [{"name": "Rapid Flow Plumbing", "phone": "+14165550150"},
                                                {"name": "Jordan Pike", "phone": "+14165550140"}],
                  "index": 0, "attempts": [], "next_at": "", "accepted_by": ""}
    tickets.save(ticket)

    call_id = f"dispatch-smoke-{int(time.time())}"
    with TestClient(voice_server.app) as client:
        with client.websocket_connect(f"/llm-websocket-dispatch/{call_id}") as ws:
            config = ws.receive_json()
            assert config["response_type"] == "config", config
            ws.send_json({"interaction_type": "call_details", "call": {
                "call_type": "phone_call", "direction": "outbound", "from_number": "+16475550100",
                "to_number": "+14165550150", "metadata": {"purpose": "dispatch", "ticket_id": ticket.id, "contact": "Rapid Flow Plumbing"},
            }})
            opening = ws.receive_json()
            assert opening["response_id"] == 0, opening
            print(f"Agent: {opening['content']}")
            transcript = [{"role": "agent", "content": opening["content"]}]
            ended = False
            for response_id, said in enumerate(CONTRACTOR_TURNS, start=1):
                print(f"Contractor: {said}")
                transcript.append({"role": "user", "content": said})
                ws.send_json({"interaction_type": "response_required", "response_id": response_id, "transcript": transcript})
                started, text, last = time.time(), "", None
                while True:
                    msg = ws.receive_json()
                    if msg.get("response_type") != "response" or msg.get("response_id") != response_id:
                        continue
                    text += msg.get("content", "")
                    if msg.get("content_complete"):
                        last = msg
                        break
                print(f"Agent ({time.time() - started:.1f}s): {text.strip()}")
                assert "[ACCEPT]" not in text and "[DECLINE]" not in text, "decision mark leaked into speech"
                transcript.append({"role": "agent", "content": text.strip()})
                if last and last.get("end_call"):
                    ended = True
                    print("(agent ended the call)")
                    break

    final = tickets.get(ticket.id)
    print(f"\nTicket {final.id}: status={final.status} ack={final.ack.get('state')} accepted_by={final.ack.get('accepted_by')}")
    for action in final.actions:
        print("  " + action)
    ok = ended and final.ack.get("state") == "accepted" and final.status == "in_progress"
    print("\nPASS" if ok else "\nCHECK: expected the contractor's yes to accept the ticket")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
