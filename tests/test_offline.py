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
