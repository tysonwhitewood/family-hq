# Kids HQ — Barefoot money school for Maia, Annaliese and TJ

**Date:** 18 September 2026
**Status:** Phases 1–3 approved to build 18 September 2026. Phase 4 (Camillo paper stall) parked.
**Related:** Family HQ Mattermost (`2026-09-09-obligations-and-mattermost-design.md`), Camillo Radar (`~/Dev/camillo`), Kit Smart Money Playbooks (5–7, 8–10, 11–13), *Barefoot Kids* and *The Barefoot Investor for Families* by Scott Pape.

## Problem

Maia (12), Annaliese (8) and TJ (6) are getting CommBank Kit cards. A Youthsaver ending 7637 is already linked to Kit. Kit is emailing Robyn to set up a regular **PayDay pocket money**. That is the opposite of how this family wants money to work: the kids are not paid for existing. They are paid for extra work. The household already loves Barefoot. Kit is a good wallet. It is not the teacher, and it does not talk to Mattermost.

## Goal

Maia, Annaliese and TJ each get a fun iPad door into Family HQ — **Kids HQ** — where they learn Barefoot money in Australian English, see their jars, play age-right games, and earn a little for getting a principle right. Kit holds the real dollars and the card. Youthsaver holds the long-term Smile/Grow money. Mattermost tells each child their own numbers, and never shows them adult finance.

After a term they should be able to say, in their own words:

- I help at home because I live here (that is free).
- Extra jobs earn extra money.
- Every pay is split: Splurge, Smile, Give.
- Money left in Smile grows. Money spent does not.
- Nagging does not turn a no into a yes. There are no bailouts.

## Non-goals

- No automatic weekly pocket money for being a Whitewood.
- No live share trading, no crypto, no “buy this stock”.
- No kids in the Family Finance Mattermost channel.
- No CommBank or Kit login inside Family HQ. Family HQ never moves their money.
- No clone of Kit Money Quests. Kit can keep its own videos.
- No sibling richest-kid leaderboard.
- No Camillo paper stall until the Save/Smile habit has stuck (about six months).

## The children

| Child | Age | Kit playbook | Barefoot pitch |
|---|---|---|---|
| **Maia** | 12 | 11–13 | Splurge / Smile / Give, then Grow. Compound interest, debit vs credit, second-hand, maybe a mini business. Camillo later. |
| **Annaliese** | 8 | 8–10 | Jars, a short Smile goal, “interest is a reward for leaving it there”, sleep-on-it, no bailouts. |
| **TJ** | 6 | 5–7 | Coins are real, need vs want, family jobs vs extra jobs, a magic jar, PIN is a secret. Goals measured in weeks, not months. |

Timezone for every schedule: **Australia/Brisbane**. Quiet hours stay 9pm–7am, same as adult Family HQ.

---

## How the three systems fit together

```
Kit .............. wallet (card, jobs tick-off, PayDay release, Stacks, Youthsaver)
Kids HQ .......... school (Barefoot games, principles, family bonus interest, PINs)
Mattermost ....... messenger (each child their own channel; parents get approvals)
Youthsaver ....... the real Smile/Grow account (already linked)
Camillo .......... later, paper only, Maia first
```

**Kit is the bank account. Kids HQ is the classroom. You two are still the parents.**

If a chore is ticked in two apps, the kids will game one and ignore the other. So:

- **Family jobs and extra earn jobs live in Kit** (they already have the app on the iPads).
- **Kids HQ does not become a second chore list.**
- Kids HQ *does* teach, quiz, game, show the Barefoot jars, calculate family bonus interest, and talk on Mattermost.

Balances get into Kids HQ the same way adult balances get into Family HQ: a Kit screenshot (or typed figures) posted to the child’s Mattermost channel, read by the bot.

---

## Barefoot is the spine (not Kit’s pocket-money email)

Scott Pape, in *Barefoot Kids* and *The Barefoot Investor for Families*, boils kids’ money down to something this family already believes.

### The six Barefoot Kids steps (the curriculum)

1. **Help around the house for free.** You live here. Making the bed, dishes, feeding animals as a normal member of the family is not a paid job.
2. **Stash your cash — three buckets.** Splurge, Smile, Give. Save *before* you spend.
3. **Be a Barefoot boss.** Extra jobs and little businesses earn extra money. That is how you turbocharge a goal.
4. **Get what you want (that Mum and Dad approve).** Save for it. Look second-hand. Sleep on it. Parent still says yes or no to the thing.
5. **Make someone smile.** The Give bucket is the brat-buster. Kindness counts. Happiest people give.
6. **Grow your money.** Compound interest. Later, investing with as little as $5 — paper first in this house.

### The weekly ritual (Barefoot Families)

- **Three jars** (now digital in Kit, still called Splurge / Smile / Give).
- **Three family jobs** each, done because they live here.
- **Three minutes**, once a week, over a **Money Meal**.

Kids HQ and Mattermost exist to make that Sunday ritual happen without anyone opening a spreadsheet.

### What we deliberately ignore in Kit’s email

Kit’s eDM says: set up a regular pocket money PayDay; research links regular pocket money to responsibility.

Barefoot (and this family) say: **do not pay for existing.** Kit itself also says, in the playbooks: never put a price on a family expectation; pay only for above-and-beyond; Required Jobs are the family list; Earn Per Job is extra.

So we use Kit’s machinery, not Kit’s marketing:

| Kit setting | Whitewood rule |
|---|---|
| Regular PayDay amount | **$0.** They are not paid for Saturday arriving. |
| Required Jobs | The three family jobs. Unpaid. They still get ticked. If they are not done, extra earn pay waits until Sunday’s Money Meal says so. |
| Pay Per Job | Extra work, priced. This is the only way dollars appear. |
| Automated vs Boss approval | **Boss approval.** Always. You two release pay at the Money Meal, not when a child ticks a box. |
| PayDay split | Automatic split of whatever was *earned* that week: Splurge / Smile / Give. |
| Linked Youthsaver | Smile leftover and Grow. Money in Youthsaver cannot be spent on the card — that is the point. |

Pay is weekly so they practise waiting (Kit is right about *that*). The wait is “until Sunday”, not “until I nag”. Amounts follow work done, not a rostered allowance.

---

## The four Barefoot buckets, mapped onto Kit

| Barefoot jar | What it teaches | Where the dollars sit | Family bonus interest? |
|---|---|---|---|
| **Splurge** | Practise spending, make small mistakes, no bailouts | Kit **Card** | No |
| **Smile** | Save for a thing that will make them smile (parent-approved) | Kit **Stack** named for the goal | Yes, while it stays there |
| **Give** | Kindness. Brat-buster. | Kit **Stack** named Give | No |
| **Grow** | Money that is meant to stay and compound. Maia first, then the others when Smile is a habit. | **Youthsaver** | Yes (family bonus on top of the bank’s tiny rate) |

**Default split of every extra-job payment** (editable in config, not hard-coded):

- TJ (6): 40% Splurge, 50% Smile, 10% Give. Locked. Two or three jars only, as Kit’s 5–7 playbook says.
- Annaliese (8): 40% Splurge, 50% Smile, 10% Give. Can nibble the percentages inside a floor of 30% Smile.
- Maia (12): 40% Splurge, 40% Smile, 10% Give, 10% Grow. She helps choose the split, once a term, at the Money Meal.

Buffett line they will hear until they roll their eyes: **do not save what is left after spending; spend what is left after saving.**

### The $100 seed

Each child: **$100 into Youthsaver**, not onto the card. That is the Grow/Smile seed they can watch. It is not for the dairy. Kit’s Youthsaver link already shares name, BSB, account number, balance and history for 12 months — confirm **each** child has their own Youthsaver linked, not only the account ending 7637.

### Family bonus interest (Bank of Mum and Dad)

Real Youthsaver interest on $100 is about 40 cents a month. TJ will not see it. Barefoot and Kit’s 8–10 playbook both say: pay a juicy parent rate so compounding is visible.

Kids HQ calculates, every Sunday:

- **1% of the Smile + Grow balances that week**
- Cap **$2 per child per week**
- Paid only if the money was still there (they did not raid Smile to Splurge)
- Mattermost to you: “Pay $1.08 into Maia’s Youthsaver (family bonus).”
- Mattermost to Maia: “Your Smile and Grow earned $1.08 this week because you left it there. Next week it earns a bit on this week’s bit too.”

Labelled **family bonus**, never “the bank’s rate”. A second line can show real Youthsaver interest when you type it in from the CommBank app.

Rate and cap live in `data/config.json` under `kids`.

### No bailouts

If Splurge is empty, the answer is wait until next Sunday’s extra jobs — not a top-up from you, and not a raid on Smile. Kit playbooks and Barefoot agree. Kids HQ will not have a “Mum, pay me” button.

---

## Sunday Money Meal (the whole system in 15 minutes)

**When:** Sunday 4:00pm Australia/Brisbane (configurable). Adult Family HQ already posts the reserve position at 5:00pm; kids go first so dinner can include the three-minute chat.

**Where:** Kitchen table, iPads away after the numbers are read. Mattermost has already pinged.

**Script (Barefoot three minutes, slightly stretched):**

1. Each child: did you do your three **family jobs**? (Kit Required Jobs.)
2. Extra jobs done this week — Boss approval in Kit. Money released.
3. Split happens (Kit PayDay split).
4. Kids HQ / Mattermost reads the new jars. Family bonus if Smile/Grow stayed put.
5. One sentence each: something they are grateful for (Kit playbook + Barefoot Give).
6. If someone wants a thing: is it a need or a want? Sleep on it until next Sunday unless it is already a Smile goal you have approved.

You two stay the boss of the thing they are saving for. Barefoot: they can save for anything **you** approve.

---

## Kids HQ on the iPad

**Address:** `https://family.edencommercial.au/kids`

Different icon, name **Kids HQ**. Add to Home Screen on each iPad. Adult login still sits at the main URL. Guessing `/obligations` still hits the adult password.

**Login:** three big faces — Maia, Annaliese, TJ — each with a 4-digit PIN you set. Not the adult password.

**Home, after PIN:**

1. **My jars** — Splurge, Smile, Give, and Grow for Maia. Big numbers. A plant on Smile/Grow that grows while the money stays.
2. **This week** — family jobs (unpaid, ticked from last screenshot/status you recorded) and extra jobs they can chase. Deep link or “open Kit to tick” rather than a fake second list.
3. **My Smile goal** — one picture, price, bar, “about N extra jobs to go”.
4. **Play** — games for *that* child only.
5. **Learn** — the next Barefoot principle, one screen.

TJ’s home is pictures and one number. Annaliese gets a goal bar. Maia gets a small chart and a list of what came in and went out.

Australian English. Fat buttons. Portrait and landscape.

---

## Principles and games by child

Each principle = a short lesson + a game. **First time they get it right, they earn money into Kit** (you pay it in after Mattermost says so). Repeats are for fun and badges, not pay.

Suggested first-time pay (config): TJ $1, Annaliese $2, Maia $3.

Wrong answer: try again, no shame, no pay until it is right.

### TJ (6) — Kit 5–7 + Barefoot steps 1–2

He should leave term one knowing: money is earned extra, family jobs are free, some money is for later, PIN is a secret.

| Principle | Game |
|---|---|
| Coins have value (50c is biggest, $2 is worth more) | Coin sort |
| Need vs want | Picture sort (water vs lollies) |
| Family job vs extra job | Match the picture: bed = family, washing the car = extra $ |
| Save before you spend | Magic jar: coins left in get a baby next week |
| Digital money still goes down | Show-the-balance: card tap, number gets smaller |
| PIN and “ask an adult” | Lost-card story. Friend wants the PIN. The answer is no. |
| Wait a little | Marshmallow-style: wait for Sunday, not for the dairy today |
| Give | Pick a way to make someone smile this month (a Stack, not a lecture) |

Goals for TJ: **weeks**, not months (Kit 5–7).

### Annaliese (8) — Kit 8–10 + Barefoot steps 2–5

She should leave term one knowing: the card is not free money, Smile is the only jar that grows, a goal beats a mood, no bailouts.

| Principle | Game |
|---|---|
| Split the pay | Drag this week’s extra-job dollars into Splurge / Smile / Give |
| Interest is a reward for leaving it | Interest garden vs Spend path |
| Sleep on it | Shop trap: buy now vs wait four extra jobs |
| Smile goal | One real goal, bar fills, SMART (specific, priced, few weeks to a couple of months) |
| Second-hand | “One person’s trash is another’s treasure” — find the cheaper path |
| Advertising is trying to get you | Spot the trick (flash sale, “everyone has it”) |
| No bailouts | Story: Splurge empty on Friday. What happens? |
| Give | Choose a real Give this term |

### Maia (12) — Kit 11–13 + Barefoot steps 3–6

She should leave two terms knowing: starting early beats being clever, spending has a future cost, borrowing charges you, a share is a slice of a real thing, you do not have to be first.

| Principle | Game |
|---|---|
| Save before you spend | She sets her own split (with the Smile floor) |
| Compounding | Time machine: $100 spent vs Smile vs Grow at family bonus vs real 5%, 1 / 5 / 10 years. Rule of 72. |
| Opportunity cost | Two wants. Pick one. See the other path in a year. |
| Inflation | The $20 thing is $22 later |
| Debit vs credit | Credit is someone else’s money with a price. Barefoot: never get a credit card as a life rule, when she is older. |
| Second-hand / value | Price is what you pay, value is what you get |
| Barefoot boss | Mini business sketch: what, who, costs, price, profit (farm-stall energy, not a startup fantasy) |
| Give with values | Give bucket to something she chooses |
| Grow | Paper stall later — see Camillo phase |
| Scams | “If it sounds like free money, it is not” |

Kit already has Money Quests. Kids HQ games should feel like **this family** (jars, chooks, Aratula, Sunday Money Meal), not a second quiz app.

---

## Mattermost

Not Family Finance. That stays Tyson (`tawhai`) and Robyn (`mum`).

For each child, you (as Mattermost admin) create:

- A user they can actually log in as on the iPad Mattermost app, **or** they only receive (parent iPads already have Mattermost — then the child’s channel is something they open in a browser/app you set up).
- A **private channel**: `kids-hq-maia`, `kids-hq-annaliese`, `kids-hq-tj`
- Members: that child + Tyson + Robyn + Family HQ bot
- They are never invited to Family Finance

**Sunday 4pm** (Money Meal recap), example for Maia:

> Hi Maia.
> Splurge $12.40 · Smile $48.00 (Lego $45 — $3 over, nice) · Give $6.20 · Grow $101.08
> Extra jobs this week: 2, earned $11. Family bonus $1.08 because Grow stayed put.
> Next principle: *Why waiting beats tapping.*
> Money Meal is at 4.

**When you release Kit pay:** bot can also say “your extra jobs landed, split like this” if you type `paid maia 11` in the parent view or in Family Finance.

**Child vocabulary (v1, no Claude chatting to kids):**

| They type | Bot says |
|---|---|
| `jars` or `balance` | The four (or three) numbers |
| `goal` | Smile goal progress |
| `help` | Those words |

Numbers only. Adult Family HQ keeps Claude. Kids HQ does not let a 6-year-old free-ask an LLM about money.

**You get** (in Family Finance or a `kids-hq-parents` channel): pending lesson-pay, “family bonus to transfer”, “screenshot please if jars look stale”.

---

## What you see in adult Family HQ

A **Kids** page behind the existing login:

- PINs (set by you)
- Family bonus rate and cap
- Lesson-pay amounts
- Last known Kit jars per child (from screenshot or typed)
- “Pay family bonus / lesson $ into Kit” checklist
- Which Barefoot principle each child is up to
- Money Meal preview (same idea as Preview today’s message)

Adult Obligations, cash flow and Family Finance are untouched.

---

## Camillo — parked (phase 4)

Camillo Radar notices a trend, asks if the story is in the press yet, asks if the share price has already moved. It informs. It does not trade.

When we pick this up, look for **trends that apply to these three homeschool kids** — not adult franchise radar. Seeds Tyson named: they are sort of into **Pokémon** because of their cousins; they like **reading**, so some books might be worth watching for collectable value. Still paper only. Still Maia first.

When Maia’s Smile/Grow habit has stuck:

- Paper only. Pretend $20. Marked to a real price once a week.
- Kid English for Camillo’s two questions: *Has this been on the news yet? Has the price already jumped?*
- Close by patience rules (stop, target, or time) — the same idea as your adult rules, not a casino.
- Annaliese may watch. TJ does not paper-trade.
- Under 18 they cannot own shares in their own name. Kids HQ will never place an order or say “buy this”.

Kit’s 11–13 playbook agrees: master saving and spending first; simulate purchases; trusted sources only.

---

## What we steal (and do not fork)

Open-source kids-money apps were checked earlier. None should be copied into Family HQ (wrong stack, wrong country). Ideas we keep:

- Parent is the bank; app is the ledger (*kid-bank*, FamZoo).
- Spend needs a parent yes (*money-sprouts*).
- Accelerated parent-paid interest so compounding is visible (FamZoo, Kit 8–10 “bank of mum and dad”).
- Short browser games, one idea each (*financial-literacy-playlist-games*).
- Mental models, not memorising rules (*literacy-for-kids*).

Barefoot and the Kit playbooks beat all of those for *this* house.

---

## Config (when we build)

All of this will live in `data/config.json` under `kids`, with sensible defaults, documented in the README. Nothing important is hard-coded. Children, PINs (hashed), channel IDs, split percentages, bonus rate, bonus cap, lesson pay, Money Meal weekday/hour, timezone `Australia/Brisbane`.

Mattermost channel IDs are required. If a kids channel ID is missing or accidentally set to Family Finance, the bot **must refuse to post**. Kids must never see BAS or the mortgage.

## Architecture (when we build)

Same Flask + SQLite + Coolify app. Kids HQ lives in the `kids/` folder (`kids/__init__.py` for the engine, `kids/page.html` for the iPad page). Routes and SQLite tables stay in `app.py`. Tests stay in `tests/`. Scheduler reuses the adult reminder thread with a second daily/weekly job.

Gunicorn stays one worker. Australian English in every user-facing string.

## Build in four phases (each approved before the next)

**Phase 1 — Jars, Money Meal, Mattermost.** Kids logins, Barefoot jars, $100 seed recorded, screenshot/typed balances, Sunday recap, family bonus calculation, parent “pay this into Kit” list. No games yet. Usable the week the cards are in their hands.

**Phase 2 — School.** Age-banded Barefoot principles + games + first-time lesson pay.

**Phase 3 — Goals and no-bailouts.** One Smile goal each, sleep-on-it, card/PIN safety, matching “paid in Kit”.

**Phase 4 — Grow / Camillo paper stall.** Maia first. Paper positions. Still no broker.

## Side effects to watch

- Adult 7am and Sunday 5pm jobs must keep running. Kids Sunday 4pm is an extra job, not a rewrite.
- A shared bot posting to the wrong channel is the serious risk. Channel IDs in config, hard fail if they match Family Finance.
- Two chore lists would confuse them. Jobs stay in Kit.

## How we will know it is working (four weeks)

- Each child can say their jar numbers without looking.
- TJ can sort need vs want, and can say family jobs are not paid.
- Annaliese has a Smile bar that has moved, and has had at least one “sleep on it”.
- Maia can explain why $100 left in Grow beats $100 spent, in her own words.
- You have paid family bonus at least once, and they noticed.
- Youthsaver balances went **up** in the calendar month (real CBA bonus on top).
- No automatic pocket money went out for a week of existing.

## Assumptions

- Youthsaver ending 7637 is linked; we still need **three** linked Youthsavers if they do not already have one each.
- Kit is on their iPads; Mattermost users for the kids may still need creating.
- You and Robyn approve extra jobs in Kit (Boss approval) and approve Smile goals.
- Extra-job prices are yours to set in Kit. Kids HQ does not need the price list in v1.
- I have not clicked through Robyn’s Kit app, so PayDay-with-$0 may need a tiny workaround if Kit insists on a dollar figure. If it does, we set $1 and treat it as a rounding nuisance, not an allowance.

## Open questions (for this review)

1. Confirm each child has their own Kit profile and their own Youthsaver, not one shared 7637.
2. Sunday 4pm Money Meal — keep, or pick another Brisbane time?
3. Default splits above — keep, or change before we write them into config?
4. Mattermost: do the kids get their own logins, or do they only see Kids HQ on the iPad and you read them the Sunday message?

---

Nothing in this document is an instruction to write code. Implementation starts only after Tyson and Robyn say this draft is right.
