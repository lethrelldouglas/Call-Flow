# Demo script (about 3 minutes)

## Screen setup before anyone is watching
- Browser, full width: the board at http://localhost:8000, emptied with
  `python -c "import tickets; tickets.clear()"` so it says "No tickets yet".
- A terminal window ready behind it (VS Code terminal is fine), sitting in the project folder.
- Gmail open in a browser tab, inbox for resolvops@gmail.com.
- Phone in your hand, unlocked, Messages app open, volume up.
- Retell agent published with the number attached, tunnel and server windows running.
- Say the Retell number out loud once so people can call it themselves afterwards.

## Beat 1: the setup (15 seconds)
DO: nothing, board is empty on screen.
SAY: "This is the after-hours line for a property manager with 400 tenants. It's 2 AM, nobody's awake, and a tenant has water coming through the ceiling. Watch what happens."

## Beat 2: the call (60 seconds)
DO: dial the number on speaker.
SAY (as the tenant): "Hi, there's water coming through my bathroom ceiling in unit 412 at Northgate Tower. It's still pouring and getting worse."
Let her give the safety instruction. Answer what she asks:
- name: "Amara Osei"
- number: "yes, this number is fine"
- entry and pets: "you can come in if I'm out, I have a cat"
- read-back: "yes, that's right"
- anything else: "no, that's everything, thanks"
Hang up when she says goodbye.

## Beat 3: the board (20 seconds)
DO: look at the browser. Within ten seconds the red EMERGENCY card appears.
SAY: "Ticket's open. Plumbing, emergency, unit 412. It picked the plumber on file and texted them, texted the on-call manager, and confirmed to the tenant. Every action is logged here with a timestamp."

## Beat 4: the phone (20 seconds)
DO: hold up the phone as the three texts arrive.
SAY: "Those are the texts. This one was for the plumber, this one for the manager, this one for the tenant. In the demo they all come to me; in production they go to the real people in the profile."

## Beat 4b: the phone rings (30 seconds)
DO: a few seconds after the texts, your phone rings from the Retell number. That's "the plumber" being
called. Answer on speaker. Ariyah: "Hi, this is Ariyah from Northgate Rentals with an emergency dispatch
for Rapid Flow Plumbing. Unit 412 at Northgate Tower, water pouring through the bathroom ceiling. Can you
take this job now?" Say: "Yeah, I can take it, heading over now." She confirms and hangs up.
SAY: "A text can sit unread, so for emergencies it phones the contractor, and that call is Nemotron too.
I said yes, so the board flips to in progress and the tenant gets a text that the plumber is on the way.
Say no, or don't pick up, and it calls the next contact, then the manager."

## Beat 5: the email (15 seconds)
DO: switch to the Gmail tab, open the newest message.
SAY: "The manager also gets the full ticket by email: what the tenant said, what was sent, what's still missing."

## Beat 6: the quiet night (30 seconds)
DO: in the terminal, run `python ops_demo.py`. Cards start landing on the board.
SAY: "Emails and texts go through the same intake. A no-heat call: emergency, heating contractor. A lockout: urgent, superintendent, nobody else woken. A noise complaint: routine, office queue in the morning. And the elevator-financing spam never became a ticket."

## Beat 7: the morning (20 seconds)
DO: run `python morning_summary.py --hours 24`, then open the new email in Gmail.
SAY: "At 7 AM the office gets this. Nemotron wrote the overview, the list underneath is exact."

## Beat 8: the close (20 seconds)
SAY: "Retell is the phone line. Everything the agent says and every decision it makes is Nemotron on Nebius Token Factory: Lightning talks in under a second, Super triages and dispatches, zero data retention so tenant calls are never stored. I run ResolvOps, and this is the open-model version of what our clients pay for. Want to call it?"

## If the call fails
Don't debug on stage. Say "let me show you the same thing from the inbox side," run `python ops_demo.py --only 1`, and carry on from Beat 3 with that ticket. Fix the call afterwards (usually the tunnel: double-click start_demo.bat, paste the new address into Retell).

## If someone asks to try it
Let them call. Say only: "tell it what's wrong and where you are." The board and your phone do the rest. Routine problems won't text anyone but the tenant, which is a good thing to point out.
