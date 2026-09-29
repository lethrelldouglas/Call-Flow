"""Point the Retell agent at this server's current public address.

Quick tunnels get a new address every time they start, and Retell keeps the old one until
told otherwise. This updates the agent's Custom LLM URL and webhook URL through Retell's API.

    python retell_sync.py            # uses the tunnel address from tunnel.log (or PUBLIC_URL)
    python retell_sync.py --show     # just print what the agent currently points at
"""
from __future__ import annotations

import argparse
import sys

import httpx

import settings

RETELL = "https://api.retellai.com"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--show", action="store_true", help="print the agent's current settings and exit")
    args = parser.parse_args()

    if not settings.RETELL_API_KEY or not settings.RETELL_DISPATCH_AGENT_ID:
        print("RETELL_API_KEY or RETELL_DISPATCH_AGENT_ID is empty in .env; nothing to sync.")
        return 0
    headers = {"Authorization": f"Bearer {settings.RETELL_API_KEY}"}
    agent_id = settings.RETELL_DISPATCH_AGENT_ID

    current = httpx.get(f"{RETELL}/get-agent/{agent_id}", headers=headers, timeout=20)
    if current.status_code >= 300:
        print(f"Could not read the agent: HTTP {current.status_code} {current.text[:200]}")
        return 1
    agent = current.json()
    engine = agent.get("response_engine") or {}
    print(f"Agent: {agent.get('agent_name')}  ({agent_id})")
    print(f"  websocket now : {engine.get('llm_websocket_url')}")
    print(f"  webhook now   : {agent.get('webhook_url')}")
    if args.show:
        return 0
    if engine.get("type") != "custom-llm":
        print("  This agent is not a Custom LLM agent; not touching it.")
        return 1

    base = settings.public_url()
    if not base:
        print("No public address known: start the tunnel first (start_demo.bat), or set PUBLIC_URL in .env.")
        return 1
    wss = base.replace("https://", "wss://") + "/llm-websocket"
    webhook = base + "/webhook"
    if engine.get("llm_websocket_url") == wss and agent.get("webhook_url") == webhook:
        print("  Already up to date.")
        return 0

    body = {"response_engine": {"type": "custom-llm", "llm_websocket_url": wss}, "webhook_url": webhook}
    update = httpx.patch(f"{RETELL}/update-agent/{agent_id}", headers=headers, json=body, timeout=20)
    if update.status_code >= 300:
        print(f"Update failed: HTTP {update.status_code} {update.text[:300]}")
        return 1
    print(f"  websocket set : {wss}")
    print(f"  webhook set   : {webhook}")
    return bind_number(headers, agent_id)


def bind_number(headers: dict, agent_id: str) -> int:
    """Make sure the from-number rings this agent for inbound calls and uses it for outbound ones."""
    number = settings.RETELL_FROM_NUMBER
    if not number:
        return 0
    current = httpx.get(f"{RETELL}/get-phone-number/{number}", headers=headers, timeout=20)
    if current.status_code >= 300:
        print(f"  number {number}: could not read it (HTTP {current.status_code})")
        return 0
    info = current.json()
    bound_in = [a.get("agent_id") for a in info.get("inbound_agents") or []]
    bound_out = [a.get("agent_id") for a in info.get("outbound_agents") or []]
    if bound_in == [agent_id] and bound_out == [agent_id]:
        print(f"  number {number}: already bound to this agent, both directions")
        return 0
    agent = httpx.get(f"{RETELL}/get-agent/{agent_id}", headers=headers, timeout=20).json()
    entry = [{"agent_id": agent_id, "agent_version": agent.get("version", 0), "weight": 1}]
    update = httpx.patch(f"{RETELL}/update-phone-number/{number}", headers=headers,
                         json={"inbound_agents": entry, "outbound_agents": entry}, timeout=20)
    if update.status_code >= 300:
        print(f"  number {number}: binding failed (HTTP {update.status_code}) {update.text[:200]}")
        return 1
    print(f"  number {number}: now rings this agent and calls out with it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
