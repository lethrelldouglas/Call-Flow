"""IMAP polling and SMTP replies.

Behaviour is ported unchanged from the original ResolvOps agent: unseen
messages are fetched and marked as read, and replies go out in-thread with
the right In-Reply-To and References headers.
"""
from __future__ import annotations

import email
import email.header
import email.policy
import email.utils
import html
import imaplib
import logging
import re
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger("frontdesk.mail")


def decode_header_value(raw: str) -> str:
    if not raw:
        return ""
    decoded = []
    try:
        for part, charset in email.header.decode_header(raw):
            if isinstance(part, bytes):
                try:
                    decoded.append(part.decode(charset or "utf-8", errors="replace"))
                except Exception:
                    decoded.append(part.decode("utf-8", errors="replace"))
            else:
                decoded.append(str(part))
    except Exception as exc:
        log.warning("Header decode failed: %s", exc)
        return str(raw)
    return "".join(decoded)


def _html_to_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _decoded_payload(part) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")


def extract_text_body(msg) -> str:
    """Plain-text body of a (possibly multipart) email; falls back to stripped HTML."""
    try:
        parts = list(msg.walk()) if msg.is_multipart() else [msg]
        for part in parts:
            disposition = str(part.get("Content-Disposition", ""))
            if part.get_content_type() == "text/plain" and "attachment" not in disposition:
                text = _decoded_payload(part)
                if text:
                    return text
        for part in parts:
            if part.get_content_type() == "text/html":
                text = _html_to_text(_decoded_payload(part))
                if text:
                    return text
        if not msg.is_multipart():
            return _decoded_payload(msg)
    except Exception as exc:
        log.warning("Body extraction failed: %s", exc)
    return ""


def sender_address(raw_from: str) -> str:
    _, addr = email.utils.parseaddr(raw_from)
    return addr.strip() or raw_from.strip()


def build_references(msg) -> list[str]:
    refs: list[str] = []
    for header in ("References", "In-Reply-To"):
        refs.extend(re.findall(r"<[^>]+>", msg.get(header, "")))
    mid = (msg.get("Message-ID") or "").strip()
    if mid and mid not in refs:
        refs.append(mid)
    return refs


def fetch_unseen(imap_cfg: dict, since=None, limit: int | None = None) -> list[dict]:
    """Fetch unseen messages, mark the fetched ones as read, and return parsed dicts.

    `since` (a date) ignores older unread mail, so pointing the agent at a busy inbox
    does not process its backlog. `limit` caps how many are handled per call, newest first
    in arrival order; the rest stay unread for the next cycle.
    """
    emails: list[dict] = []
    imap = imaplib.IMAP4_SSL(imap_cfg["host"], int(imap_cfg["port"]))
    imap.login(imap_cfg["username"], imap_cfg["password"])
    imap.select(imap_cfg.get("mailbox", "INBOX"))

    criteria = ["UNSEEN"]
    if since:
        criteria.append(f"SINCE {since:%d-%b-%Y}")
    _, data = imap.search(None, *criteria)
    uids = data[0].split()
    if limit and len(uids) > limit:
        log.info("IMAP: %d unseen message(s), handling %d this cycle", len(uids), limit)
        uids = uids[-limit:]
    else:
        log.info("IMAP: %d unseen message(s)", len(uids))

    for uid in uids:
        try:
            _, raw = imap.fetch(uid, "(RFC822)")
            if not raw or raw[0] is None or raw[0][1] is None:
                continue
            msg = email.message_from_bytes(raw[0][1], policy=email.policy.compat32)
            emails.append({
                "uid": uid.decode(),
                "message_id": str(msg.get("Message-ID") or "").strip(),
                "from": decode_header_value(str(msg.get("From") or "")),
                "subject": decode_header_value(str(msg.get("Subject") or "(no subject)")),
                "body": extract_text_body(msg),
                "references": build_references(msg),
                "date": str(msg.get("Date") or ""),
            })
            imap.store(uid, "+FLAGS", "\\Seen")
        except Exception as exc:
            log.warning("Skipping malformed email: %s", exc)

    imap.logout()
    return emails


def _smtp_session(smtp_cfg: dict) -> smtplib.SMTP:
    host, port = smtp_cfg["host"], int(smtp_cfg["port"])
    security = smtp_cfg.get("security", "starttls").lower()
    if security == "tls":
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        if security == "starttls":
            server.starttls()
    server.login(smtp_cfg["username"], smtp_cfg["password"])
    return server


def send_email(smtp_cfg: dict, to_addrs: list[str], subject: str, body: str, from_name: str = "") -> str:
    """Send a plain-text email (used for staff notifications). Returns the Message-ID."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    message_id = f"<notify.{stamp}@{smtp_cfg['username'].split('@')[-1]}>"
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = f"{from_name} <{smtp_cfg['username']}>" if from_name else smtp_cfg["username"]
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    server = _smtp_session(smtp_cfg)
    server.sendmail(smtp_cfg["username"], to_addrs, msg.as_string())
    server.quit()
    return message_id


def send_reply(smtp_cfg: dict, original: dict, reply_body: str, reply_domain: str) -> str:
    """Send reply_body as an in-thread reply to the original message. Returns the new Message-ID."""
    to_addr = sender_address(original["from"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    new_message_id = f"<frontdesk.{stamp}@{reply_domain}>"

    msg = MIMEMultipart("alternative")
    msg["From"] = smtp_cfg["username"]
    msg["To"] = to_addr
    msg["Subject"] = f"Re: {original['subject']}"
    msg["Message-ID"] = new_message_id
    if original.get("message_id"):
        msg["In-Reply-To"] = original["message_id"]
    if original.get("references"):
        msg["References"] = " ".join(original["references"])
    msg.attach(MIMEText(reply_body, "plain", "utf-8"))

    host, port = smtp_cfg["host"], int(smtp_cfg["port"])
    security = smtp_cfg.get("security", "starttls").lower()
    if security == "tls":
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        if security == "starttls":
            server.starttls()
    server.login(smtp_cfg["username"], smtp_cfg["password"])
    server.sendmail(smtp_cfg["username"], [to_addr], msg.as_string())
    server.quit()
    return new_message_id
