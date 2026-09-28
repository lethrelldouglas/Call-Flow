"""ResolvOps Front Desk agent.

Every POLL_INTERVAL_SECS it:
  1. fetches unseen emails over IMAP
  2. triages each one with NVIDIA Nemotron 3 Nano (via Nebius Token Factory)
  3. researches open questions with Tavily
  4. drafts the reply with NVIDIA Nemotron 3 Super
  5. sends it in-thread over SMTP, or just logs it when DRY_RUN=true

Run:   python agent.py
Stop:  Ctrl-C (finishes the current cycle first)
"""
from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import date

import mailer
import ops
import settings
from frontdesk import handle_email
from llm import USAGE

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(settings.ROOT / "agent.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("frontdesk.agent")

_running = True


def _handle_signal(sig, frame):
    global _running
    log.info("Shutdown signal received. Finishing the current cycle, then stopping.")
    _running = False


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def validate() -> None:
    """Fail fast with a clear message if something required is missing."""
    problems = []
    if not settings.NEBIUS_API_KEY:
        problems.append("NEBIUS_API_KEY is empty in .env")
    if not settings.IMAP["username"] or not settings.SMTP["username"]:
        problems.append("EMAIL_USERNAME is empty in .env")
    if not settings.IMAP["password"] or not settings.SMTP["password"]:
        problems.append("EMAIL_PASSWORD is empty in .env (Gmail needs an App Password)")
    if not active_profile().exists():
        problems.append(f"profile not found: {active_profile()}")
    if problems:
        log.error("Cannot start. Fix the following in .env first:")
        for p in problems:
            log.error("  - %s", p)
        raise SystemExit(1)


def active_profile():
    return settings.PROPERTY_PROFILE if settings.INBOX_MODE == "property" else settings.BUSINESS_PROFILE


def run() -> None:
    validate()
    property_mode = settings.INBOX_MODE == "property"
    knowledge = settings.load_business_knowledge(active_profile())

    log.info("=" * 60)
    log.info("ResolvOps Front Desk agent starting")
    log.info("  Inbox    : %s @ %s", settings.IMAP["username"], settings.IMAP["host"])
    log.info("  Mode     : %s", "property ops (tickets + dispatch + reply)" if property_mode else "front desk (replies)")
    log.info("  Profile  : %s", active_profile().name)
    log.info("  Triage   : %s", settings.TRIAGE_MODEL)
    log.info("  Writer   : %s", settings.WRITER_MODEL)
    log.info("  Research : %s", "Tavily on" if settings.LEAD_RESEARCH else "off")
    log.info("  Poll     : every %ss, at most %d emails per poll", settings.POLL_INTERVAL_SECS, settings.MAX_PER_CYCLE)
    log.info("  Backlog  : %s", "ignored (only mail from today on)" if settings.IGNORE_OLDER_MAIL else "will be processed")
    log.info("  Dry run  : %s  (no emails sent when True)", settings.DRY_RUN)
    log.info("=" * 60)

    stats = {"processed": 0, "replied": 0, "skipped": 0, "errors": 0}
    since = date.today() if settings.IGNORE_OLDER_MAIL else None

    while _running:
        cycle_start = time.time()
        log.info("-- Polling inbox --")
        try:
            emails = mailer.fetch_unseen(settings.IMAP, since=since, limit=settings.MAX_PER_CYCLE)
        except Exception as exc:
            log.error("IMAP fetch failed: %s", exc)
            stats["errors"] += 1
            emails = []

        for em in emails:
            log.info("Processing [%s] %r from %r", em["uid"], em["subject"], em["from"])
            stats["processed"] += 1
            try:
                if property_mode:
                    outcome = ops.handle_tenant_email(em, knowledge)
                    draft, skip_reason = outcome.draft, outcome.note
                else:
                    result = handle_email(em, knowledge)
                    draft, skip_reason = result.draft, result.category
            except Exception as exc:
                log.error("  model error: %s", exc)
                stats["errors"] += 1
                continue

            if not draft:
                log.info("  -> SKIP (%s)", skip_reason)
                stats["skipped"] += 1
                continue

            if settings.DRY_RUN:
                log.info("  -> DRY RUN, reply not sent. Draft:\n%s\n%s\n%s", "-" * 40, draft, "-" * 40)
                stats["replied"] += 1
                continue

            try:
                mid = mailer.send_reply(settings.SMTP, em, draft, settings.REPLY_DOMAIN)
                log.info("  -> SENT (Message-ID: %s)", mid)
                stats["replied"] += 1
            except Exception as exc:
                log.error("  SMTP send failed: %s", exc)
                stats["errors"] += 1

        log.info(
            "Cycle done. Total: %d processed, %d replied, %d skipped, %d errors.",
            stats["processed"], stats["replied"], stats["skipped"], stats["errors"],
        )
        deadline = cycle_start + settings.POLL_INTERVAL_SECS
        while _running and time.time() < deadline:
            time.sleep(1)

    log.info("Token usage this run:\n%s", USAGE.summary())
    log.info("Agent stopped cleanly.")


if __name__ == "__main__":
    run()
