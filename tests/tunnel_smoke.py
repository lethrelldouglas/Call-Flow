"""Hold a short scripted call against the voice server THROUGH its public address, the way Retell will.

    python tests/tunnel_smoke.py https://your-tunnel-host.trycloudflare.com

Checks the health endpoint, then opens the WebSocket, reads the config and greeting, sends a
few caller turns, and prints what the agent streams back and how long each turn took.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request

import websockets

TURNS = [
    "Hi, there's water coming through my bathroom ceiling in unit 412 at Northgate Tower. It's still pouring.",
    "It's Amara Osei, and yes, this number is the best one. You can come in if I'm out, I have a cat.",
    "Yes, that's all right. Is someone coming tonight?",
    "No, that's everything. Thanks.",
]


async def run(base: str) -> int:
    base = base.rstrip("/")
    http = base if base.startswith("http") else "https://" + base
    wss = http.replace("https://", "wss://").replace("http://", "ws://")

    with urllib.request.urlopen(http + "/health", timeout=20) as resp:
        print("health:", resp.read().decode()[:80])

    call_id = f"tunnel-{int(time.time())}"
    started = time.time()
    async with websockets.connect(f"{wss}/llm-websocket/{call_id}", open_timeout=20) as ws:
        print(f"websocket connected in {time.time() - started:.1f}s")
        config = json.loads(await ws.recv())
        greeting = json.loads(await ws.recv())
        assert config.get("response_type") == "config", config
        print("Agent:", greeting.get("content"))
        await ws.send(json.dumps({"interaction_type": "call_details", "call": {"call_type": "phone_call", "from_number": "+14165550199"}}))
        await ws.send(json.dumps({"interaction_type": "ping_pong", "timestamp": int(time.time() * 1000)}))

        transcript = [{"role": "agent", "content": greeting.get("content", "")}]
        for response_id, said in enumerate(TURNS, start=1):
            print("Caller:", said)
            transcript.append({"role": "user", "content": said})
            await ws.send(json.dumps({"interaction_type": "response_required", "response_id": response_id, "transcript": transcript}))
            t0, text, ended = time.time(), "", False
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if msg.get("response_type") != "response" or msg.get("response_id") != response_id:
                    continue
                text += msg.get("content", "")
                if msg.get("content_complete"):
                    ended = bool(msg.get("end_call"))
                    break
            print(f"Agent ({time.time() - t0:.1f}s): {text.strip()}")
            transcript.append({"role": "agent", "content": text.strip()})
            if ended:
                print("(agent ended the call)")
                break
    print("PASS: the public address reaches the voice server and streams replies")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(run(sys.argv[1])))
