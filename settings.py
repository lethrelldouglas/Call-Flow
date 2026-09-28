"""Configuration for the ResolvOps front desk agent.

Secrets (API keys, mailbox password) come from the .env file next to this
file, which is git-ignored. Everything else has a sensible default here and
can also be overridden from .env.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=True)


def env(name: str, default: str = "") -> str:
    """Read an environment variable, stripping stray quotes and spaces."""
    return (os.getenv(name) or default).strip().strip('"').strip("'")


def env_bool(name: str, default: bool) -> bool:
    value = env(name).lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


# --- Nebius Token Factory (OpenAI-compatible endpoint) -----------------------
TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1/"
NEBIUS_API_KEY = env("NEBIUS_API_KEY")

# Nemotron 3 Nano triages every email (fast, cheap, thinking switched off).
# Nemotron 3 Super writes the replies (bigger model, reasoning left on).
TRIAGE_MODEL = env("TRIAGE_MODEL", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
WRITER_MODEL = env("WRITER_MODEL", "nvidia/nemotron-3-super-120b-a12b")

# --- Tavily (live web research so replies quote real facts) ------------------
TAVILY_API_KEY = env("TAVILY_API_KEY")
LEAD_RESEARCH = env_bool("LEAD_RESEARCH", True) and bool(TAVILY_API_KEY)
# Where the business operates. Added to location-specific searches so results are local.
BUSINESS_REGION = env("BUSINESS_REGION", "Ontario, Canada")
SEARCH_COUNTRY = env("SEARCH_COUNTRY", "canada")  # Tavily country boost; leave blank to disable

# --- Behaviour ---------------------------------------------------------------
DRY_RUN = env_bool("DRY_RUN", True)  # True = never send, only log the drafts
POLL_INTERVAL_SECS = int(env("POLL_INTERVAL_SECS", "60"))
IGNORE_OLDER_MAIL = env_bool("IGNORE_OLDER_MAIL", True)  # leave the inbox backlog alone; only mail from today on
MAX_PER_CYCLE = int(env("MAX_PER_CYCLE", "5"))  # at most this many emails per poll
BUSINESS_PROFILE = ROOT / "business" / env("BUSINESS_PROFILE", "demo_tree_service.md")
REPLY_DOMAIN = env("REPLY_DOMAIN", "resolvops.ai")  # used inside generated Message-IDs

# --- Property operations (tenant calls, tickets, dispatch) -------------------
PROPERTY_PROFILE = ROOT / "business" / env("PROPERTY_PROFILE", "demo_property_ops.md")
DATA_DIR = ROOT / "data"  # ticket store read by the dashboard
# Post-call triage and dispatch run once per ticket, so the bigger model is worth its extra seconds.
OPS_MODEL = env("OPS_MODEL", WRITER_MODEL)
# What agent.py does with the mailbox: "property" opens tickets and dispatches (tenant inbox),
# "frontdesk" drafts customer replies for a service business.
INBOX_MODE = env("INBOX_MODE", "property")

# --- Voice brain for Retell (custom LLM over WebSocket) ----------------------
# Nemotron 3.5 Lightning answers a spoken turn in about half a second.
VOICE_MODEL = env("VOICE_MODEL", "nvidia/Nemotron-3_5-Lightning")
VOICE_PORT = int(env("VOICE_PORT", "8000"))
AGENT_NAME = env("AGENT_NAME", "Aria")

# --- SMS through Twilio (optional; without it, texts are logged, not sent) ---
TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = env("TWILIO_FROM_NUMBER")
SMS_DRY_RUN = env_bool("SMS_DRY_RUN", True)
# Demo redirect: when set, every text goes to this one number, labelled with who it was for,
# so a demo can never page a real contractor. Leave blank in production.
DEMO_PHONE = env("DEMO_PHONE")

# --- Staff emails (emergency alerts, morning summary) sent from the mailbox above ----
EMAIL_NOTIFY_DRY_RUN = env_bool("EMAIL_NOTIFY_DRY_RUN", False)
# Same idea as DEMO_PHONE: every staff email goes to this one address instead. Blank in production.
DEMO_EMAIL = env("DEMO_EMAIL")

# --- Mailbox (only agent.py needs this; demo.py never touches email) ---------
IMAP = {
    "host": env("IMAP_HOST", "imap.gmail.com"),
    "port": int(env("IMAP_PORT", "993")),
    "username": env("EMAIL_USERNAME"),
    "password": env("EMAIL_PASSWORD"),
    "mailbox": env("IMAP_MAILBOX", "INBOX"),
}
SMTP = {
    "host": env("SMTP_HOST", "smtp.gmail.com"),
    "port": int(env("SMTP_PORT", "587")),
    "security": env("SMTP_SECURITY", "starttls"),  # starttls | tls | none
    "username": env("EMAIL_USERNAME"),
    "password": env("EMAIL_PASSWORD"),
}


def load_business_knowledge(path: Path | None = None) -> str:
    """The business profile the models are told about (plain Markdown)."""
    return (path or BUSINESS_PROFILE).read_text(encoding="utf-8").strip()
