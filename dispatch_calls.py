"""Emergency acknowledgement by phone.

After the texts and email go out for an emergency, Twilio phones the assigned contractor,
reads the ticket out loud and asks them to press 1 to accept. Accepting marks the ticket in
progress and tells the tenant who is on the way. If nobody accepts within ESCALATION_MINUTES,
or the call is declined, busy or unanswered, the next contact in the chain is called:
contractor first, then the on-call manager. When the chain runs out, the manager is texted
that the job is still unaccepted.

State lives on the ticket in `ticket.ack`, so it survives restarts and shows on the board.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

import notify
import settings
import tickets

log = logging.getLogger("frontdesk.calls")

SAY_VOICE = "Polly.Joanna"
_lock = threading.Lock()  # one escalation step at a time, whichever thread noticed first


def _now() -> datetime:
    return datetime.now(timezone.utc)


def company_name(knowledge: str) -> str:
    first = knowledge.splitlines()[0] if knowledge else ""
    return first.lstrip("# ").split("(")[0].strip() or "the property manager"


def on_call_manager(knowledge: str) -> dict | None:
    """The first line in the profile mentioning a manager together with a phone number."""
    for line in knowledge.splitlines():
        if "manager" in line.lower():
            phones = tickets.phone_numbers_in(line)
            if phones:
                after_colon = line.split(":", 1)[1] if ":" in line else line
                name = after_colon.split(",")[0].strip().lstrip("-").strip() or "the on-call manager"
                return {"name": name, "phone": "+1" + sorted(phones)[0]}
    return None


def build_chain(ticket: tickets.Ticket, plan: dict, knowledge: str) -> list[dict]:
    """Who to ring, in order: the assigned contractor, then the on-call manager."""
    chain: list[dict] = []
    notes = plan.get("notifications", []) or []
    for n in notes:
        if n.get("to_name") == plan.get("assign_to") and n.get("to_phone"):
            chain.append({"name": n["to_name"], "phone": n["to_phone"]})
            break
    if not chain:
        for n in notes:
            if "manager" not in n.get("to_name", "").lower() and n.get("to_phone"):
                chain.append({"name": n["to_name"], "phone": n["to_phone"]})
                break
    manager = on_call_manager(knowledge)
    if manager and all(c["phone"] != manager["phone"] for c in chain):
        chain.append(manager)
    return chain


def spoken_digits(number: str) -> str:
    digits = re.sub(r"\D", "", number or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return " ".join(digits)


def spoken_id(ticket_id: str) -> str:
    return " ".join(ch for ch in ticket_id if ch != "-")


def _say(text: str) -> str:
    return f'<Say voice="{SAY_VOICE}">{escape(text)}</Say>'


def twiml_closed() -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{_say("This ticket is no longer open. Goodbye.")}</Response>'


def twiml_for(ticket: tickets.Ticket, knowledge: str) -> str:
    """What Twilio says when the contact picks up, with a keypad prompt."""
    base = settings.public_url()
    where = f"unit {ticket.unit or 'unknown'}" + (f" at {ticket.building}" if ticket.building else "")
    callback = spoken_digits(ticket.phone) if ticket.phone else "no callback number"
    tenant = ticket.tenant_name or "unknown"
    full = (
        f"This is {settings.AGENT_NAME} from {company_name(knowledge)} with an emergency dispatch. "
        f"Ticket {spoken_id(ticket.id)}. {where}. {ticket.issue or ticket.summary} "
        f"The tenant is {tenant}, callback number {callback}. "
        "Press 1 to accept this job. Press 2 if you cannot take it."
    )
    short = f"{where}. {ticket.issue or ticket.summary} Press 1 to accept, 2 to decline."
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n'
        f'  <Gather numDigits="1" timeout="6" action="{base}/voice/dispatch/{ticket.id}/answer" method="POST">\n'
        f"    {_say(full)}\n    <Pause length=\"1\"/>\n    {_say('Once more. ' + short)}\n"
        "  </Gather>\n"
        f"  {_say('No response received. The next contact will be called. Goodbye.')}\n"
        "</Response>"
    )


def _current(ticket: tickets.Ticket) -> dict | None:
    ack = ticket.ack or {}
    chain, index = ack.get("chain", []), ack.get("index", 0)
    return chain[index] if 0 <= index < len(chain) else None


def _outcome(result: dict) -> str:
    if result.get("to") and result.get("to") != result.get("intended"):
        return f"{result['status']}, redirected to {result['to']}"
    return result["status"]


def _call_current(ticket: tickets.Ticket) -> None:
    contact = _current(ticket)
    if not contact:
        return
    base = settings.public_url()
    attempt_no = len(ticket.ack.get("attempts", [])) + 1
    if not base and settings.CALL_PROVIDER != "retell":
        ticket.ack["state"] = "no_public_url"
        ticket.log_action(f"cannot call {contact['name']}: no public address (is the tunnel running?)")
        tickets.save(ticket)
        return
    if settings.CALL_PROVIDER == "retell":
        result = notify.place_call_retell(contact["phone"], ticket.id, label=contact["name"])
    else:
        result = notify.place_call(
            contact["phone"], f"{base}/voice/dispatch/{ticket.id}", f"{base}/voice/dispatch/{ticket.id}/status",
            label=contact["name"],
        )
    ticket.ack.setdefault("attempts", []).append({
        "name": contact["name"], "phone": contact["phone"], "at": _now().isoformat(timespec="seconds"),
        "result": result["status"], "sid": result.get("sid", ""),
    })
    ticket.ack["next_at"] = (_now() + timedelta(minutes=settings.ESCALATION_MINUTES)).isoformat(timespec="seconds")
    if result["status"] == "placed":
        ticket.ack["state"] = "calling"
        how = "Nemotron will ask if they can take it" if settings.CALL_PROVIDER == "retell" else "press 1 to accept"
        ticket.log_action(f"calling {contact['name']} [{_outcome(result)}] attempt {attempt_no}: {how}")
        tickets.save(ticket)
    elif result["status"] == "dry_run":
        ticket.ack["state"] = "dry_run"
        ticket.log_action(f"call to {contact['name']} [dry run] attempt {attempt_no}")
        tickets.save(ticket)
    else:
        ticket.log_action(f"call to {contact['name']} failed: {result.get('error', '')[:120]}")
        _escalate(ticket)


def start_acknowledgement(ticket: tickets.Ticket, plan: dict, knowledge: str) -> None:
    """Begin the call chain for an emergency ticket."""
    chain = build_chain(ticket, plan, knowledge)
    if not chain:
        ticket.log_action("nobody to call for acknowledgement (no contractor with a number)")
        tickets.save(ticket)
        return
    with _lock:
        ticket.ack = {"state": "calling", "chain": chain, "index": 0, "attempts": [], "next_at": "", "accepted_by": ""}
        _call_current(ticket)


def _escalate(ticket: tickets.Ticket) -> None:
    ack = ticket.ack
    ack["index"] = ack.get("index", 0) + 1
    if ack["index"] < len(ack.get("chain", [])):
        nxt = ack["chain"][ack["index"]]
        ticket.log_action(f"escalating to {nxt['name']}")
        _call_current(ticket)
        return
    ack["state"] = "unacknowledged"
    ack["next_at"] = ""
    ticket.log_action("nobody accepted the job; escalation chain exhausted")
    manager = ack["chain"][-1] if ack.get("chain") else None
    if manager:
        msg = (f"URGENT: nobody has accepted emergency ticket {ticket.id}, unit {ticket.unit or '?'}"
               f"{', ' + ticket.building if ticket.building else ''}: {ticket.issue}. Please handle.")
        result = notify.send_sms(manager["phone"], msg, label=manager["name"])
        ticket.log_action(f"text to {manager['name']} [{_outcome(result)}]: {msg}")
    tickets.save(ticket)


def handle_answer(ticket_id: str, digits: str) -> str:
    """The contact pressed a key. Returns the TwiML to speak back."""
    with _lock:
        ticket = tickets.get(ticket_id)
        if not ticket or not ticket.ack or ticket.ack.get("state") in ("accepted", "unacknowledged"):
            return twiml_closed()
        contact = _current(ticket) or {"name": "the contact", "phone": ""}
        if digits.strip() == "1":
            ticket.ack["state"] = "accepted"
            ticket.ack["accepted_by"] = contact["name"]
            ticket.ack["next_at"] = ""
            ticket.status = "in_progress"
            ticket.assigned_to = contact["name"]
            ticket.log_action(f"{contact['name']} accepted the job by phone")
            if ticket.phone:
                msg = (f"Update on ticket {ticket.id}: {contact['name']} has accepted and is on the way to "
                       f"unit {ticket.unit or '?'}. Reply to this text if anything changes.")
                result = notify.send_sms(ticket.phone, msg, label="tenant")
                ticket.log_action(f"text to tenant [{_outcome(result)}]: {msg}")
            tickets.save(ticket)
            return ('<?xml version="1.0" encoding="UTF-8"?><Response>'
                    + _say("Thank you. The tenant has been told you are on the way. Goodbye.") + "</Response>")
        ticket.log_action(f"{contact['name']} declined by phone")
        _escalate(ticket)
        return ('<?xml version="1.0" encoding="UTF-8"?><Response>'
                + _say("Understood. The next contact will be called. Goodbye.") + "</Response>")


def handle_status(ticket_id: str, call_status: str) -> None:
    """Twilio's end-of-call report. A call that ended without acceptance moves the chain on."""
    with _lock:
        ticket = tickets.get(ticket_id)
        if not ticket or not ticket.ack or ticket.ack.get("state") != "calling":
            return
        if call_status in ("no-answer", "busy", "failed", "canceled", "completed"):
            contact = _current(ticket) or {"name": "the contact"}
            ticket.log_action(f"call to {contact['name']}: {call_status}, not accepted")
            _escalate(ticket)


DISPATCH_CALL_SYSTEM = """You are {agent_name}, calling {contact} on behalf of {company} about an emergency. You are on a live phone call; everything you write is spoken aloud.

The job:
- Ticket {ticket_id}: {where}. {issue}
- Tenant: {tenant}. Callback number: {callback}.
- Access: {access}

Your only goal is to find out whether {contact} can take this job now.
- Open by saying who you are and that it is an emergency dispatch, give the unit and the problem in one sentence, and ask if they can take it.
- Answer questions from the details above only. If asked something you do not know, say the tenant can tell them on the callback number.
- If they accept: say you will tell the tenant they are on the way, say goodbye, and finish your reply with the exact text {accept_mark}
- If they decline or cannot come soon: say you will call the next contact, say goodbye, and finish with the exact text {decline_mark}
- If you reach voicemail or an answering machine: leave a two-sentence message with the unit, the problem and the callback number, then finish with {decline_mark}
- At most 35 words per turn. Plain words, no lists."""

ACCEPT_MARK = "[ACCEPT]"
DECLINE_MARK = "[DECLINE]"


def dispatch_call_prompt(ticket: tickets.Ticket, knowledge: str) -> str:
    """System prompt for the Retell dispatcher conversation about one ticket."""
    contact = _current(ticket) or {"name": "the contractor"}
    where = f"unit {ticket.unit or 'unknown'}" + (f" at {ticket.building}" if ticket.building else "")
    callback = ticket.phone or "no callback number"
    access = ticket.details or ("permission to enter still to confirm" if "permission_to_enter" in ticket.missing else "see tenant")
    return DISPATCH_CALL_SYSTEM.format(
        agent_name=settings.AGENT_NAME, contact=contact["name"], company=company_name(knowledge),
        ticket_id=ticket.id, where=where, issue=ticket.issue or ticket.summary, tenant=ticket.tenant_name or "unknown",
        callback=callback, access=access, accept_mark=ACCEPT_MARK, decline_mark=DECLINE_MARK,
    )


def tick() -> int:
    """Timer fallback: escalate calls that have waited longer than ESCALATION_MINUTES. Returns how many."""
    moved = 0
    now = _now()
    with _lock:
        for ticket in tickets.load_all():
            ack = ticket.ack or {}
            if ack.get("state") != "calling" or not ack.get("next_at"):
                continue
            try:
                due = datetime.fromisoformat(ack["next_at"])
            except ValueError:
                continue
            if due <= now:
                ticket.log_action(f"no acceptance within {settings.ESCALATION_MINUTES:g} minutes")
                _escalate(ticket)
                moved += 1
    return moved
