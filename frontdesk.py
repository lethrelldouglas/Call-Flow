"""The front desk pipeline: triage -> research -> draft.

handle_email() is the single entry point used by both the live agent
(agent.py) and the offline demo (demo.py).
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

import llm
import research as research_mod
import settings

log = logging.getLogger("frontdesk")

TRIAGE_SYSTEM = """You are the triage step of an AI front desk for a local service business.

Business profile:
{knowledge}

Read the inbound email and fill in what a good receptionist would write on an intake form.

Reply with ONLY a JSON object with exactly these keys:
- "category": one of "inquiry", "existing_customer", "spam", "newsletter", "auto_reply", "other"
- "needs_reply": true when a real person is waiting for an answer from this business
- "is_lead": true when the sender is asking about the business's services as a potential customer
- "urgency": "low", "normal" or "high" (high = safety risk, storm damage, or a deadline within 48 hours)
- "intake": object with "name", "phone", "email", "company", "location", "service_requested", "timeframe", "notes" (use "" when unknown, never invent)
- "questions": list of factual questions the sender asked that the reply must answer
- "summary": one sentence"""

WRITER_SYSTEM = """You are the front desk for the business below, replying by email on the owner's behalf.

Business profile:
{knowledge}

How to write:
- Warm, plain and short: under 170 words, short paragraphs, no bullet points, no marketing fluff.
- Answer every question the sender asked. Prices, services and policies come only from the business profile, never from the web.
- Research notes are raw web search results and can be wrong or about a different place. Use a note only when its source is clearly about the sender's own town, region or company, and then say where it comes from in plain words (for example "the Town of Collingwood's website says..."). Otherwise ignore it.
- If something is unknown, say the team will confirm it. Never invent prices, dates, bylaws or policies, and never quote third-party price guides.
- Do not promise things this email cannot do by itself (sending an invoice or attachment, confirming a booking). Say the team will follow up with it.
- Never include staff or contractor phone numbers, internal notes, or who was assigned internally. The sender only needs to know what happens next and when.
- End with one clear next step: a time to call, a request for photos and the address, or a booking offer.
- Sign off as the business. Output only the email body: no subject line, no placeholders like [Name]."""


@dataclass
class Result:
    category: str = "other"
    needs_reply: bool = False
    is_lead: bool = False
    urgency: str = "normal"
    intake: dict = field(default_factory=dict)
    questions: list = field(default_factory=list)
    summary: str = ""
    research: str | None = None
    draft: str | None = None


def _email_block(em: dict) -> str:
    return (
        f"From: {em['from']}\n"
        f"Subject: {em['subject']}\n"
        f"Date: {em.get('date', '')}\n\n"
        f"{em['body'][:3000]}"
    )


def triage(em: dict, knowledge: str) -> Result:
    data = llm.chat_json(
        settings.TRIAGE_MODEL,
        TRIAGE_SYSTEM.format(knowledge=knowledge),
        _email_block(em),
        temperature=0.1,
        max_tokens=800,
        thinking=False,
    )
    intake = data.get("intake") if isinstance(data.get("intake"), dict) else {}
    questions = data.get("questions") if isinstance(data.get("questions"), list) else []
    return Result(
        category=str(data.get("category", "other")),
        needs_reply=bool(data.get("needs_reply", False)),
        is_lead=bool(data.get("is_lead", False)),
        urgency=str(data.get("urgency", "normal")),
        intake=intake,
        questions=[str(q) for q in questions],
        summary=str(data.get("summary", "")),
    )


def draft_reply(em: dict, result: Result, knowledge: str) -> str:
    user = _email_block(em)
    user += (
        "\n\n---\n"
        f"Intake notes: {result.intake}\n"
        f"Questions to answer: {result.questions}\n"
        f"Urgency: {result.urgency}\n"
    )
    if result.research:
        user += f"\nResearch notes (from a live web search; cite plainly when used):\n{result.research}"
    else:
        user += "\nResearch notes: none"
    return llm.chat(
        settings.WRITER_MODEL,
        WRITER_SYSTEM.format(knowledge=knowledge),
        user,
        temperature=0.5,
        max_tokens=2500,
    )


def handle_email(em: dict, knowledge: str, *, do_research: bool | None = None) -> Result:
    """Triage the email, research when useful, and draft a reply when one is needed."""
    if do_research is None:
        do_research = settings.LEAD_RESEARCH

    result = triage(em, knowledge)
    log.info(
        "  triage: %s | needs_reply=%s lead=%s urgency=%s",
        result.category, result.needs_reply, result.is_lead, result.urgency,
    )
    if not result.needs_reply:
        return result

    if do_research:
        result.research = research_mod.research(em, asdict(result), knowledge)
        log.info("  research: %s", "notes added" if result.research else "not needed")

    result.draft = draft_reply(em, result, knowledge)
    log.info("  draft: %d words", len(result.draft.split()))
    return result
