# ResolvOps Front Desk

A 24/7 front desk for property managers and local service businesses, running on NVIDIA Nemotron 3 through Nebius Token Factory, with Tavily for live web research.

A tenant calls at 2 am about water coming through the ceiling. The agent talks them through the safety step, collects the unit, callback number and access details, opens a ticket, texts the on-call plumber and the property manager, confirms by text to the tenant, and the whole thing appears on the live operations board before the call has ended. Emails and texts go through the same intake. Spam and auto-replies are skipped. Nothing is sent to a real phone until you turn dry-run off.

Built for the Nebius x NVIDIA Global AI Hackathon, Best Apps and Agents track, by Lethrell Douglas, founder of [ResolvOps](https://resolvops.ai). ResolvOps already runs AI phone and email coverage for businesses in Ontario on closed models; this is the open-model rebuild, shaped by a proposal for a 400-tenant property manager.

## How it works

```
phone call ─── Retell (phone line, speech to text, text to speech)
                  │  transcript, every turn, over a WebSocket
                  ▼
           voice_server.py ── Nemotron 3.5 Lightning writes the next thing to say
                  │            (streamed back in ~0.6 s; thinking off)
                  │  between turns: Nano extracts name / unit / phone / access
                  ▼ call ends
email ──┐   tickets.triage_request ── Nemotron 3 Super fills the work order:
text ───┤        category, urgency, emergency flags, what is still missing
        ▼
   tickets.dispatch ── Super applies the routing rules from the property profile:
        │              emergency → matching contractor + manager, urgent → superintendent,
        │              routine → office. Phone numbers are checked against the profile,
        │              so the model cannot invent one.
        ▼
   notify.send_sms (Twilio, or logged in dry run)  +  data/tickets.json  →  dashboard at /
```

The email front desk for service businesses (`agent.py`, `demo.py`) uses the same models: Nano triages every email in about a second, a Nano planner decides whether a Tavily search would change the answer (a permit question gets the town's own bylaw page), and Super writes the reply.

## Try it in five minutes

```bash
pip install -r requirements.txt
copy .env.example .env          # paste NEBIUS_API_KEY and TAVILY_API_KEY
python check_keys.py            # one Nemotron call and one Tavily search
python ops_demo.py --reset      # six tenant messages → tickets, dispatch texts, email replies
python voice_server.py          # then open http://localhost:8000 for the live board
```

Recorded runs: [samples/ops_demo_output.txt](samples/ops_demo_output.txt) and [samples/demo_output.txt](samples/demo_output.txt). `python tests/voice_smoke.py` holds a scripted phone call against the server the way Retell would and checks that a ticket comes out.

## Put it on a real phone line

Retell owns the number and the voice; this server is the brain.

1. Start the server: `python voice_server.py`
2. Give it a public address. Either of these works from a laptop:
   - `cloudflared tunnel --url http://localhost:8000` (install with `winget install Cloudflare.cloudflared`)
   - `ssh -R 80:localhost:8000 nokey@localhost.run` (no install, uses the ssh already on Windows)
   Both print an `https://…` address.
3. In the Retell dashboard, open your agent, set the LLM to **Custom LLM**, and enter `wss://<that address>/llm-websocket`. Retell appends the call id itself.
4. Optionally set the agent's webhook to `https://<that address>/webhook`, so a call that drops before the WebSocket finishes still becomes a ticket.
5. Use Retell's test-call button, then call the number.

Texts stay in dry-run (logged on the ticket, not sent) until you add Twilio credentials to `.env` and set `SMS_DRY_RUN=false`. For a demo, also set `DEMO_PHONE` to your own cell: every text the agent would send goes there instead, prefixed with who it was for, so a rehearsal can never page a real contractor.

## Emergencies get a phone call, not just a text

A text can sit unread. For emergency tickets the server phones the assigned contractor through Twilio, reads the ticket out loud and asks them to press 1 to accept. Accepting flips the ticket to in progress and texts the tenant who is on the way. Declining, no answer, or no keypress within `ESCALATION_MINUTES` moves the chain on: contractor, then the on-call manager, then a final "nobody accepted" text to the manager. The chain state lives on the ticket and shows on the board.

Twilio fetches the call script from this server, so the server needs its public address: `PUBLIC_URL` in `.env`, or, when blank, the tunnel address from `tunnel.log`. `DEMO_PHONE` redirects the calls too.

## Staff email: emergency alerts and the morning summary

Emergencies also email the addresses listed under `- Emergency emails:` in the property profile, with the full ticket, the texts that went out, and what the tenant said. The mail goes from the same Gmail account the agent reads.

```bash
python morning_summary.py            # overnight tickets, emailed to the profile's "Morning summary emails"
python morning_summary.py --preview  # print it, send nothing
```

Nemotron writes the three-sentence overview at the top; the list below it comes straight from the ticket store. Schedule it for 7 am with Task Scheduler or cron. `DEMO_EMAIL` redirects all staff email to one inbox, the same way `DEMO_PHONE` does for texts.

## Files

| File | What it does |
|---|---|
| `voice_server.py` | Retell WebSocket brain, post-call ticketing, dashboard and JSON API |
| `web/dashboard.html` | Live operations board: emergencies first, one-click status changes |
| `tickets.py` | Work-order triage, dispatch planning with the roster guardrail, JSON store |
| `notify.py` | Text messages through Twilio, or logged in dry run |
| `ops_demo.py` | Tenant emails, a text and a call transcript through the whole flow |
| `agent.py`, `demo.py`, `frontdesk.py`, `mailer.py`, `research.py` | The email front desk for service businesses |
| `llm.py` | Nemotron calls through Token Factory's OpenAI-compatible API |
| `settings.py` | Configuration, read from `.env` |
| `business/` | Profiles the models are told about: a property manager and a tree service, both fictional |
| `samples/` | Sample inboxes and recorded runs |
| `tests/` | `test_offline.py` needs no keys; `voice_smoke.py` simulates a Retell call |

## Nebius, NVIDIA and Tavily features used

- Token Factory's OpenAI-compatible endpoint, so the standard `openai` package is the only client, including streaming for the voice path.
- `nvidia/Nemotron-3_5-Lightning` for live speech turns: first token in about half a second with `chat_template_kwargs: {"enable_thinking": false}`.
- `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` for in-call extraction, email triage and research planning, with `response_format: json_object`.
- `nvidia/nemotron-3-super-120b-a12b` for work-order triage, dispatch decisions and written replies, reasoning left on. Token Factory returns the reasoning in a separate field, so nothing spoken or sent ever contains thinking text.
- Tavily Search with `include_answer` and a country boost for the questions a tenant or customer asks that a business profile cannot answer.
- Zero Data Retention enabled on the Token Factory account: calls and emails are never stored by the model provider.

## What is next

- Tenant texts in through Twilio, on the same intake.
- Contractor check-in: the crew texts "on site" or "done" and the ticket updates itself.
- Tavily during the call: utility outage and weather checks for no-heat and no-power reports.
- Per-building profiles so one deployment fronts a whole portfolio.

## License

MIT, see [LICENSE](LICENSE).
