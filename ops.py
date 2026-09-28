"""Tenant emails and texts through the property pipeline: ticket, dispatch, reply.

Used by agent.py (live mailbox, INBOX_MODE=property) and ops_demo.py (samples).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import frontdesk
import mailer
import tickets

log = logging.getLogger("frontdesk.ops")


@dataclass
class OpsResult:
    ticket: tickets.Ticket | None
    plan: dict | None
    draft: str | None
    note: str = ""


def handle_tenant_email(em: dict, knowledge: str, *, source_ref: str = "") -> OpsResult:
    """Triage the email into a work order, dispatch it, and draft the reply to the tenant."""
    text = f"From: {em['from']}\nSubject: {em['subject']}\nDate: {em.get('date', '')}\n\n{em['body']}"
    triage = tickets.triage_request(text, "email", knowledge)
    if not triage.get("is_request", True):
        return OpsResult(None, None, None, note=triage.get("summary", "not a request"))

    ticket = tickets.new_ticket(
        triage, "email",
        transcript=em["body"],
        source_ref=source_ref or em.get("message_id", ""),
        email=mailer.sender_address(em["from"]),
    )
    tickets.save(ticket)
    plan = tickets.dispatch(ticket, knowledge)

    result = frontdesk.Result(
        category="inquiry", needs_reply=True, is_lead=False, urgency=ticket.urgency,
        intake={
            "name": ticket.tenant_name, "unit": ticket.unit, "building": ticket.building,
            "phone": ticket.phone, "ticket number": ticket.id, "what happens next": plan["eta"],
        },
        questions=ticket.questions, summary=ticket.summary,
    )
    draft = frontdesk.draft_reply(em, result, knowledge)
    ticket.log_action("email reply drafted")
    tickets.save(ticket)
    log.info("  ticket %s: %s, %s, assigned to %s", ticket.id, ticket.urgency, ticket.category, ticket.assigned_to or "Office")
    return OpsResult(ticket, plan, draft)
