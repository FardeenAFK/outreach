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
- **Keyboard**: `O` open + copy, `S` sent → next, `J`/`K` move, `T` tomorrow, `X` skip, `U` undo, `F` focus mode
  (one person at a time), `1`-`6` tabs, `?` all keys. Edits to a message are saved as you type, so a reload never
  loses them. Filter chips show one platform at a time.
- **Undo**: every Sent / Tomorrow / Skip / Replied / Won / Dead / Applied shows an **Undo** button for 10 seconds.
- **Replies**: when someone answers, click **Replied**. That stops their follow-ups. Mark **Won** / **Dead**.
  Filter by replied / waiting / LinkedIn accept, or search by name.
- **Jobs**:
  - *Proposals*: drafted for new posts from YT Jobs and the feeds below. Open job + copy, then **Applied ✓**.
  - *Find jobs*: one-click searches for the sites that ban bots (Indeed, LinkedIn Jobs, Upwork, Freelancer,
    OnlineJobs.ph, Himalayas, Contra, Workana, PeoplePerHour) plus places people post "hiring an editor" right now
    (X, LinkedIn posts, Reddit r/HireAnEditor and r/forhire, Facebook groups). Change the keyword at the top.
  - *Tracker*: everything you applied to or pitched by hand, with a follow-up reminder after 7 days. Logging a link
    your teammate already has warns you first.
- **Team**: who is on the team, last sync, and **Check a link**: paste any profile, job link, @handle or email
  before you message someone by hand. It says *free*, *yours* or *taken by (name)*.
- **Stats**: 14-day chart, reply rate per platform, open applications.
- **Settings**: your name/offer/skills, caps and follow-up days per platform, email, daily volume, lead filters,
  job feeds on/off, your country (jobs not open to it are skipped), theme.
- Stop for the day on a platform that shows "action blocked" or "try again later".

The queue is http://127.0.0.1:8777/ (bookmark it in the Chrome profile you send from). Optional: the same queue as a
Chrome side panel via `chrome://extensions` → Developer mode → Load unpacked → the `extension` folder.

## Where jobs come from (all allowed by the sites themselves)
Scanned every morning, only through feeds or APIs their owners publish for this, once a day, with a link back:
YT Jobs, Himalayas, Remote First Jobs, Remote OK, Jobicy, Work With Indies, Open Assignments, Dribbble,
We Work Remotely (title + link only, their terms forbid storing the rest), and Bluesky "hiring" posts (read only).
Indeed, Upwork, LinkedIn, Freelancer.com, Fiverr, Glassdoor and Reddit forbid bots in their terms, so they are
links you open yourself. Never add a scraper for them: it gets accounts banned.

## Email (optional, off by default)
Channels whose only public contact is a business email can go into an Email lane. You send each one yourself from
your own mailbox (Gmail / Outlook compose opens with everything filled in). Anti-spam law needs your postal address
and an opt-out line in every cold email, so the lane stays off until both are filled in under Settings → Email.
Keep it under 10 a day, ideally from a separate business address.

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
- `claimed/<name>.txt`: hashes of every person (channel, X / Instagram / LinkedIn / Facebook profile, email) and
  every job post each member has queued, contacted or applied to. So the same creator found twice (a channel
  and a job post, two channels with one X account) is still only pitched once. No names or links are stored.
- `installs/<name>.txt`: which PC uses that name. Two PCs can't both be "sami": the second one is refused.

Safety nets, all automatic:
- The server syncs every 20 minutes while it runs, and the morning run syncs before drafting and after importing.
- A PC that doesn't know the team yet (never synced, not in the list, or using a name another PC has) **drafts
  nothing** and says why in the queue and the morning report, instead of guessing and double-pitching.
- A folder downloaded as a ZIP (no git) can still read the team list and everyone's claims, but can't share its
  own. Install with `git clone` (see above) so your teammates see who you contacted.
- Before messaging anyone outside the queue, use **Team → Check a link** (or `python engine.py check LINK`).
- Someone working alone sets `"solo": true` under `team` in config.json.

## Updating
Ask Claude Code: "Update Outreach: `git pull` in Documents\Outreach, then restart the server by its PID on port 8777."
Your `config.json`, `outreach.db`, `work/` and `reports/` are never in the repo, so an update never touches your
settings, leads or history. Teammates don't commit code to `main`; send changes to the repo owner.

## Files
- `engine.py daily`: the whole morning collection in one command. `engine.py finish`: check, import, report, sync.
- `engine.py check LINK`: free / yours / a teammate's. `engine.py team-sync [--take-over]`, `engine.py team-join NAME`.
- `team.py`: the team split, claims and the `team` branch sync.
- `DAILY-TASK.md`: what the 7am Claude run does, including the message-writing rules.
- `SETUP.md`: the install steps Claude follows on a new PC.
- `config.example.json`: the starting `config.json` (caps, ramp-up, follow-up days, filters, job keywords, your profile).
- `server.py`: local API and queue page on 127.0.0.1:8777 (refuses requests from other websites).
- `JOB-SITES.md`: which job sites are worth it and who can apply (live research 2026-09-28).
- `python tools/selftest.py`: regression tests against copies of your data (needs a few days of data first).

## Not built (on purpose)
- **Automatic sending** of any DM or email: that is what gets accounts banned, and it breaks the platforms' rules.
- **Scrapers for Indeed / Upwork / LinkedIn / Freelancer / Reddit / Fiverr**: their terms forbid it (Upwork even bans
  job-alert extensions). They stay one-click links + the tracker.
- **Bulk email tools**: the email lane is one hand-sent message at a time, from your own mailbox.
