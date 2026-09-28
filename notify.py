"""Outbound text messages.

Texts are really sent only when Twilio credentials are in .env and SMS_DRY_RUN=false.
Otherwise every message is logged and returned with status "dry_run", so the whole
dispatch flow can be demonstrated without a phone number.
"""
from __future__ import annotations

import logging
import re

import mailer
import settings

log = logging.getLogger("frontdesk.notify")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def email_configured() -> bool:
    return bool(settings.SMTP["username"] and settings.SMTP["password"])


def send_email(to: list[str] | str, subject: str, body: str, label: str = "") -> dict:
    """Email one or more staff addresses. Never raises; the returned dict says what happened.

    With DEMO_EMAIL set, the mail is redirected there and the intended recipients are noted
    at the top of the body. With EMAIL_NOTIFY_DRY_RUN=true it is only logged.
    """
    intended = [a.strip() for a in ([to] if isinstance(to, str) else to) if a and EMAIL_RE.fullmatch(a.strip())]
    record = {"to": intended, "intended": intended, "subject": subject, "status": "dry_run"}
    if not intended:
        record.update(status="failed", error="no valid email address")
        return record
    recipients = intended
    if settings.DEMO_EMAIL:
        recipients = [settings.DEMO_EMAIL]
        body = f"[demo: this was for {label or ', '.join(intended)}]\n\n{body}"
        record["to"] = recipients
    if settings.EMAIL_NOTIFY_DRY_RUN or not email_configured():
        reason = "EMAIL_NOTIFY_DRY_RUN=true" if settings.EMAIL_NOTIFY_DRY_RUN else "mailbox not configured"
        log.info("[EMAIL %s] to %s: %s", reason, recipients, subject)
        return record
    try:
        mid = mailer.send_email(settings.SMTP, recipients, subject, body, from_name=f"{settings.AGENT_NAME} (front desk)")
        record.update(status="sent", message_id=mid)
        log.info("[EMAIL sent] to %s: %s", recipients, subject)
    except Exception as exc:
        record.update(status="failed", error=str(exc))
        log.error("[EMAIL failed] to %s: %s", recipients, exc)
    return record


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
