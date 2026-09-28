"""Outbound text messages.

Texts are really sent only when Twilio credentials are in .env and SMS_DRY_RUN=false.
Otherwise every message is logged and returned with status "dry_run", so the whole
dispatch flow can be demonstrated without a phone number.
"""
from __future__ import annotations

import logging
import re

import settings

log = logging.getLogger("frontdesk.notify")


def normalize_phone(raw: str) -> str:
    """Return a +1XXXXXXXXXX number for North American input, or the digits found."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return ("+" + digits) if digits else ""


def configured() -> bool:
    return bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM_NUMBER)


def send_sms(to: str, body: str, label: str = "") -> dict:
    """Send one text. Never raises; the returned dict says what happened.

    `label` names the intended recipient (for example "Rapid Flow Plumbing" or "tenant").
    With DEMO_PHONE set, the text is redirected there and the label is prefixed to the body.
    """
    to_number = normalize_phone(to)
    intended = to_number
    if settings.DEMO_PHONE:
        to_number = normalize_phone(settings.DEMO_PHONE)
        body = f"[for {label or intended or 'unknown'}] {body}"
    record = {"to": to_number, "intended": intended, "body": body, "status": "dry_run"}
    if not to_number:
        record["status"] = "failed"
        record["error"] = "no phone number"
        return record

    if settings.SMS_DRY_RUN or not configured():
        reason = "SMS_DRY_RUN=true" if settings.SMS_DRY_RUN else "Twilio not configured"
        log.info("[SMS %s] to %s: %s", reason, to_number, body)
        return record

    try:
        from twilio.rest import Client

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        message = client.messages.create(to=to_number, from_=settings.TWILIO_FROM_NUMBER, body=body)
        record.update(status="sent", sid=message.sid)
        log.info("[SMS sent] to %s (%s)", to_number, message.sid)
    except Exception as exc:
        record.update(status="failed", error=str(exc))
        log.error("[SMS failed] to %s: %s", to_number, exc)
    return record
