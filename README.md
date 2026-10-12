# Outreach: daily client finder + one-click send queue

For freelance video editors. Every morning at 7:00 a Claude run collects editing jobs from many places (job-board
feeds, YT Jobs, and the job-alert emails Indeed / LinkedIn / Glassdoor & co. send you) and writes a proposal for
each, plus a short DM for people who publicly posted that they're hiring. You open the queue page and work through
it: **Open job + copy** → paste → submit → **Applied ✓ → next**, and the same for DMs.

**Applications first, DMs second.** Applying to posted jobs carries no account risk, so that is the main lane.
DMs are capped low and, in the default *Low-DM mode*, only go to people who asked for an editor in public.

Nothing is ever sent automatically. The queue never reads or touches LinkedIn, Instagram, X or job-board pages: it
only opens a URL and copies text. Sites that forbid bots are never automated, not even through "turn any site into an
API" tools: that is exactly what gets accounts banned.

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
  - *Proposals*: drafted for every new job from the feeds, YT Jobs and your alert emails. `O` open job + copy,
    paste and submit, then `A` = **Applied ✓ → next** (logs it in the Tracker and opens the next one, copied).
  - *Alerts*: connect a mailbox once (an app password, stored encrypted outside the folder), then switch on job
    alerts on each listed site. Every morning the alert emails become proposals. Use a separate mailbox just for alerts.
  - *Apply kit*: your standard answers (intro, why me, samples, software, rate, turnaround, payment) with one-click
    Copy for application forms. Edit them in Settings.
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

## The best way to get editing clients (researched 2026-10-12)
Measured on 345 live YT Jobs editor posts plus hiring creators' own advice. The queue's **Today's plan** card walks you
through it every day.
1. **Niche proof first.** 57% of posts ask for samples and creators reject off-niche reels. Make 3 spec pieces of 45-90 s
   in your niche (e.g. an animated map sequence, a money story with stat reveals, a photo-parallax cold open) + one
   30 s vertical cut, labelled as spec, on your YT Jobs profile and YouTube.
2. **Fast, tailored applications to live posts** (the main lane, no ban risk): ~3 a day, best-paid and under 48 hours
   old. Start with their keyword if they ask, answer every question, show 1-2 matching samples first, give a flat
   price per video + turnaround + weekly capacity, and one specific idea about their latest video.
3. **One 10-20 s spec clip a day** for the single best-fit job (the ⭐ spec pick). This is what gets you shortlisted.
4. **One spec-first email a day** to a channel you can visibly improve: a 20-30 s re-edit of their own video, from
   your own address, follow-ups on day 3 and 7. Small, targeted lists reply ~3x more than big blasts.
5. **A few warm DMs only** (people who posted they're hiring). Mass DMs: 6,000 sent got 2 low-paid clients in one test.
6. **After the first hire**: ask for a credit, offer a retainer after 2-3 videos, ask for referrals (where most
   freelancers' work ends up coming from).
Weekly: 3 X posts with before/after clips, an editing breakdown video in weeks 2 and 4. If replies stay under 1 in 20
after 30 applications, change the samples, not the volume.

## Where jobs come from (all allowed by the sites themselves)
Scanned every morning, only through feeds or APIs their owners publish for this, once a day, with a link back:
YT Jobs, Himalayas, Remote First Jobs, Remote OK, Jobicy, Work With Indies, Open Assignments, Dribbble,
We Work Remotely (title + link only, their terms forbid storing the rest), and Bluesky "hiring" posts (read only).
Indeed, Upwork, LinkedIn, Freelancer.com, Fiverr, Glassdoor and Reddit forbid bots in their terms, so they are
links you open yourself (or job-alert emails, see Alerts). Never add a scraper for them: it gets accounts banned.

**International, best-paid first.** Only jobs open worldwide are kept (no country-locked posts), low-paying ones
(e.g. $30 shorts edits) are skipped, and the rest are ranked by pay: YT Jobs pay is read per project / hour / year.
Agency posts get a small boost (they hire again and again) and an "agency" tag. Both switches live in Settings → Job
search. Measured on YT Jobs (Oct 2026): median $325 per video, $438 at 100K+ channels, ~18 well-paid posts a week.

## Email (optional, off by default)
Channels whose only public contact is a business email can go into an Email lane. You send each one yourself from
your own mailbox (Gmail / Outlook compose opens with everything filled in). Anti-spam law needs your postal address
and an opt-out line in every cold email, so the lane stays off until both are filled in under Settings → Email.
Keep it under 10 a day, ideally from a separate business address.

## Safety rules built in
- **Low-DM mode (default)**: social DMs only to people who publicly posted they're hiring, at X 10 / Instagram 8 /
  LinkedIn 5 / Facebook 3 a day. Switch to "Everyone" in Settings only if your accounts are old and replies are good.
- Week 1 runs at 60% of your caps and week 2 at 80%. Full caps after that.
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
- `inbox.py`: reads job-alert emails from your mailbox (read-only, never marks anything) and adds the jobs.
  `python engine.py inbox --dry-run` lists what it would add. The login lives in `%APPDATA%\Outreach\inbox.json`,
  password encrypted with Windows DPAPI.
- `sources.json`: every job source shown in the Jobs tab (auto feeds, alert sites, manual links). Edit to add more.
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
