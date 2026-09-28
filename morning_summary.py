"""Morning summary: email the office everything the front desk handled overnight.

    python morning_summary.py                 # tickets from the last 16 hours, emailed to the profile's summary addresses
    python morning_summary.py --hours 24      # a wider window
    python morning_summary.py --preview       # print it, send nothing
    python morning_summary.py --to you@x.com  # override the recipients

Nemotron writes the three-sentence overview at the top; the ticket list underneath is built
straight from the ticket store, so the numbers are always exact. Schedule it for 7 am with
Windows Task Scheduler or cron.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

import llm
import notify
import settings
import tickets

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

OVERVIEW_SYSTEM = """You write the three-sentence overview at the top of a property manager's morning report.
Plain words, no bullet points, no headings. Say how busy the night was, name anything that was an emergency
and what was done about it, and flag any ticket that still needs a callback number or permission to enter.
Never invent details that are not in the list."""


def overnight_tickets(hours: float) -> list[tickets.Ticket]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = []
    for t in tickets.load_all():
        try:
            created = datetime.fromisoformat(t.created_at)
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created >= cutoff:
            rows.append(t)
    rank = {"emergency": 0, "urgent": 1, "routine": 2}
    rows.sort(key=lambda t: (rank.get(t.urgency, 3), t.created_at))
    return rows


def local_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%a %I:%M %p").replace(" 0", " ")
    except ValueError:
        return iso


def ticket_lines(rows: list[tickets.Ticket]) -> str:
    if not rows:
        return "No tickets overnight."
    out = []
    for t in rows:
        where = f"unit {t.unit or '?'}" + (f", {t.building}" if t.building else "")
        out.append(f"{t.urgency.upper():9s} {t.id}  {local_time(t.created_at)}  {where}  via {t.channel}")
        out.append(f"          {t.issue or t.summary}")
        who = t.tenant_name or "name unknown"
        contact = t.phone or t.email or "no contact details"
        out.append(f"          {who}, {contact}. Assigned: {t.assigned_to or 'Office'}. Status: {t.status.replace('_', ' ')}.")
        if t.missing:
            out.append(f"          Still missing: {', '.join(m.replace('_', ' ') for m in t.missing)}")
        out.append("")
    return "\n".join(out).rstrip()


def build_report(rows: list[tickets.Ticket], hours: float, company: str) -> tuple[str, str]:
    counts = {k: sum(1 for t in rows if t.urgency == k) for k in ("emergency", "urgent", "routine")}
    listing = ticket_lines(rows)
    if rows:
        try:
            overview = llm.chat(settings.WRITER_MODEL, OVERVIEW_SYSTEM, f"Window: last {hours:g} hours.\n\n{listing}", temperature=0.3, max_tokens=1200)
        except Exception as exc:
            overview = f"(overview unavailable: {exc})"
    else:
        overview = "A quiet night. No tickets came in."
    today = datetime.now().strftime("%A %B %d").replace(" 0", " ")
    subject = f"Morning summary {today}: {counts['emergency']} emergency, {counts['urgent']} urgent, {counts['routine']} routine"
    body = "\n".join([
        f"{company}: overnight front desk summary, last {hours:g} hours",
        "",
        overview.strip(),
        "",
        f"Emergencies: {counts['emergency']}   Urgent: {counts['urgent']}   Routine: {counts['routine']}   Total: {len(rows)}",
        "",
        listing,
        "",
        f"Live board: http://localhost:{settings.VOICE_PORT}/",
    ])
    return subject, body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hours", type=float, default=16, help="how far back to look (default 16, i.e. since 5 pm yesterday at 9 am)")
    parser.add_argument("--preview", action="store_true", help="print the report instead of emailing it")
    parser.add_argument("--to", nargs="*", help="recipients, overriding the profile's morning summary addresses")
    args = parser.parse_args()

    knowledge = settings.load_business_knowledge(settings.PROPERTY_PROFILE)
    company = knowledge.splitlines()[0].lstrip("# ").split("(")[0].strip()
    rows = overnight_tickets(args.hours)
    subject, body = build_report(rows, args.hours, company)

    print(subject)
    print("-" * 72)
    print(body)
    print("-" * 72)
    if args.preview:
        print("(preview only, nothing sent)")
        return 0

    recipients = args.to or tickets.notification_emails(knowledge, "Morning summary")
    if not recipients:
        print("No recipients: add '- Morning summary emails: ...' to the profile or pass --to")
        return 1
    result = notify.send_email(recipients, subject, body, label="morning summary: " + ", ".join(recipients))
    print(f"email {result['status']} to {result['to']}" + (f" ({result.get('error')})" if result.get("error") else ""))
    return 0 if result["status"] in ("sent", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
