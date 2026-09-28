"""Check that the Nebius Token Factory and Tavily API keys in .env work.

Run:  python check_keys.py

Keys are read from the .env file next to this script. They are never printed.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from tavily import TavilyClient

# Windows consoles default to a legacy encoding; make sure model output prints fine.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env", override=True)

TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1/"
PREFERRED_MODEL = "nvidia/nemotron-3-super-120b-a12b"


def get_key(name: str) -> str:
    return (os.getenv(name) or "").strip().strip('"').strip("'")


def safe(exc: Exception, key: str) -> str:
    """Error text with the key masked, in case a server echoes it back."""
    text = f"{type(exc).__name__}: {exc}"
    return text.replace(key, "***") if key else text


def check_nebius() -> bool:
    key = get_key("NEBIUS_API_KEY")
    if not key:
        print("[NEBIUS] No key found. Paste it after NEBIUS_API_KEY= in .env and save.")
        return False
    client = OpenAI(base_url=TOKEN_FACTORY_URL, api_key=key)

    model = PREFERRED_MODEL
    try:
        ids = [m.id for m in client.models.list()]
        nemotron = sorted(i for i in ids if "nemotron" in i.lower())
        print(f"[NEBIUS] {len(ids)} models available, {len(nemotron)} of them Nemotron:")
        for i in nemotron:
            print(f"           {i}")
        if PREFERRED_MODEL not in ids and nemotron:
            model = nemotron[0]
    except Exception as exc:
        print(f"[NEBIUS] Could not list models ({safe(exc, key)}). Trying a chat call anyway.")

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": "In one short sentence, what does an AI front desk do for a tree removal company?",
            }],
            max_tokens=600,
        )
    except Exception as exc:
        print(f"[NEBIUS] Chat call to {model} failed: {safe(exc, key)}")
        return False

    text = (resp.choices[0].message.content or "").strip()
    print(f"[NEBIUS] Key works. {model} replied:")
    print(f"           {text[:400] or '(only reasoning came back; raise max_tokens)'}")
    if resp.usage:
        print(f"[NEBIUS] Tokens used: {resp.usage.prompt_tokens} in, {resp.usage.completion_tokens} out")
    return True


def check_tavily() -> bool:
    key = get_key("TAVILY_API_KEY")
    if not key:
        print("[TAVILY] No key found. Paste it after TAVILY_API_KEY= in .env and save.")
        return False
    try:
        result = TavilyClient(api_key=key).search("Blue Mountain Village Collingwood Ontario", max_results=2)
    except Exception as exc:
        print(f"[TAVILY] Search failed: {safe(exc, key)}")
        return False
    hits = result.get("results", [])
    top = hits[0]["title"] if hits else "(no results)"
    print(f"[TAVILY] Key works. Top result: {top}")
    return True


if __name__ == "__main__":
    nebius_ok = check_nebius()
    print()
    tavily_ok = check_tavily()
    print()
    if nebius_ok and tavily_ok:
        print("ALL GOOD. Both keys work.")
        sys.exit(0)
    print("Something needs fixing. See the messages above.")
    sys.exit(1)
