# Outreach: daily client finder + one-click send queue

For freelance video editors. Every morning at 7:00 a Claude run finds YouTube channels that could use an editor,
checks YT Jobs for new editing gigs, finds the posters' Instagram / X / LinkedIn, and writes a personal first
message or proposal for each. You open the queue page, click **Open profile + copy**, paste, press Enter, then
**Sent ✓ → next** (it opens the next person with their message already copied).

Nothing is ever sent automatically. The queue never reads or touches LinkedIn, Instagram or X pages: it only opens a
URL and copies text. That, the daily caps and one-message-per-person keep your accounts safe.

## Install on your PC (Windows)
1. Send the repo owner your GitHub username and accept the invite (GitHub emails it to you).
2. Make sure you have **Claude Code on your own Claude account** (the desktop app's Code tab is fine).
3. Paste this into Claude Code:

   > Set up Outreach for me. Install Git and the GitHub CLI with winget if they are missing. If `gh auth status`
   > says I'm not signed in, tell me to run `gh auth login` myself and wait (never type my password). Then clone
   > https://github.com/FardeenAFK/outreach into `%USERPROFILE%\Documents\Outreach` (if that folder already exists,
   > read SETUP.md section "Already have an Outreach folder" first), read `SETUP.md` there and follow it step by step.

It asks your name, your skills and how old your accounts are, then starts the local server, creates your 7am routine
and does a first run (about 40 minutes).

## Your accounts: the rules
- **Your own Claude account.** Sharing code is fine. Sharing a Claude login, token or account between people is
  against Anthropic's terms and can get every account involved suspended.
- **Your own Instagram / X / LinkedIn / Facebook**, in your own Chrome profile. Never send from someone else's.
- **You press send on every message.** Don't change the system to send messages by itself: that is what gets accounts
  banned, and automated unsolicited DMs break X, Instagram and LinkedIn rules.

## Daily use
- **Today**: messages per platform, up to today's cap. Edit the text if you like, then
  **Open profile + copy** → paste → Enter → **Sent ✓ → next**. Instagram/Facebook open the DM thread directly;
  X/LinkedIn open the profile (click Message there). **Profile** opens their profile instead.
  - LinkedIn: first you **Connect with no note** (free accounts only get 3 notes a month). When they
    accept, go to **Replies → Accepted ✓** and the DM is queued.
  - Instagram: one message only. People who don't follow you can't get a second one until they accept.
  - X: two short follow-ups are scheduled automatically (day 4 and day 9) unless they reply.
- **Undo**: every Sent / Tomorrow / Skip / Replied / Won / Dead shows an **Undo** button for 10 seconds.
- **Replies**: when someone answers, click **Replied**. That stops their follow-ups. Mark **Won** / **Dead**.
- **Jobs**: the job-site hub (see `JOB-SITES.md`) plus drafted proposals.
  Anything in `[ADD ...]` brackets needs your link or rate.
- **Stats**: sent today, replies, reply rate. Check it after 2-3 weeks to see which platform works.
- **Settings**: caps (hard max X 40 / Instagram 30 / LinkedIn 15) and lead filters (subs range,
  niches, countries, excluded words).
- Stop for the day on a platform that shows "action blocked" or "try again later".

The queue is http://127.0.0.1:8777/ (bookmark it in the Chrome profile you send from). Optional: the same queue as a
Chrome side panel via `chrome://extensions` → Developer mode → Load unpacked → the `extension` folder.

## Safety rules built in
- Week 1 runs at 60% of your caps and week 2 at 80%. Full caps after that (X 30, Instagram 20, LinkedIn 15, Facebook 10).
- One platform per person, never a pitch on three apps at once. Every message is written differently, and
  `tools/check_drafts.py` rejects repeated phrases, invented experience and links where they aren't allowed.
- Bans come mostly from people ignoring or reporting you, not only from volume. If replies are near
  zero after 2 weeks, fix the message before sending more.

## Team split: nobody pitches the same person twice
Every lead and every job post belongs to exactly one person on the team. A hash of its id decides, the same way
on every PC, so there is no shared server and no shared database. The team lives on this repo's `team` branch:
- `members.json`: the team, in join order. `python engine.py team-join NAME` adds you. Names are only ever appended:
  removing or reordering one would reshuffle who owns what. Someone who stops keeps their place.
- `claimed/<name>.txt`: hashes of everyone each person has already queued or contacted, so a new member never
  pitches a person someone else already messaged. No names or links are stored, only hashes.

`engine.py daily` fetches the team branch before drafting and `engine.py finish` publishes your claims after
importing, so this happens by itself every morning. When someone joins, everyone else picks it up on their next run.

## Updating
Ask Claude Code: "Update Outreach: `git pull` in Documents\Outreach, then restart the server by its PID on port 8777."
Your `config.json`, `outreach.db`, `work/` and `reports/` are never in the repo, so an update never touches your
settings, leads or history. Teammates don't commit code to `main`; send changes to the repo owner.

## Files
- `engine.py daily`: the whole morning collection in one command. `engine.py finish`: check, import, report, sync.
- `team.py`: the team split and the `team` branch sync.
- `DAILY-TASK.md`: what the 7am Claude run does, including the message-writing rules.
- `SETUP.md`: the install steps Claude follows on a new PC.
- `config.example.json`: the starting `config.json` (caps, ramp-up, follow-up days, filters, job keywords, your profile).
- `server.py`: local API and queue page on 127.0.0.1:8777 (refuses requests from other websites).
- `JOB-SITES.md`: which job sites are worth it and who can apply (live research 2026-09-28).
- `python tools/selftest.py`: regression tests against copies of your data (needs a few days of data first).

## Not built (on purpose)
- **Email**: channels with a public email are kept (status `email_only`). Sending cold email needs a separate
  domain + mailbox, a warm-up period, a postal address and an opt-out line. Never cold-email from a personal Gmail.
- **Upwork / Freelancer.com**: never automated, both ban it (Upwork even bans job-alert extensions).
