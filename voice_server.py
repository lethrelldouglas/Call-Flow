"""Voice brain for Retell, plus the live operations dashboard.

Retell handles the phone line, speech-to-text and text-to-speech. For every turn of a
call it sends the transcript here over a WebSocket; Nemotron (via Nebius Token Factory)
writes what the agent says next and it is streamed back word by word. When the call
ends, the transcript becomes a ticket, the dispatcher decides who to text, and the
dashboard at / updates.

Run:     python voice_server.py                       (http://localhost:8000)
Expose:  cloudflared tunnel --url http://localhost:8000
Retell:  agent settings -> Custom LLM URL -> wss://<your-tunnel-host>/llm-websocket
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from openai import AsyncOpenAI

import settings
import tickets

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
for _noisy in ("twilio", "httpx", "httpcore", "openai"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)  # keep the call log readable
log = logging.getLogger("frontdesk.voice")

app = FastAPI(title="ResolvOps Front Desk")
KNOWLEDGE = settings.load_business_knowledge(settings.PROPERTY_PROFILE)
DASHBOARD = Path(__file__).resolve().parent / "web" / "dashboard.html"
END_CALL_MARK = "[END_CALL]"
GREETING = f"Thanks for calling the after-hours line, this is {settings.AGENT_NAME}. What's going on?"

VOICE_SYSTEM = """You are {agent_name}, the after-hours phone line for the property manager below. You are on a live phone call and everything you write is spoken aloud.

Property profile:
{knowledge}

Your job on this call:
1. If the caller describes an emergency from the profile, give the safety instruction first, in one or two sentences.
2. Collect, one question at a time: the caller's name, the unit number and building, the best callback number (if the caller id is known, just confirm it), what the problem is and where, whether it is still happening, and permission to enter with any pets. Take whatever the caller volunteers and only ask about what is still missing.
3. When nothing is missing, read the details back in one sentence and ask if that is right.
4. When they confirm, say who is being notified and when they will hear back, following the profile's rules exactly, then ask if there is anything else.
5. When they say there is nothing else, say goodbye.

How to speak:
- At most 40 words per turn, then stop and wait. Plain words. No lists, no headings, no emojis.
- This is a live transcript with speech-recognition errors. Guess what the caller meant and keep going; ask them to repeat only when you must.
- If the caller does not answer a question, move on to the next one and come back to it later. Never ask the same question twice in a row.
- Refer to who is coming as "our emergency contractor", "the superintendent" or "the office", never by company or personal name. Never give out phone numbers. Never promise a repair time beyond the profile's rules. Never diagnose the cause.
- Do not repeat a question the caller already answered.

Known so far from this call (blank means unknown): {known}
Still needed: {missing}

When the caller confirms there is nothing else, say goodbye and finish your reply with the exact text {end_mark}"""

EXTRACT_SYSTEM = """Extract intake details from a live phone transcript for a property manager's after-hours line.
Reply with ONLY a JSON object with the keys "tenant_name", "phone", "unit", "building", "issue", "still_happening", "permission_to_enter", "pets".
Use "" for anything not said yet. Never guess."""


class CallState:
    def __init__(self, call_id: str) -> None:
        self.call_id = call_id
        self.from_number = ""
        self.transcript: list[dict] = []
        self.known: dict = {}
        self.latest_response_id = -1
        self.finalized = False
        self.extracting = False


CALLS: dict[str, CallState] = {}
_llm: AsyncOpenAI | None = None


def llm_client() -> AsyncOpenAI:
    global _llm
    if _llm is None:
        _llm = AsyncOpenAI(base_url=settings.TOKEN_FACTORY_URL, api_key=settings.NEBIUS_API_KEY)
    return _llm


def transcript_text(transcript: list[dict]) -> str:
    return "\n".join(
        f"{'Agent' if u.get('role') == 'agent' else 'User'}: {u.get('content', '')}" for u in transcript
    )


def missing_fields(state: CallState) -> list[str]:
    wanted = ["tenant_name", "unit", "phone", "issue", "permission_to_enter"]
    missing = [f for f in wanted if not str(state.known.get(f, "")).strip()]
    if "phone" in missing and state.from_number:
        missing[missing.index("phone")] = "phone (confirm caller id)"
    return missing


def build_messages(state: CallState, interaction_type: str) -> list[dict]:
    known = {k: state.known.get(k, "") for k in ("tenant_name", "unit", "building", "phone", "issue", "permission_to_enter", "pets")}
    if state.from_number and not known["phone"]:
        known["phone"] = f"{state.from_number} (caller id, not yet confirmed)"
    system = VOICE_SYSTEM.format(
        agent_name=settings.AGENT_NAME,
        knowledge=KNOWLEDGE,
        known=json.dumps(known, ensure_ascii=False),
        missing=", ".join(missing_fields(state)) or "nothing, wrap up the call",
        end_mark=END_CALL_MARK,
    )
    messages = [{"role": "system", "content": system}]
    for utterance in state.transcript:
        role = "assistant" if utterance.get("role") == "agent" else "user"
        content = (utterance.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    if interaction_type == "reminder_required":
        messages.append({"role": "user", "content": "(The caller has gone quiet. Gently check they are still there and repeat your last question.)"})
    return messages


def split_safe(buffer: str) -> tuple[str, str]:
    """Split streamed text so a partially received end-call mark is held back, not spoken."""
    for size in range(min(len(buffer), len(END_CALL_MARK) - 1), 0, -1):
        if END_CALL_MARK.startswith(buffer[-size:]):
            return buffer[:-size], buffer[-size:]
    return buffer, ""


async def stream_reply(state: CallState, request: dict):
    """Yield Retell response events for one turn, streamed from Nemotron."""
    response_id = request["response_id"]
    stream = await llm_client().chat.completions.create(
        model=settings.VOICE_MODEL,
        messages=build_messages(state, request["interaction_type"]),
        stream=True,
        temperature=0.4,
        max_tokens=220,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    buffer, end_call = "", False
    try:
        async for chunk in stream:
            if state.latest_response_id != response_id:
                break  # the caller spoke again; Retell wants a fresh response
            piece = chunk.choices[0].delta.content if chunk.choices and chunk.choices[0].delta else None
            if not piece:
                continue
            buffer += piece
            if END_CALL_MARK in buffer:
                end_call = True
                spoken = buffer.split(END_CALL_MARK)[0]
                buffer = ""
                if spoken.strip():
                    yield {"response_type": "response", "response_id": response_id, "content": spoken, "content_complete": False}
                break
            emit, buffer = split_safe(buffer)
            if emit:
                yield {"response_type": "response", "response_id": response_id, "content": emit, "content_complete": False}
    finally:
        try:
            await stream.close()  # release the HTTP stream cleanly when we stop early
        except Exception:
            pass
    if buffer.strip() and not end_call:
        yield {"response_type": "response", "response_id": response_id, "content": buffer, "content_complete": False}
    yield {"response_type": "response", "response_id": response_id, "content": "", "content_complete": True, "end_call": end_call}


def extract_known(state: CallState) -> None:
    """Update what the call has established so far (runs in a worker thread between turns)."""
    import llm  # sync client, used off the event loop

    try:
        data = llm.chat_json(
            settings.TRIAGE_MODEL, EXTRACT_SYSTEM, transcript_text(state.transcript),
            temperature=0.0, max_tokens=400, thinking=False,
        )
        state.known = {k: str(v or "").strip() for k, v in data.items()}
    except Exception as exc:
        log.warning("extraction skipped: %s", exc)


def finalize_call(state: CallState) -> tickets.Ticket | None:
    """Turn the finished call into a ticket and dispatch it. Safe to call more than once."""
    if state.finalized:
        return tickets.find_by_source(state.call_id)
    state.finalized = True
    user_said_anything = any(u.get("role") != "agent" and (u.get("content") or "").strip() for u in state.transcript)
    if not user_said_anything:
        log.info("call %s: caller said nothing, no ticket", state.call_id)
        return None
    text = transcript_text(state.transcript)
    try:
        triage = tickets.triage_request(text, "phone", KNOWLEDGE)
        if not triage.get("is_request", True):
            log.info("call %s: not a request, no ticket", state.call_id)
            return None
        ticket = tickets.new_ticket(triage, "phone", transcript=text, source_ref=state.call_id, caller_phone=state.from_number)
        tickets.save(ticket)
        plan = tickets.dispatch(ticket, KNOWLEDGE)
        log.info("call %s -> ticket %s (%s, %s), assigned to %s, %d text(s)",
                 state.call_id, ticket.id, ticket.urgency, ticket.category, plan["assign_to"], len(plan["notifications"]))
        return ticket
    except Exception as exc:
        log.error("call %s: could not create ticket: %s", state.call_id, exc)
        return None


@app.websocket("/llm-websocket/{call_id}")
async def llm_websocket(websocket: WebSocket, call_id: str):
    await websocket.accept()
    state = CALLS.setdefault(call_id, CallState(call_id))
    log.info("call %s: connected", call_id)
    await websocket.send_json({"response_type": "config", "config": {"auto_reconnect": True, "call_details": True}})
    await websocket.send_json({"response_type": "response", "response_id": 0, "content": GREETING, "content_complete": True, "end_call": False})

    async def handle(event: dict) -> None:
        kind = event.get("interaction_type")
        if kind == "ping_pong":
            await websocket.send_json({"response_type": "ping_pong", "timestamp": event.get("timestamp")})
            return
        if kind == "call_details":
            call = event.get("call", {}) or {}
            state.from_number = call.get("from_number", "") or ""
            log.info("call %s: %s from %s", call_id, call.get("call_type", "call"), state.from_number or "unknown number")
            return
        if "transcript" in event:
            state.transcript = event["transcript"] or []
        if kind not in ("response_required", "reminder_required"):
            return
        state.latest_response_id = event["response_id"]
        try:
            async for reply in stream_reply(state, event):
                await websocket.send_json(reply)
                if reply.get("end_call"):
                    log.info("call %s: agent ended the call", call_id)
        except Exception as exc:
            log.error("call %s: model error: %s", call_id, exc)
            await websocket.send_json({"response_type": "response", "response_id": event["response_id"],
                                       "content": "Sorry, I missed that. Could you say it again?", "content_complete": True})
        if kind == "response_required" and not state.extracting:
            state.extracting = True
            try:
                await asyncio.to_thread(extract_known, state)
            finally:
                state.extracting = False

    try:
        async for event in websocket.iter_json():
            asyncio.create_task(handle(event))
    except WebSocketDisconnect:
        log.info("call %s: disconnected", call_id)
    except Exception as exc:
        log.error("call %s: websocket error: %s", call_id, exc)
    finally:
        await asyncio.to_thread(finalize_call, state)
        CALLS.pop(call_id, None)


@app.post("/webhook")
async def retell_webhook(request: Request):
    """Retell's post-call events. If the WebSocket never produced a ticket, the transcript here does."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid json"})
    event = payload.get("event")
    call = payload.get("call") or payload.get("data") or {}
    call_id = call.get("call_id", "")
    log.info("webhook: %s for %s", event, call_id or "?")
    if event in ("call_ended", "call_analyzed") and call_id and not tickets.find_by_source(call_id):
        raw = call.get("transcript_object") or []
        transcript = [{"role": u.get("role"), "content": u.get("content", "")} for u in raw] if raw else []
        if not transcript and call.get("transcript"):
            transcript = [{"role": "user", "content": call["transcript"]}]
        if transcript:
            state = CallState(call_id)
            state.transcript = transcript
            state.from_number = call.get("from_number", "") or ""
            await asyncio.to_thread(finalize_call, state)
    if event == "call_analyzed" and call_id:
        ticket = tickets.find_by_source(call_id)
        summary = (call.get("call_analysis") or {}).get("call_summary")
        if ticket and summary:
            ticket.log_action(f"Retell call summary: {summary}")
            tickets.save(ticket)
    return {"received": True}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD.read_text(encoding="utf-8")


@app.get("/api/meta")
async def meta():
    first_line = KNOWLEDGE.splitlines()[0].lstrip("# ").split("(")[0].strip()
    return {"company": first_line, "agent": settings.AGENT_NAME, "voice_model": settings.VOICE_MODEL,
            "triage_model": settings.TRIAGE_MODEL, "sms": "live" if not settings.SMS_DRY_RUN else "dry run"}


@app.get("/api/tickets")
async def list_tickets():
    rows = [asdict(t) for t in tickets.load_all()]
    rank = {"emergency": 0, "urgent": 1, "routine": 2}
    open_first = {"new": 0, "dispatched": 0, "in_progress": 0, "done": 1}
    rows.sort(key=lambda r: (open_first.get(r["status"], 0), rank.get(r["urgency"], 3), r["created_at"]), reverse=False)
    rows.sort(key=lambda r: (open_first.get(r["status"], 0), rank.get(r["urgency"], 3)))
    return rows


@app.post("/api/tickets/{ticket_id}/status")
async def set_status(ticket_id: str, request: Request):
    body = await request.json()
    ticket = tickets.get(ticket_id)
    if not ticket:
        return JSONResponse(status_code=404, content={"error": "no such ticket"})
    new_status = body.get("status")
    if new_status not in ("new", "dispatched", "in_progress", "done"):
        return JSONResponse(status_code=400, content={"error": "bad status"})
    ticket.status = new_status
    ticket.log_action(f"status set to {new_status} from the dashboard")
    tickets.save(ticket)
    return asdict(ticket)


@app.get("/health")
async def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}


if __name__ == "__main__":
    if not settings.NEBIUS_API_KEY:
        raise SystemExit("NEBIUS_API_KEY is empty in .env")
    log.info("Property profile: %s | voice model: %s | SMS: %s",
             settings.PROPERTY_PROFILE.name, settings.VOICE_MODEL, "dry run" if settings.SMS_DRY_RUN else "LIVE")
    uvicorn.run(app, host="0.0.0.0", port=settings.VOICE_PORT, log_level="warning")
