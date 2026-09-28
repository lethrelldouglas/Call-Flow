"""Run the front desk on sample emails, without touching a real mailbox.

    python demo.py                    # every sample, Tavily research on when the key is set
    python demo.py --no-research      # Nemotron only
    python demo.py --only 1           # just the first sample
    python demo.py --profile resolvops.md
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

import settings
from frontdesk import handle_email
from llm import USAGE

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", type=int, metavar="N", help="run only sample number N (1-based)")
    parser.add_argument("--no-research", action="store_true", help="skip the Tavily step")
    parser.add_argument("--profile", default=None, help="file name inside business/ (default: from .env)")
    parser.add_argument("--samples", default="samples/inbox.json", help="JSON file with sample emails")
    args = parser.parse_args()

    profile = settings.ROOT / "business" / args.profile if args.profile else settings.BUSINESS_PROFILE
    knowledge = settings.load_business_knowledge(profile)
    samples = json.loads((settings.ROOT / args.samples).read_text(encoding="utf-8"))
    do_research = settings.LEAD_RESEARCH and not args.no_research

    print(f"Business profile : {profile.name}")
    print(f"Triage model     : {settings.TRIAGE_MODEL}")
    print(f"Writer model     : {settings.WRITER_MODEL}")
    print(f"Tavily research  : {'on' if do_research else 'off'}")

    for number, em in enumerate(samples, start=1):
        if args.only and number != args.only:
            continue
        print("\n" + "=" * 72)
        print(f"#{number}  From: {em['from']}")
        print(f"    Subject: {em['subject']}")
        print("=" * 72)

        started = time.time()
        result = handle_email(em, knowledge, do_research=do_research)

        print(f"Triage   : {result.category} | needs_reply={result.needs_reply} | lead={result.is_lead} | urgency={result.urgency}")
        print(f"Summary  : {result.summary}")
        filled = {k: v for k, v in result.intake.items() if v}
        if filled:
            print("Intake   : " + ", ".join(f"{k}={v}" for k, v in filled.items()))
        if result.questions:
            print("Questions: " + " | ".join(result.questions))
        if result.research:
            print("\nResearch notes:")
            for line in result.research.splitlines():
                print("   " + line[:160])
        if result.draft:
            print("\nDraft reply:\n")
            for line in result.draft.splitlines():
                print("   " + line)
        else:
            print("\nNo reply needed.")
        print(f"\n({time.time() - started:.1f}s)")

    print("\n" + "-" * 72)
    print("Token usage:")
    print(USAGE.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
