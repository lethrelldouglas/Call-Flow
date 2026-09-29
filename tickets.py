"""Maintenance tickets: the record every tenant request becomes, whatever channel it came in on.

triage_request() turns a message or call transcript into a work-order form (Nemotron Nano),
plan_dispatch() decides who gets notified and what to say (Nano, held to the roster in the
property profile), and the JSON store under data/ feeds the live dashboard.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone

import llm
import notify
import settings

log = logging.getLogger("frontdesk.tickets")

CATEGORIES = [
    "plumbing", "hvac", "electrical", "elevator", "appliance", "doors_windows_locks", "pests",
    "noise_or_neighbour", "cleaning_or_garbage", "renovation_or_contractor", "rent_or_admin", "other",
]
URGENCY = ["emergency", "urgent", "routine"]
EMERGENCY_FLAGS = [
    "flooding_or_active_leak", "sewage_backup", "gas_smell", "electrical_hazard", "no_heat", "no_water",
    "elevator_entrapment", "fire_or_smoke", "unit_unsecured", "person_in_danger",
]
REQUIRED_FIELDS = ["tenant_name", "phone", "unit"]

TRIAGE_SYSTEM = """You are the maintenance intake desk for a residential property manager.

Property profile:
{knowledge}

From the tenant's message (an email, a text, or a phone call transcript), fill in the work-order form. Apply the profile's emergency, urgent and routine rules exactly.

Reply with ONLY a JSON object with exactly these keys:
- "is_request": true when a tenant, contractor or staff member is reporting an issue or asking for something; false for spam, newsletters and auto-replies
- "category": one of {categories}
- "urgency": one of {urgency}. "emergency" only when an emergency flag applies or someone is unsafe; "urgent" when it affects daily living and should be handled within 24 hours; otherwise "routine"
- "emergency_flags": list drawn from {flags}, empty when none apply
- "tenant_name", "phone", "unit", "building": strings, "" when not given, never guess
- "issue": one sentence in plain words saying what the problem is and where
- "details": anything the crew needs (when it started, what the tenant tried, access, pets, permission to enter)
- "questions": list of questions the sender asked that need an answer
- "missing": the required items still unknown, drawn from ["tenant_name", "phone", "unit", "permission_to_enter"]. Include "permission_to_enter" only when a crew will need to enter the unit, never for noise complaints or questions
- "summary": one sentence for the dashboard"""

DISPATCH_SYSTEM = """You are the dispatcher for a residential property manager.

Property profile (the only people and phone numbers you may use):
{knowledge}

Given a work order, decide who handles it and who gets a text right now. Routing rules:
- emergency: assign the contractor whose trade matches the category (plumbing to the plumbing contractor, hvac to the heating contractor, electrical to the electrician, elevator to the elevator company, locks to the locksmith, spreading water damage to the restoration contractor). Text that contractor and text the property manager on call.
- urgent: assign the superintendent for that building and text them. Do not text the manager or a contractor.
- routine: assign "Office". Text nobody on staff; the office picks it up in business hours.
- Always write a confirmation text for the tenant.
The tenant's callback number is the "phone" field of the work order. If it is blank, write "no callback number" rather than inventing one.

Reply with ONLY a JSON object with exactly these keys:
- "assign_to": the contractor, the staff member, or "Office", named exactly as in the profile
- "notifications": list of objects {{"to_name": "...", "to_phone": "...", "message": "..."}} to text now, following the routing rules. Phone numbers must be copied from the profile. Each message under 300 characters with the ticket id {ticket_id}, unit, building, issue, urgency and the tenant's callback number.
- "tenant_message": a text to the tenant, under 300 characters, confirming ticket {ticket_id}, who is coming or when to expect a call, and any safety step to take now. Never include staff or contractor phone numbers. Never state things the profile does not say (for example whether water will be shut off).
- "eta": plain words for when someone will respond, following the rules"""


@dataclass
class Ticket:
    id: str
    created_at: str
    channel: str  # phone | email | sms | voicemail
    status: str = "new"  # new | dispatched | in_progress | done
    tenant_name: str = ""
    phone: str = ""
    email: str = ""
    unit: str = ""
    building: str = ""
    category: str = "other"
    urgency: str = "routine"
    emergency_flags: list = field(default_factory=list)
    issue: str = ""
    details: str = ""
    questions: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    summary: str = ""
    assigned_to: str = ""
    eta: str = ""
    actions: list = field(default_factory=list)  # what the agent did, in order
    transcript: str = ""
    source_ref: str = ""  # Retell call id, email Message-ID, SMS sid
    ack: dict = field(default_factory=dict)  # phone acknowledgement chain (see dispatch_calls.py)
    call_transcripts: list = field(default_factory=list)  # [{"with": name, "transcript": text}] for dispatcher calls

    @property
    def is_emergency(self) -> bool:
        return self.urgency == "emergency" or bool(self.emergency_flags)

    def log_action(self, text: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.actions.append(f"{stamp} {text}")


_lock = threading.Lock()


def _store_path():
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return settings.DATA_DIR / "tickets.json"


def _read_raw() -> list[dict]:
    path = _store_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("tickets.json is corrupt; starting a fresh store")
        return []


def _from_dict(data: dict) -> Ticket:
    known = {f.name for f in fields(Ticket)}
    return Ticket(**{k: v for k, v in data.items() if k in known})


def load_all() -> list[Ticket]:
    with _lock:
        return [_from_dict(d) for d in _read_raw()]


def get(ticket_id: str) -> Ticket | None:
    return next((t for t in load_all() if t.id == ticket_id), None)


def find_by_source(source_ref: str) -> Ticket | None:
    if not source_ref:
        return None
    return next((t for t in load_all() if t.source_ref == source_ref), None)


def save(ticket: Ticket) -> Ticket:
    """Insert or replace the ticket in the store."""
    with _lock:
        rows = [d for d in _read_raw() if d.get("id") != ticket.id]
        rows.append(asdict(ticket))
        _store_path().write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return ticket


def clear() -> None:
    with _lock:
        _store_path().write_text("[]", encoding="utf-8")


def new_id() -> str:
    return "NG-" + uuid.uuid4().hex[:6].upper()


def triage_request(text: str, channel: str, knowledge: str) -> dict:
    """Turn a tenant message or call transcript into the work-order form."""
    data = llm.chat_json(
        settings.OPS_MODEL,
        TRIAGE_SYSTEM.format(knowledge=knowledge, categories=CATEGORIES, urgency=URGENCY, flags=EMERGENCY_FLAGS),
        f"Channel: {channel}\n\n{text[:6000]}",
        temperature=0.1,
        max_tokens=2500,
    )
    if data.get("category") not in CATEGORIES:
        data["category"] = "other"
    if data.get("urgency") not in URGENCY:
        data["urgency"] = "routine"
    data["emergency_flags"] = [f for f in data.get("emergency_flags", []) if f in EMERGENCY_FLAGS]
    if data["emergency_flags"]:
        data["urgency"] = "emergency"
    for key in ("tenant_name", "phone", "unit", "building", "issue", "details", "summary"):
        data[key] = str(data.get(key) or "").strip()
    for key in ("questions", "missing"):
        data[key] = [str(x) for x in data.get(key, []) if x]
    return data


def new_ticket(triage: dict, channel: str, *, transcript: str = "", source_ref: str = "", caller_phone: str = "", email: str = "") -> Ticket:
    ticket = Ticket(id=new_id(), created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), channel=channel, email=email)
    for key in ("tenant_name", "phone", "unit", "building", "category", "urgency", "emergency_flags",
                "issue", "details", "questions", "missing", "summary"):
        if key in triage:
            setattr(ticket, key, triage[key])
    # A phone number needs at least 10 digits; anything else ("fine", "ending in 0177") falls back to caller id.
    if len(re.sub(r"\D", "", ticket.phone)) < 10:
        ticket.phone = caller_phone or ""
    if ticket.phone:
        ticket.missing = [m for m in ticket.missing if m != "phone"]
    elif "phone" not in ticket.missing:
        ticket.missing.append("phone")
    ticket.transcript = transcript
    ticket.source_ref = source_ref
    ticket.log_action(f"ticket opened from {channel}: {ticket.urgency}, {ticket.category}")
    return ticket


def notification_emails(knowledge: str, kind: str) -> list[str]:
    """Addresses listed in the profile under '- Emergency emails:' or '- Morning summary emails:'."""
    match = re.search(rf"(?im)^\s*-\s*{re.escape(kind)}\s+emails?\s*:\s*(.+)$", knowledge)
    if not match:
        return []
    return [a.strip() for a in re.split(r"[,\s]+", match.group(1)) if "@" in a]


def emergency_email(ticket: Ticket, plan: dict) -> tuple[str, str]:
    """Subject and body of the emergency alert for the manager, built straight from the ticket."""
    where = f"unit {ticket.unit or '?'}" + (f", {ticket.building}" if ticket.building else "")
    subject = f"EMERGENCY {ticket.id}: {where}: {ticket.issue or ticket.summary}"
    lines = [
        f"Emergency ticket {ticket.id} opened by {ticket.channel} at {ticket.created_at}.",
        "",
        f"Where:      {where}",
        f"Tenant:     {ticket.tenant_name or 'unknown'}",
        f"Callback:   {ticket.phone or 'no callback number'}" + (f" / {ticket.email}" if ticket.email else ""),
        f"Issue:      {ticket.issue}",
        f"Details:    {ticket.details or '-'}",
        f"Flags:      {', '.join(f.replace('_', ' ') for f in ticket.emergency_flags) or '-'}",
        f"Category:   {ticket.category.replace('_', ' ')}",
        f"Assigned:   {plan.get('assign_to') or ticket.assigned_to or '-'} ({plan.get('eta') or ticket.eta or '-'})",
        f"Still missing: {', '.join(m.replace('_', ' ') for m in ticket.missing) or 'nothing'}",
        "",
        "Texts sent:",
    ]
    lines += [f"  - {n['to_name']}: {n['message']}" for n in plan.get("notifications", [])] or ["  - none"]
    if ticket.transcript:
        lines += ["", "What the tenant said:", ticket.transcript[:2500]]
    lines += ["", f"Live board: http://localhost:{settings.VOICE_PORT}/"]
    return subject, "\n".join(lines)


def phone_numbers_in(text: str) -> set[str]:
    """Every phone number in the text, as bare digits, so dispatch can be checked against the roster."""
    found = set()
    for match in re.findall(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", text):
        digits = re.sub(r"\D", "", match)
        found.add(digits[-10:])
    return found


def plan_dispatch(ticket: Ticket, knowledge: str) -> dict:
    """Decide who to notify. Any number not in the profile is dropped, so the model cannot invent one."""
    order = {k: v for k, v in asdict(ticket).items() if k not in ("transcript", "actions")}
    plan = llm.chat_json(
        settings.OPS_MODEL,
        DISPATCH_SYSTEM.format(knowledge=knowledge, ticket_id=ticket.id),
        "Work order:\n" + json.dumps(order, ensure_ascii=False, indent=2),
        temperature=0.1,
        max_tokens=2500,
    )
    roster = phone_numbers_in(knowledge)
    checked = []
    for note in plan.get("notifications", []) or []:
        if not isinstance(note, dict):
            continue
        digits = re.sub(r"\D", "", str(note.get("to_phone", "")))[-10:]
        if digits and digits in roster:
            checked.append({"to_name": str(note.get("to_name", "")), "to_phone": "+1" + digits, "message": str(note.get("message", ""))})
        else:
            log.warning("Dropped a notification to %r: number not in the profile", note.get("to_name"))
    plan["notifications"] = checked
    plan["assign_to"] = str(plan.get("assign_to", "") or "")
    plan["tenant_message"] = str(plan.get("tenant_message", "") or "")
    plan["eta"] = str(plan.get("eta", "") or "")
    return plan


def dispatch(ticket: Ticket, knowledge: str) -> dict:
    """Plan the dispatch, send (or log) the texts, record every action on the ticket, and save it."""
    plan = plan_dispatch(ticket, knowledge)
    ticket.assigned_to = plan["assign_to"]
    ticket.eta = plan["eta"]
    def outcome(result: dict) -> str:
        if result.get("to") and result.get("to") != result.get("intended"):
            return f"{result['status']}, redirected to {result['to']}"
        return result["status"]

    for note in plan["notifications"]:
        result = notify.send_sms(note["to_phone"], note["message"], label=note["to_name"])
        ticket.log_action(f"text to {note['to_name']} [{outcome(result)}]: {note['message']}")
    if plan["tenant_message"]:
        if ticket.phone:
            result = notify.send_sms(ticket.phone, plan["tenant_message"], label="tenant")
            ticket.log_action(f"text to tenant [{outcome(result)}]: {plan['tenant_message']}")
        else:
            ticket.log_action(f"tenant message (no phone on file): {plan['tenant_message']}")

    if ticket.is_emergency:
        recipients = notification_emails(knowledge, "Emergency")
        if recipients:
            subject, body = emergency_email(ticket, plan)
            result = notify.send_email(recipients, subject, body, label="emergency contacts: " + ", ".join(recipients))
            ticket.log_action(f"email to {', '.join(recipients)} [{outcome(result)}]: {subject}")
        else:
            ticket.log_action("no emergency email addresses in the profile, email skipped")

    ticket.status = "dispatched" if plan["notifications"] else "new"
    save(ticket)

    if ticket.is_emergency and settings.CALL_CONTRACTORS:
        import dispatch_calls  # imported here because it imports this module

        dispatch_calls.start_acknowledgement(ticket, plan, knowledge)
    return plan
