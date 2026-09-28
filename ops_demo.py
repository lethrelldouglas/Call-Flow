"""Property operations demo: tenant messages in, tickets and dispatch out.

Runs the sample tenant inbox (emails, a text, a phone transcript) through intake triage,
opens tickets, plans who to notify, sends the texts (dry run unless Twilio is configured
and SMS_DRY_RUN=false), and drafts an email reply where one is due. Tickets land in
data/tickets.json, so start voice_server.py afterwards to see them on the dashboard.

    python ops_demo.py            # all samples, appends to existing tickets
    python ops_demo.py --reset    # clear the ticket store first
    python ops_demo.py --only 2   # one sample
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

import ops
import settings
import tickets
from llm import USAGE

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")


def message_text(sample: dict) -> str:
    if sample["channel"] == "phone":
        return sample["body"]
    return f"From: {sample['from']}\nSubject: {sample['subject']}\nDate: {sample['date']}\n\n{sample['body']}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", type=int, metavar="N", help="run only sample number N (1-based)")
    parser.add_argument("--reset", action="store_true", help="clear data/tickets.json first")
    parser.add_argument("--samples", default="samples/tenant_inbox.json")
    args = parser.parse_args()

    knowledge = settings.load_business_knowledge(settings.PROPERTY_PROFILE)
    samples = json.loads((settings.ROOT / args.samples).read_text(encoding="utf-8"))
    if args.reset:
        tickets.clear()
        print("ticket store cleared")

    print(f"Property profile : {settings.PROPERTY_PROFILE.name}")
    print(f"Triage/dispatch  : {settings.TRIAGE_MODEL}")
    print(f"Reply writer     : {settings.WRITER_MODEL}")
    print(f"Texts            : {'LIVE via Twilio' if not settings.SMS_DRY_RUN else 'dry run (logged, not sent)'}")

    for number, sample in enumerate(samples, start=1):
        if args.only and number != args.only:
            continue
        print("\n" + "=" * 72)
        print(f"#{number}  [{sample['channel']}] {sample['from']}")
        print(f"    {sample['subject']}")
        print("=" * 72)
        started = time.time()
        draft = None

        if sample["channel"] == "email":
            em = {"from": sample["from"], "subject": sample["subject"], "date": sample["date"], "body": sample["body"]}
            outcome = ops.handle_tenant_email(em, knowledge, source_ref=f"sample-{number}")
            if not outcome.ticket:
                print(f"Not a request ({outcome.note}). No ticket.")
                print(f"\n({time.time() - started:.1f}s)")
                continue
            ticket, plan, draft = outcome.ticket, outcome.plan, outcome.draft
        else:
            triage = tickets.triage_request(message_text(sample), sample["channel"], knowledge)
            if not triage.get("is_request", True):
                print(f"Not a request ({triage.get('summary', '')}). No ticket.")
                print(f"\n({time.time() - started:.1f}s)")
                continue
            ticket = tickets.new_ticket(triage, sample["channel"], transcript=sample["body"], source_ref=f"sample-{number}", caller_phone=sample["from"])
            tickets.save(ticket)
            plan = tickets.dispatch(ticket, knowledge)

        print(f"Ticket   : {ticket.id} | {ticket.urgency.upper()} | {ticket.category} | unit {ticket.unit or '?'} {ticket.building}")
        print(f"Tenant   : {ticket.tenant_name or '?'} {ticket.phone or ticket.email}")
        print(f"Issue    : {ticket.issue}")
        if ticket.emergency_flags:
            print(f"Flags    : {', '.join(ticket.emergency_flags)}")
        if ticket.missing:
            print(f"Missing  : {', '.join(ticket.missing)}")
        print(f"Assigned : {plan['assign_to'] or '?'}  ({plan['eta']})")
        for note in plan["notifications"]:
            print(f"  -> text {note['to_name']} {note['to_phone']}: {note['message']}")
        if plan["tenant_message"]:
            print(f"  -> text tenant: {plan['tenant_message']}")

        if draft:
            print("\nEmail reply:\n")
            for line in draft.splitlines():
                print("   " + line)
        print(f"\n({time.time() - started:.1f}s)")

    print("\n" + "-" * 72)
    print(f"{len(tickets.load_all())} ticket(s) in data/tickets.json. Start voice_server.py to see them on the dashboard.")
    print("Token usage:")
    print(USAGE.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
