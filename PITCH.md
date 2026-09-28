# What this is, in my own words

## One line
An after-hours front desk for property managers: it answers the phone, opens the work order, wakes the right contractor, texts the tenant, and puts it all on a live board, running on NVIDIA Nemotron through Nebius Token Factory.

## The problem (the Altair Rentals pitch)
A property manager with 400 tenants gets calls, emails and voicemails all night: floods, no heat, lockouts, noise, "which contractor is coming tomorrow?" One overflowing voicemail box, staff who can't sleep, emergencies buried under routine requests, and no single view of what's open. That's the job I proposed to automate for Altair Rentals, and this is the open-model build of it.

## What it does, end to end
1. **Phone**: Retell owns the number and the voice. Every time the tenant speaks, Retell sends the transcript to my server; Nemotron 3.5 Lightning decides what to say and streams it back in under a second. It gives the safety instruction first for emergencies, collects name, unit, callback, the problem, whether it's still happening, and permission to enter, then reads it back.
2. **Ticket**: when the call ends, Nemotron 3 Super fills the work order: category, urgency, emergency flags, what's still missing. Emails and texts go through the same intake.
3. **Dispatch**: Super applies the property's rules. Emergency: matching contractor plus the on-call manager, by text, and the manager also gets an email with the full ticket. Urgent: the building's superintendent. Routine: the office queue, nobody woken. The tenant gets a confirmation text. The dispatcher can only use phone numbers that exist in the property profile, so it can't invent one.
3b. **Acknowledgement**: for emergencies the server then phones the contractor, reads the ticket aloud and asks for "press 1 to accept". Accepting flips the ticket to in progress and texts the tenant who is on the way. No acceptance within ten minutes, or a decline, moves to the next contact, then the manager.
4. **Board**: every ticket lands on the live operations dashboard, emergencies first, with the full log of what the agent did. Staff can mark tickets in progress or done.
5. **Morning**: one command emails the office an overnight summary; Nemotron writes the three-sentence overview, the ticket list is exact.

## What happens when I double-click start_demo.bat
1. Checks my Nemotron and Tavily keys work on this wifi.
2. Starts the voice server (the brain plus the dashboard) in its own window.
3. Opens a Cloudflare tunnel so Retell on the internet can reach my laptop; opens the dashboard in the browser.
4. Puts the day's Retell address on my clipboard. I paste it into the Retell agent, publish, and the number is live.

## What's real and what's demo
- Real: every model call, the phone line, the ticket logic, the texts, the emails, the dashboard.
- Demo only: the property "Northgate Rentals" and its people and contractors are fictional. DEMO_PHONE and DEMO_EMAIL redirect every text and email to my own phone and inbox, labelled with who they were for. Clear those two settings and add a real profile, and it's a deployment.

## The stack and why
- **Nemotron 3.5 Lightning** for the live call: first word in about half a second with thinking off. Speed is everything on a phone call.
- **Nemotron 3 Nano** for in-call extraction, email triage and research planning: JSON output, cheap, about a second.
- **Nemotron 3 Super** for the work order, dispatch and written replies: the decisions that need to be right, reasoning on. Runs once per ticket, so the extra seconds don't matter.
- **Nebius Token Factory**: OpenAI-compatible endpoint, so the standard openai package is the only client; streaming for voice; zero data retention turned on, so tenant calls are never stored by the model provider.
- **Tavily**: live web research in the email front desk (a permit question gets the town's own bylaw page). Next: weather and utility-outage checks during no-heat and no-power calls.
- **Retell** for telephony and speech, **Twilio** for texts, **Gmail** for staff email, **FastAPI** for the server, plain HTML for the board.

## Numbers I can say out loud
- About a second per spoken turn.
- Ticket, dispatch decision and three texts within about ten seconds of hang-up.
- Model cost per call: a cent or two. The phone line (Retell) is about seven cents a minute.
- Token Factory serves four Nemotron models; I use three of them for three different jobs.

## What I'm building at the event (the four hours)
1. Get it live on a real number and make real calls.
2. Tavily during the call: weather and outage lookups that change how a no-heat or no-power call is handled.
3. Contractor check-in: the plumber texts "on my way" or "done" and the ticket updates itself.
4. Record the demo while it works; push to GitHub; start the Devpost submission.

## Answers to likely questions
- **Why not one model?** Voice needs speed, dispatch needs judgement. Lightning is fast, Super is careful. Using both is cheaper and better than either alone.
- **Why Retell rather than building voice yourself?** Telephony, speech-to-text and turn-taking are solved problems. The hard part, and the part judges care about, is what the agent decides. That's all Nemotron.
- **What stops it texting a wrong number?** The dispatcher's output is checked against the numbers in the profile; anything else is dropped and logged.
- **What stops it making things up to the tenant?** Prices, policies and people come only from the profile; it never gives out staff numbers; it never diagnoses; anything unknown becomes "the team will confirm."
- **Who is this for?** ResolvOps clients: property managers, contractors, trades. A closed-model version already answers email for paying clients; this is the open-model rebuild.

## The 30-second version
"I run ResolvOps. We put AI front desks in front of local businesses. This one is for a property manager with 400 tenants: it takes the 2 AM call about water through the ceiling, gives the safety step, opens the ticket, texts the plumber and the manager, confirms to the tenant, and puts it on the live board before the call is over. The whole brain is Nemotron on Nebius Token Factory: Lightning talks, Super decides. Want to call it?"
