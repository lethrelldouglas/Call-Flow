# Event day checklist (Builders & Brews Toronto, Tue Sep 29, 2 PM, Propeller Coffee Co.)

Bring: laptop, charger, phone (Luma ticket + it receives every demo text).

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

## 5. Retell (once per new address)

1. Create a NEW agent (leave the live ResolvOps agent alone).
2. LLM: choose **Custom LLM**.
3. WebSocket URL: `wss://<the address from step 3>/llm-websocket`
   (Retell appends the call id itself; nothing else to add).
4. Voice: any; the greeting and every line come from the server.
5. Optional: agent webhook URL `https://<the address>/webhook`.
6. Test with Retell's test-call button first, then call the number.

## 6. What to say on the test call

"There's water coming through my bathroom ceiling in unit 412 at Northgate Tower, it's still pouring."
Then answer its questions: your name, "yes this number is fine", "you can come in, I have a cat", "no that's everything".

Within about ten seconds of hanging up: a ticket on the dashboard, and three texts on your phone
(`[for Rapid Flow Plumbing]`, `[for Jordan Pike]`, `[for tenant]`).

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

## If something breaks

- Texts not arriving: `python -c "import notify; print(notify.configured())"` must print True.
- Retell can't connect: re-run step 4; if it fails, the tunnel died, restart step 3 and update Retell.
- Agent talks nonsense: the property profile is `business/demo_property_ops.md`; the call rules are `VOICE_SYSTEM` in `voice_server.py`.
