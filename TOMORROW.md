# Event day checklist (Builders & Brews Toronto, Tue Sep 29, 2 PM, Propeller Coffee Co.)

Bring: laptop, charger, phone (Luma ticket + it receives every demo text).

## The short version: one double-click

1. Connect to the wifi (or your phone's hotspot).
2. Double-click **start_demo.bat** in this folder. It checks the keys, starts the server and the
   tunnel in their own windows, opens the dashboard, and updates the Retell agent to today's
   address by itself (it prints "websocket set: wss://..."). The address is also on your clipboard
   in case you ever need to paste it by hand.
3. In Retell, click **Publish** and attach the phone number (or use Test Audio).
4. Call it.

Leave the "Voice server" and "Tunnel" windows open all day. If either closes, double-click
start_demo.bat again and paste the new address into Retell.

The manual steps below do the same thing by hand.

## 1. On the venue wifi, prove the keys work (1 min)

```
cd "C:/Users/Lethr/OneDrive/Desktop/resolvops-frontdesk"
python check_keys.py
```

## 2. Start the brain and the dashboard (terminal 1)

```
python voice_server.py
```

Dashboard: http://localhost:8000

## 3. Open the tunnel (terminal 2)

```
cloudflared tunnel --url http://localhost:8000
```

It prints an address like `https://something-something.trycloudflare.com`.
The address changes every time cloudflared restarts, so do this step before touching Retell.

No cloudflared? Use the built-in ssh instead:
```
ssh -R 80:localhost:8000 nokey@localhost.run
```

## 4. Prove the tunnel works (terminal 3, optional but worth 30 seconds)

```
python tests/tunnel_smoke.py https://<the address from step 3>
```

## 5. Retell (already set up; only needed if starting from scratch)

The agent (**Custom LLM agent**, id `agent_d313c90db7ad09f38148374c2d`) already exists and is bound
to +16475561419 for both inbound and outbound. `start_demo.bat` re-points its Custom LLM URL and
webhook at today's tunnel address automatically (`retell_sync.py`) — you don't need to touch Retell
by hand. This is only for setting up a brand-new agent from zero:

1. Create an agent, type **Custom LLM**.
2. WebSocket URL: `wss://<the address from step 3>/llm-websocket` (Retell appends the call id itself).
3. Voice: any; every line the agent says comes from the server.
4. Webhook URL: `https://<the address>/webhook`.
5. Under the phone number, set both **Inbound** and **Outbound Call Agent** to this agent.
6. Publish, then test.

## 6. What happens on a call

**Inbound (tenant calls in).** Say: "There's water coming through my bathroom ceiling in unit 412
at Northgate Tower, it's still pouring." Answer what it asks: your name, "yes this number is fine",
"you can come in, I have a cat", "no that's everything".

Within about ten seconds of hanging up: a ticket appears on the dashboard, three texts land on your
phone (`[for Rapid Flow Plumbing]`, `[for Jordan Pike]`, `[for tenant]`), and the manager gets an
email with the full ticket.

**Outbound (the contractor call — the best part).** A few seconds later your phone rings again, from
the same Retell number. That's Ariyah calling "the plumber": "This is Ariyah from Northgate Rentals
with an emergency dispatch for Rapid Flow Plumbing... can you take this job now?" Say "yeah, I can
take it, heading over now." The ticket flips to in progress and the tenant gets a follow-up text.
Say no or don't answer and it escalates to the next contact instead — both are worth showing once.

## 7. Fill the board without a call

```
python ops_demo.py --reset
```

## 8. The morning-summary email (nice closer for the demo)

```
python morning_summary.py --hours 24
```

Lands in resolvops@gmail.com (DEMO_EMAIL) within seconds. Emergency tickets also email the
manager automatically; with DEMO_EMAIL set those land in the same inbox.

## Settings to know (.env)

- `DEMO_PHONE` = your cell. Every text goes there. Clear it only for a real deployment.
- `SMS_DRY_RUN=false` right now: texts are real (to your phone). Set `true` to log instead.
- `DRY_RUN=true`: email replies are drafted, never sent.
- `AGENT_NAME=Ariyah`: what the agent calls itself on the phone.
- `CALL_PROVIDER=retell`: the contractor dispatch call goes out through Retell (Nemotron talks),
  not Twilio. Twilio voice is still blocked pending Trust Hub verification; not needed today.

## If something breaks

- Texts not arriving: `python -c "import notify; print(notify.configured())"` must print True.
- Retell can't connect: re-run step 4; if it fails, the tunnel died, restart step 3 and update Retell.
- Agent talks nonsense: the property profile is `business/demo_property_ops.md`; the call rules are `VOICE_SYSTEM` in `voice_server.py`.
