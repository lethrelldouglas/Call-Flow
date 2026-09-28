"""Offline checks that need no API keys or network.

Run with either:
    python tests/test_offline.py
    pytest
"""
from __future__ import annotations

import email
import email.policy
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm  # noqa: E402
import mailer  # noqa: E402
import research  # noqa: E402


def _sample_message():
    msg = MIMEMultipart("alternative")
    msg["From"] = "=?utf-8?q?Ren=C3=A9e_L=C3=A9vesque?= <renee@example.com>"
    msg["Subject"] = "=?utf-8?q?Tree_quote_=E2=80=93_Thornbury?="
    msg["Message-ID"] = "<abc123@example.com>"
    msg["In-Reply-To"] = "<prev1@example.com>"
    msg.attach(MIMEText("<p>Hello <b>there</b></p>", "html", "utf-8"))
    msg.attach(MIMEText("Hello there, plain version.", "plain", "utf-8"))
    return email.message_from_bytes(msg.as_bytes(), policy=email.policy.compat32)


def test_encoded_headers_are_decoded():
    msg = _sample_message()
    assert mailer.decode_header_value(str(msg.get("From"))) == "Renée Lévesque <renee@example.com>"
    assert mailer.decode_header_value(str(msg.get("Subject"))) == "Tree quote – Thornbury"


def test_plain_text_body_is_preferred_over_html():
    assert mailer.extract_text_body(_sample_message()).strip() == "Hello there, plain version."


def test_html_only_body_is_stripped_to_text():
    msg = MIMEText("<p>Hi <b>Sam</b>,<br>quote please</p>", "html", "utf-8")
    parsed = email.message_from_bytes(msg.as_bytes(), policy=email.policy.compat32)
    assert "quote please" in mailer.extract_text_body(parsed)
    assert "<" not in mailer.extract_text_body(parsed)


def test_reply_threading_headers():
    msg = _sample_message()
    assert mailer.sender_address("Renée Lévesque <renee@example.com>") == "renee@example.com"
    assert mailer.build_references(msg) == ["<prev1@example.com>", "<abc123@example.com>"]


def test_sender_domain_detection():
    assert research.sender_domain("Dana <dana.k@gmail.com>") == "gmail.com"
    assert research.sender_domain("priya@maplehillcottages.example") == "maplehillcottages.example"
    assert research.sender_domain("no-address-here") == ""
    assert "gmail.com" in research.FREE_MAIL


def test_notification_emails_come_from_the_profile():
    import tickets
    profile = (
        "# Demo\n- Emergency emails: jordan@example.com\n"
        "- Morning summary emails: office@example.com, jordan@example.com\n"
    )
    assert tickets.notification_emails(profile, "Emergency") == ["jordan@example.com"]
    assert tickets.notification_emails(profile, "Morning summary") == ["office@example.com", "jordan@example.com"]
    assert tickets.notification_emails("# nothing here", "Emergency") == []


def test_emergency_email_carries_the_ticket_facts():
    import tickets
    t = tickets.Ticket(id="NG-TEST01", created_at="2026-09-28T02:00:00+00:00", channel="phone",
                       tenant_name="Amara Osei", phone="+14165550199", unit="412", building="Northgate Tower",
                       category="plumbing", urgency="emergency", emergency_flags=["flooding_or_active_leak"],
                       issue="Water pouring through the bathroom ceiling.", missing=["permission_to_enter"])
    plan = {"assign_to": "Rapid Flow Plumbing", "eta": "now",
            "notifications": [{"to_name": "Rapid Flow Plumbing", "to_phone": "+14165550150", "message": "go now"}]}
    subject, body = tickets.emergency_email(t, plan)
    assert subject.startswith("EMERGENCY NG-TEST01: unit 412, Northgate Tower")
    for needle in ("Amara Osei", "+14165550199", "flooding or active leak", "Rapid Flow Plumbing", "permission to enter", "go now"):
        assert needle in body, needle


def test_dispatch_never_texts_a_number_outside_the_profile():
    import tickets
    profile = "- Plumbing: Rapid Flow Plumbing, +1 416 555 0150, 24/7\n"
    assert tickets.phone_numbers_in(profile) == {"4165550150"}
    assert "4165550150" in tickets.phone_numbers_in("call 416-555-0150 today")


def test_call_chain_is_contractor_then_manager():
    import dispatch_calls
    import tickets
    profile = (
        "# Demo\n- Property manager on call: Jordan Pike, +1 416 555 0140, jp@example.com\n"
        "- Plumbing: Rapid Flow Plumbing, +1 416 555 0150, 24/7\n"
    )
    t = tickets.Ticket(id="NG-T", created_at="2026-09-28T02:00:00+00:00", channel="phone", unit="412")
    plan = {"assign_to": "Rapid Flow Plumbing", "notifications": [
        {"to_name": "Rapid Flow Plumbing", "to_phone": "+14165550150", "message": "x"},
        {"to_name": "Jordan Pike", "to_phone": "+14165550140", "message": "y"},
    ]}
    chain = dispatch_calls.build_chain(t, plan, profile)
    assert [c["name"] for c in chain] == ["Rapid Flow Plumbing", "Jordan Pike"]
    assert dispatch_calls.on_call_manager(profile) == {"name": "Jordan Pike", "phone": "+14165550140"}


def test_spoken_script_reads_the_ticket_and_asks_for_1():
    import dispatch_calls
    import settings
    import tickets
    settings.PUBLIC_URL = "https://example.trycloudflare.com"
    t = tickets.Ticket(id="NG-27E3B8", created_at="2026-09-28T02:00:00+00:00", channel="phone", tenant_name="Amara Osei",
                       phone="+14165550199", unit="412", building="Northgate Tower", issue="Water pouring through the ceiling.")
    t.ack = {"state": "calling", "chain": [{"name": "Rapid Flow Plumbing", "phone": "+14165550150"}], "index": 0}
    xml = dispatch_calls.twiml_for(t, "# Northgate Rentals\n")
    assert 'action="https://example.trycloudflare.com/voice/dispatch/NG-27E3B8/answer"' in xml
    assert "N G 2 7 E 3 B 8" in xml and "4 1 6 5 5 5 0 1 9 9" in xml and "Press 1 to accept" in xml
    assert dispatch_calls.spoken_digits("+1 (416) 555-0199") == "4 1 6 5 5 5 0 1 9 9"
    settings.PUBLIC_URL = ""


def test_chat_json_recovers_json_wrapped_in_prose():
    original = llm.chat
    llm.chat = lambda *a, **k: 'Sure, here it is:\n```json\n{"category": "spam", "needs_reply": false}\n```'
    try:
        data = llm.chat_json("any-model", "system", "user")
    finally:
        llm.chat = original
    assert data == {"category": "spam", "needs_reply": False}


def test_chat_json_rejects_non_json():
    original = llm.chat
    llm.chat = lambda *a, **k: "I cannot help with that."
    try:
        try:
            llm.chat_json("any-model", "system", "user")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for non-JSON output")
    finally:
        llm.chat = original


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print("all tests passed" if not failures else f"{failures} test(s) failed")
    sys.exit(1 if failures else 0)
