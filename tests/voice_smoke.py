"""Pretend to be Retell: open the WebSocket, hold a short call, hang up, and check a ticket appears.

Needs NEBIUS_API_KEY in .env (it makes real Nemotron calls). Run: python tests/voice_smoke.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import tickets  # noqa: E402
import voice_server  # noqa: E402

CALL_ID = f"smoke-{int(time.time())}"
# A scripted caller cannot react to what the agent asks, so each turn volunteers what a
# real caller would have answered by then. Multi-turn realism gets tested live on Retell.
CALLER_TURNS = [
    "Hi, um, there's water coming through my bathroom ceiling in unit 412 at Northgate Tower. It's still pouring and getting worse.",
    "It's Amara Osei, and yes, this number is the best one. You can come in if I'm out, I have a cat.",
    "Yes, that's all right. Is someone coming tonight?",
    "No, that's everything. Thanks.",
]


def main() -> int:
    with TestClient(voice_server.app) as client:
        with client.websocket_connect(f"/llm-websocket/{CALL_ID}") as ws:
            config = ws.receive_json()
            greeting = ws.receive_json()
            assert config["response_type"] == "config", config
            assert greeting["response_id"] == 0 and greeting["content_complete"], greeting
            print(f"Agent: {greeting['content']}")
            ws.send_json({"interaction_type": "call_details", "call": {"call_type": "phone_call", "from_number": "+14165550199", "to_number": "+14165550100"}})
            ws.send_json({"interaction_type": "ping_pong", "timestamp": int(time.time() * 1000)})

            transcript = [{"role": "agent", "content": greeting["content"]}]
            ended = False
            for response_id, said in enumerate(CALLER_TURNS, start=1):
                print(f"Caller: {said}")
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
                assert "[END_CALL]" not in text, "end-call mark leaked into speech"
                transcript.append({"role": "agent", "content": text.strip()})
                if last and last.get("end_call"):
                    ended = True
                    print("(agent ended the call)")
                    break
            if not ended:
                print("(caller hung up)")

    ticket = None
    for _ in range(60):
        ticket = tickets.find_by_source(CALL_ID)
        if ticket:
            break
        time.sleep(1)
    if not ticket:
        print("FAIL: no ticket was created for the call")
        return 1
    print(f"\nTicket {ticket.id}: {ticket.urgency} / {ticket.category} / unit {ticket.unit} {ticket.building} / {ticket.tenant_name} {ticket.phone}")
    print(f"Issue: {ticket.issue}")
    print(f"Assigned to: {ticket.assigned_to} ({ticket.eta})")
    for action in ticket.actions:
        print("  " + action)
    ok = ticket.unit == "412" and ticket.urgency == "emergency" and ticket.phone.endswith("0199")
    print("\nPASS" if ok else "\nCHECK: ticket fields differ from what was said")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
