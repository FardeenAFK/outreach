# Outreach setup on a new PC (instructions for Claude)

You are setting up Outreach for the person using this PC, a freelance video editor joining a small team. Every
morning it finds video-editing clients (mainly people who posted a hiring ad on YT Jobs, plus YouTube channels in
their niches), finds their Instagram / X / LinkedIn (Facebook as a fallback), and drafts a personal first message for
each. The user sends every message themselves from the queue page at http://127.0.0.1:8777/ in their own Chrome
(open profile + copy, paste, Enter, then "Sent → next"). Nothing is ever sent automatically. Keep it that way: never
add code that sends, posts, or logs into anything.

**Team split.** Every lead belongs to exactly one team member, decided by a hash (`team.py`). The member list and
everyone's claimed leads (as hashes) live on the repo's `team` branch. `engine.py daily` and `engine.py finish` sync
it. Never edit `members.json` by hand, never remove or reorder names, and never touch another member's
`claimed/<name>.txt`.

## Already have an Outreach folder?
If `%USERPROFILE%\Documents\Outreach` existed before this clone (an older zip install has no `.git` folder):
1. Stop its server by PID, never by process name: in PowerShell
   `Get-NetTCPConnection -LocalPort 8777 -State Listen | Select-Object -ExpandProperty OwningProcess`, then
   `Stop-Process -Id <that pid>`.
2. Rename the old folder to `Outreach-old`, clone the repo to `%USERPROFILE%\Documents\Outreach`, then copy these
   from `Outreach-old` into the clone if they exist: `config.json`, `outreach.db`, `claimed_elsewhere.json`, `reports\`.
3. Their `config.json` keeps their settings. The team name is in `config.json -> team.me` (the first teammate's old
   zip install uses `"friend"`): that name is already in the team list, so in step 5 run `team-sync`, not `team-join`.
4. Delete the old Startup shortcut only if its target points at `Outreach-old` (step 6 recreates it).

## Steps

1. **Folder.** Work from `%USERPROFILE%\Documents\Outreach` (the clone) for everything below.

2. **Tools.** `python --version` must be 3.10 or newer (`winget install -e --id Python.Python.3.12` if missing). Then
   `python -m pip install -U yt-dlp` and check that `python -m yt_dlp --version` prints a version. `git --version`
   and `gh auth status` must work (`winget install -e --id Git.Git` / `GitHub.cli`). If gh is not signed in, ask the
   user to run `gh auth login` themselves and finish it in the browser, then run `gh auth setup-git`. Never type,
   ask for, or store their password or token.

3. **Ask the user, in one message, and wait for the answers** (skip any that an existing config.json already has):
   - Their first name, as it should appear at the end of messages.
   - A short team name: lowercase, letters/digits only, usually their first name (e.g. `sami`). It must not already
     be in the team list (`git show origin/team:members.json` after `git fetch origin team`).
   - Their editing skills, styles and niches, in their own words, and the software they actually have installed.
   - Editing software they do NOT have that clients often ask for (Premiere Pro, DaVinci Resolve, CapCut, Final Cut).
   - Any software they built themselves that must stay private (messages will never mention it).
   - Which country they live in (job feeds skip posts that aren't open to it), and are they based in the US?
     (Default: no. Jobs that require US residence get skipped.)
   - Roughly how old their Instagram, X and LinkedIn accounts are, and whether to use Facebook at all.
   - Do they want the optional email lane? Only if yes: the postal address line (city + street or PO box) that
     anti-spam law requires in every cold email. Otherwise email stays off.
   - For their apply kit: their rate, usual turnaround, how they get paid (Payoneer, Wise, bank), and a portfolio or
     sample link if they have one.

4. **Write config.json.** First install: copy `config.example.json` to `config.json`. Fill in `sender_name` and
   `accounts.me.label` (their name), `profile` (their skills in their own words, nothing they didn't say),
   `not_installed` and `private_tools` (lists of names), `us_based`, `jobs.country`, and `ramp.start_date` = today
   (YYYY-MM-DD). Set `filters.niches` to 8-12 YouTube search phrases for their niches if they differ from the defaults.
   Keep `dm_mode` = "warm" (Low-DM mode) and the default caps (X 10, Instagram 8, LinkedIn 5, Facebook 3). For
   accounts younger than about 6 months halve them. No Facebook: cap 0.
   Email lane wanted: set `email.address_line` and `accounts.me.caps.email` to 5 (never above 10). Otherwise leave 0.
   Write `apply_kit`: 6-8 entries of {"label", "text"} built only from their answers (Short intro, Why me,
   Samples / portfolio, Software, Location & time zone, Rate, Turnaround, Getting paid). If they have no portfolio,
   offer a free test edit instead. Never invent clients, years or results.
   If config.json already existed (update or migration), keep it and only add missing keys from the example.

5. **Join the team.** New member: `python engine.py team-join <team name>`. Migrated install whose name is already in
   the list: `python engine.py team-sync`. Either way the output must list the members and say
   `this PC is '<name>'`. If it prints a WARNING or a CONFLICT, fix that before going on: until then this PC drafts
   nothing (on purpose, so it can never pitch a teammate's clients). A CONFLICT means another PC already uses that
   name: pick another name with team-join, or, if it is truly the same person on a new PC, `team-sync --take-over`.

6. **Start the local server and make it start at login.** In PowerShell:
   ```
   $dir = "$env:USERPROFILE\Documents\Outreach"; $pyw = (Get-Command pythonw).Source
   $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$([Environment]::GetFolderPath('Startup'))\Outreach server.lnk")
   $lnk.TargetPath = $pyw; $lnk.Arguments = "`"$dir\server.py`""; $lnk.WorkingDirectory = $dir; $lnk.Save()
   Start-Process $pyw "`"$dir\server.py`"" -WorkingDirectory $dir -WindowStyle Hidden
   ```
   Check it with `curl -s http://127.0.0.1:8777/api/health`, which must return `{"ok": true, ...}`.
   Never kill python processes by name (other tools use python); stop a server only by its PID.

7. **The queue page.** Tell the user to open **http://127.0.0.1:8777/** in THEIR Chrome (the profile signed into
   their own Instagram / X / LinkedIn) and bookmark it. The first time they click **Open profile + copy**, Chrome may
   block the pop-up: they click "Always allow" for 127.0.0.1 and click again.

8. **Daily routine.** Create a scheduled task with the scheduled-tasks tool:
   - taskId `outreach-daily`, title `Outreach: find clients + draft messages (7am)`, cron `0 7 * * *`
   - prompt:
     > You are running the daily client-outreach job for the user of this PC, a freelance video editor. Read
     > `%USERPROFILE%\Documents\Outreach\DAILY-TASK.md` in full and follow it. The run is three steps, each ONE
     > command, so it never stalls on approvals: (1) `python engine.py daily` with Bash, in the background, then
     > wait (15-40 min); (2) write `work/drafts.json` from `work/leads_to_draft.json` and `work/jobs_to_draft.json`
     > by the rules in DAILY-TASK.md (parallel subagents if more than 30 leads); (3) `python engine.py finish
     > work/drafts.json`, fixing drafts and re-running it if it lists problems. Use Bash with `python` only, no
     > PowerShell, and don't run engine sub-commands separately. Hard rules: never open, log into, or send anything
     > on LinkedIn, Instagram, X, Facebook, email or any job board, because this job only finds and DRAFTS. Never
     > invent experience, clients or years. Every message is unique; Instagram and Facebook messages contain no
     > links. Never kill python processes by name. If a step fails, note it and continue. Finish with a 4-6 line
     > report: drafted per platform, job proposals, yesterday's sent/replies, names of anyone who replied, failures.
   Tell them the Claude app must be open at 7:00 (otherwise the run happens when it next opens), and that they
   should click **Run now** once so later runs don't stop for tool approvals.

9. **First run now.** `python engine.py jobs --full`, then `python engine.py job-leads --limit 200` in the background
   (30-40 minutes: it paces requests politely), then follow `DAILY-TASK.md` from step 1 (`python engine.py daily`,
   draft, `finish`). Only this PC's share of the leads gets exported; the rest belong to teammates.

10. **Job alerts (the biggest source, 10 minutes, done by the USER).** Open http://127.0.0.1:8777/ → Jobs → Alerts
    and walk them through it. They create an app password for a mailbox (ideally a new mailbox only for job alerts)
    and type it into the page THEMSELVES: never ask for it, type it, or store it anywhere else. Then they click each
    site in "Turn on job alerts", search for their keyword, and switch on daily email alerts to that mailbox.
    "Test connection" shows which sites are already sending. From the next morning the alerts become proposals.

11. **Tell them how to use it,** in 5 lines: open http://127.0.0.1:8777/ → Jobs → Proposals: `O` open job + copy →
    paste → submit → `A` Applied → next. Then Today for the few DMs → **Open profile + copy** → paste
    in the DM box → Enter → **Sent ✓ → next**. LinkedIn: connect with NO note first, then mark "Accepted" in the
    Replies tab when they accept. When someone replies, click **Replied** (that stops follow-ups). Stop for the day
    if a platform shows "action blocked" or "try again later". Before messaging anyone by hand (an X post, a Reddit
    job), paste the link into **Team → Check a link**; log hand-made applications under **Jobs → Tracker**.
    Press `?` in the queue for keyboard shortcuts.

## Accounts (tell them once)
They run this on THEIR OWN Claude account. Sharing code is fine; sharing a Claude login, token or account between
people is against Anthropic's terms and can get every account involved suspended. Same for social accounts: each
person sends only from their own.
