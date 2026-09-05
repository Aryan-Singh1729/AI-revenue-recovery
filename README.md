# AI Revenue Recovery

*Razorpay AI Hackathon 2026 - Track 03*

I built an agent that catches failed subscription payments before that money is gone for good, figures out why each one failed, decides what to do about it, and proves - with real numbers, not a slide - how much of it actually got recovered.

I'll walk you through the problem, what I actually built, everything that broke while I was building it (a lot did), how to run it yourself, and what to look at first when you open the dashboard.

Before anything else, here's the proof, straight from the current batch - 80 cases, 50 I can watch get processed live and 30 seeded as history so the dashboard isn't empty on first load:

| | |
|---|---|
| Revenue at risk | ₹2,52,045 |
| Revenue actually recovered | ₹82,464 |
| Recovery rate | 32.7% |
| Recovered / Escalated / Stopped | 36 / 24 / 20 |

Every number in that table is re-computed live from the database, not typed in by hand - regenerate the batch and you'll get the exact same numbers, because the data generation is seeded on purpose.

## The problem

If you've ever run a subscription business, you already know this pain: a customer's card expires, or their bank account is temporarily short on funds, or their bank randomly declines the charge - and just like that, a renewal payment fails. Most companies just... let it happen. Maybe they send one generic "your payment failed" email and move on. Multiply that across thousands of customers a month, and it's a genuinely large, quiet revenue leak - money that was never lost to a bad product, just lost to bad follow-up.

The hackathon brief for this track was blunt about it: don't just point at the problem, show an agent that finds revenue at risk, picks the right intervention, and runs a bounded recovery workflow - with proof, not promises. That last part mattered a lot to me. It's easy to build something that looks smart in a demo. It's harder to build something where every number on the screen is something you can click into and verify.

So that's what I set out to do: build an agent for exactly one slice of this problem - **failed subscription renewals for a SaaS-style business** - that detects the failure, diagnoses the real reason behind it, picks a recovery action that fits that reason, checks that action against a set of hard rules before doing anything, actually does it, and then honestly reports what happened.

## How I'm solving it

The system runs on a simple loop: **detect → diagnose → decide → check → act → learn what happened.**

- **Detect** - a failed payment lands in the system, shaped exactly like the error payload Razorpay itself would send (wrong-error-code, bank-decline, card-expired, and so on - nine different failure types in total, plus fraud and disputes).
- **Diagnose** - most of the time this is a straightforward lookup (an "insufficient funds" error is obviously insufficient funds), but for the messy, ambiguous cases, I hand it to an LLM to reason about.
- **Decide** - based on the diagnosis, the system picks a recovery action: retry the payment automatically, send the customer a payment link, send a human-sounding reminder message, or - if the situation calls for it - don't touch it at all and hand it straight to a human.
- **Check** - before anything is actually executed, it has to pass all **10 stopping rules**. Don't retry more than 3 times. Don't message someone who opted out. Don't chase a ₹35 payment if it costs more than that to try. Never, ever auto-touch a fraud-flagged or disputed payment. These aren't suggestions - they're hard gates, and every single one is logged with a pass/fail either way.
- **Act** - the approved action actually runs, for real, against Razorpay's test-mode API where possible.
- **Learn** - the outcome gets recorded, the money gets attributed (conservatively - I only count it as "recovered" if it actually came back), and the whole decision trail from start to finish is saved so anyone can go back and see exactly why the system did what it did.

Nothing about the *decision-making* - the rules, the state machine, the money math - is left to an AI to improvise. The AI is used narrowly, for three specific jobs: reasoning through an ambiguous failure reason, writing a plain-English explanation of why an action was picked, and drafting the actual message a customer would receive. Every one of those three has a plain-text fallback baked in, so if the AI is unreachable for any reason, the pipeline doesn't stall - it just falls back to template text and keeps going.

## What makes this different

I looked at what a "revenue recovery agent" submission could easily turn into: a nice-looking dashboard sitting on top of fake numbers that never touch anything real. I wanted to avoid that, so a few things I made sure were genuinely true, not just claimed:

**The Razorpay integration is real, not theater.** When the agent decides to retry a payment, it creates an actual test-mode Order through Razorpay's API. When it decides to send a payment link, it creates an actual test-mode Payment Link - a real, clickable `rzp.io` URL. You can watch these get created live. And when the real API call can't go through for some reason (not configured, or Razorpay's own rate limit kicks in - more on that below), it falls back to a simulated link that *looks* identical in shape, but the dashboard always tells you the truth about which one you're looking at. It never quietly pretends a simulated link is real.

**The AI reasoning is live, not pre-baked.** A lot of AI demos are really just a script replaying saved output. Here, when you click the button, you watch the actual model get called - a small "thinking" spinner while the request is in flight, and then the model's own words appear in a chat bubble the moment it responds. If a case doesn't need AI at all (most don't - the diagnosis is a clean rule match), you won't see AI theatre for it either. It's honest either way.

**The data is fake, but it's fake on purpose, and it's reproducible.** Razorpay's test mode can't actually produce nine different kinds of realistic payment failures on demand, so I generate synthetic cases shaped exactly like Razorpay's real error payloads. Every batch is seeded, so running it again gives you the exact same 50 cases, the exact same outcomes, the exact same ₹ recovered - nothing here is a lucky roll of the dice you can't reproduce.

**Every one of the 10 stopping rules is provably exercised, not just written.** It would be easy to write ten rules and have eight of them never actually trigger in the demo data. I went and planted a deliberate edge case for every single rule, so you can go to Page 5 and see a real example case behind every rule, not just a rule that theoretically exists.

**Every decision has a full paper trail.** Pick any one of the 80 cases in the batch and you can see, step by step: what failed, why, what the system decided to do about it, whether the rules allowed it, what actually happened when it tried, and how the money got counted (or didn't). Nothing is a black box.

**It's tested like I meant it.** There are three layers of automated checks (a full pipeline test, an end-to-end acceptance test, and a dashboard test that actually clicks every button), and I mean actually tested - not "it ran once and didn't crash." More on why that distinction mattered a lot, below.

## What went wrong (the honest part)

This is the part I actually want people to read, because I think it says more about the project than any feature list could. I did not get this right on the first try. Not close. Here are the ones that really got me.

### The recovery rate that was almost right, which is worse than being very wrong

Early on, my dashboard proudly showed a 3.22% recovery rate. Not catastrophic-looking, just... small. Small enough that I almost moved on and assumed the demo data was just tough. But something felt off, so I actually did the division by hand - and it turned out I was dividing the money I'd recovered by the customer's *entire remaining subscription value* (all their future billing cycles put together), instead of just the one payment that had actually failed. Once I fixed the denominator, the real number was closer to 33%. A ten-times difference, hiding behind a number that looked plausible enough that I almost didn't question it. The lesson that stuck with me: the most dangerous bugs aren't the ones that crash loudly, they're the ones that give you a number that's wrong but not *obviously* wrong.

### The dice that always landed the same way

My outcome simulator decides whether a customer "pays" after each recovery attempt, using a random draw seeded off the case so results are reproducible. Except I'd seeded it off the wrong thing - the *type* of action being tried, instead of *where in the sequence* that action fell. Which meant if a customer's first retry failed, every cheaper follow-up action for that same case was mathematically guaranteed to fail too, because it was drawing the exact same random number every single time. It took me a while to even notice, because on the surface everything looked fine - cases were failing and escalating exactly the way you'd expect a real-but-unlucky batch to behave. The bug wasn't wrong behaviour, it was wrong *odds*, quietly stacking the deck against every follow-up attempt in the whole batch. Fixed by seeding on the step in the sequence instead. Small conceptual mix-up, big invisible consequence.

### The audit trail that lied to me

This one bothered me the most, because the entire pitch of this project is "you can trust the audit trail." I found a spot where the code decided whether to log "Real Razorpay order created" based on a flag that got set once, when the program first started up - not based on whether the actual API call that just happened had actually succeeded. So if Razorpay's real API call failed for any reason, the audit trail would still cheerfully claim a real order was created. A dishonest log entry, sitting right in the one part of the system I was proudest of. I fixed it to check the real result of the real call, every single time, and it made me go back and check every other place in the code where I was tempted to take a shortcut like that.

### The placeholder that was quietly pretending to be real

My `.env` file still had the example placeholder values in it (`rzp_test_XXXXXXXXXX`, that sort of thing) - and my code's check for "is Razorpay configured?" was just "is this value not empty?" A placeholder string is, technically, not empty. So the system spent the entire batch run making real network calls to Razorpay with fake credentials, watching every single one fail, and then quietly falling back - for every single case, every single time. Nobody was hurt by it, but it was pointless work, and it meant I'd never actually tested what "truly not configured" behaviour looked like, because it never happened. Fixing the check to actually detect placeholder-shaped values dropped one batch run from 2.9 seconds to 0.2 seconds. That gap is basically a receipt for how much wasted effort was happening under the hood.

### The model that never existed

For a while, my whole AI setup was pointed at a model called "gpt-5.6-sol," running through a local proxy that was never actually turned on. I'd built the entire diagnosis/explanation/messaging pipeline around a placeholder I'd never gone back and swapped for something real - so every single AI call, this whole time, had been silently hitting its fallback text. When I finally sat down to wire up real AI, step one was admitting that model name was never going to work because it doesn't exist anywhere. I moved everything over to Groq instead, which is free, fast, and hosts real open-weight models - and it made me realize how good the fallback design had actually been. The pipeline had been running this whole time without me noticing the AI was never really there.

### The message that stopped mid-sentence

Once I did get a real model talking (`gpt-oss-120b`, an open-weight reasoning model on Groq), I hit something genuinely new to me: the model would sometimes send back a customer message that just... stopped. Mid-word, sometimes. I assumed it was a network hiccup at first. It wasn't - I dug into the raw API response and found a field called `reasoning_tokens`. Turns out reasoning models like this one spend part of their token budget quietly "thinking" before they ever write the visible answer, and that invisible thinking was eating most of the budget I'd given it, leaving barely anything left for the actual message. The fix was to tell it explicitly to keep its reasoning light for these simple tasks, and to give it a bit more room to work with overall. I'd genuinely never had to think about "the model is thinking too much" as a bug category before this project.

### The rate limit that pretended to be a config problem

This was the most recent one, and maybe my favourite, because it's such a clean example of a bug hiding behind a *different*, more boring-sounding bug. After wiring up real Razorpay keys, most of my payment links were coming back labelled "simulated," with a note claiming Razorpay wasn't configured - which was clearly wrong, since some other links in the exact same run *were* real. I dug in and found the actual reason had been getting thrown away: the code had genuinely tried the real API call, gotten a real error back, and then overwritten that real error with a generic "not configured" message before saving it. Once I stopped throwing the real error away, it said plainly: `"Too many requests."` Razorpay throttles how fast you can create payment links, presumably to stop people spamming out shareable payment URLs. I confirmed it wasn't my account being broken by literally doing nothing for a minute and trying again - it cleared right up. I added a short automatic retry for genuine one-off blips, and fixed the message so that when the real limit does get hit mid-batch, it says so honestly instead of blaming a config problem that didn't exist.

## Running it on your own machine

Even though I'm sharing a link to the dashboard, here's the full walkthrough if you'd rather run it yourself.

**1. Get the code and install dependencies**

You'll need Python 3.10 or newer.
```bash
git clone https://github.com/Aryan-Singh1729/AI-revenue-recovery.git
cd AI-revenue-recovery
pip install -r requirements.txt
```

**2. Set up your environment file**
```bash
cp .env.example .env
```
You can genuinely stop here and run the whole thing - it works perfectly well with an empty config, falling back to simulated Razorpay actions and template-based AI text. But if you want to see the *real* integrations in action, it's worth the five minutes:

- **For real AI reasoning** - go to [console.groq.com](https://console.groq.com), sign up free (no card needed), and grab an API key. Drop it into `.env` as `LLM_API_KEY`. Groq's free tier is generous, but if you want extra headroom, you can paste in two keys separated by a comma - the system will automatically fall back to the second one if the first hits a limit.
- **For real Razorpay actions** - go to [dashboard.razorpay.com](https://dashboard.razorpay.com), sign up free, flip the switch in the top-left to **Test Mode**, and go to **Settings → API Keys → Generate Test Key**. No business verification needed for test mode. Drop the ID and secret into `.env`.

**3. Generate the demo data**
```bash
python -m data.generate_batch
python -m data.seed_historical
```
This creates 50 "active" cases you can watch get processed live, plus 30 "historical" ones so the dashboard doesn't look empty on first load. (You can also skip this step and just use the "Regenerate demo batch" button inside the dashboard itself - it does the exact same thing with one click.)

**4. Run the dashboard**
```bash
streamlit run dashboard/app.py
```
It'll open at `http://localhost:8501`. That's it - no separate backend server required, it reads straight from the local database.

**5. (Optional) run the tests, to see I'm not just saying it works**
```bash
python -m tests.test_pipeline   # the full pipeline, checked 14 different ways
python -m tests.test_e2e        # the acceptance test I run before ever showing this to anyone
```

## Touring the dashboard - start at Page 4

The dashboard opens on Page 1 by default, but honestly, if you only look at one page, **go straight to "4 · Live Recovery Engine."** Everything else is depth and proof; this page is where you actually watch the agent work.

**Page 4 - Live Recovery Engine.** There are two buttons here, and they're meant to be pressed in order. **"Run Recovery Engine"** walks every pending case through detection, diagnosis, the 10-rule policy check, and execution - live, with a running log underneath. Watch for the little chat bubbles: that's the actual AI responding in real time, not a canned message, and you'll see a spinner while it's "thinking" and then its real words appear. Once that finishes, **"Simulate Customer Responses"** plays out whether each customer actually paid, closing the loop and updating the recovered/escalated/stopped counters live in front of you. There's also a **"Regenerate demo batch"** option if you want to reset everything and watch the whole thing again from scratch.

Once you've seen it run, the other four pages are where you go to verify what you just watched:

- **Page 1 - Recovery Command Center.** The headline numbers: how much was at risk, how much came back, the recovery rate, and a funnel showing where cases ended up. This is the "so what happened overall" page.
- **Page 2 - Recovery Batch (Case Explorer).** Every single case in the batch, filterable by status, root cause, or amount. Click into any row to jump straight to its full story on Page 3.
- **Page 3 - Case Audit Trail.** Pick any one case and read its entire life story: what failed, why, what was decided, whether the rules allowed it, what happened when it tried, and how it ended. Every step is expandable if you want the raw data behind it. This is the page I'd point to if someone asked "how do I know this isn't just making things up."
- **Page 5 - Escalation & Stopping Rules.** All 10 rules, how many times each one actually fired, a real example case for each, and a compliance summary confirming - checked against the live database, not just asserted - that fraud cases never got auto-touched and opted-out customers never got messaged.

