# The daily trigger — a template, and nothing here installs it

This is the schedule for `python -m volab.daily`: a `launchd` job for this machine, and an
equivalent `cron` line. **Read the next section before copying either.** Everything below is text
for you to act on: **this repository installs nothing.** No code in it writes the job anywhere,
loads it, or enables it.

## Why nothing here installs it

Phase 4's brief is to build the scheduler so that it *can* run, and to leave it unrun. Three
reasons, in order of how much each one matters:

- **A persistent trigger on your machine is yours to create.** It outlives the session that wrote
  it, it runs while nobody is watching, and removing it is a step somebody has to remember. A
  repository that did this for you would be making that choice for you.
- **A scheduler that installed itself during its own construction would be scheduling a tree that
  is mid-change.** The first job to fire would run whatever half-built state the checkout was in
  that morning, and an unattended run against an unfinished tree is the failure the whole phase
  exists to avoid. This is anti-goal A5 in
  `docs/superpowers/specs/2026-11-03-phase4-unattended-daily-runs-design.md`, and section 4.5 there
  says the same thing in one paragraph.
- **Nothing should be able to install it by accident.** `tests/test_daily_trigger.py` holds that
  line: a derived scan over every `.py` file under
  `src/` and `tools/` fails if any code names a LaunchAgents path, `crontab`, `launchctl`, `plistlib`
  or a `.plist` file. The same file checks that the plist below parses and that the command it
  schedules is one the CLI accepts. It never runs that command.

The scan covers `src/` and `tools/` and nothing else. It does not read `tests/`, `config/` or
`.github/`; the repository has one workflow, `ci.yml`, and it has no `schedule:` trigger, which was
checked by searching it for `schedule` and `cron` and is not asserted by any test.

## Which verb, and what it does today

`python -m volab.daily` has **two verbs: `plan` and `run`.** `plan` prints what a run would do and
writes nothing — no run record, no ledger line, no payload. `run` does the work: it writes a start
line, executes the stages, and writes a completion line (story P4-03b, on top of P4-03's runner).

**The plist and the cron line below schedule `plan`, deliberately, and you change one word to make
them real.** Replace `plan` with `run` after `volab.daily` in the plist's `ProgramArguments`, and the
same word in the cron line, and nothing else. They are left on `plan` because a schedule that ran
the real stages on the day it was installed would run `volab.ingest backfill`, which fetches from
the network and writes the **tracked** `data/vendor_payloads/` — see the last section.

Two consequences of that, so that neither surprises you:

- **Scheduled today, the job does nothing but print a plan.** That is a usable dry run of the
  trigger itself (does it fire, does it find `uv`, does it log), and no more.
- **Every real date is past `dev_end`, so today the job exits 1.** The plan refuses any date past
  `dev_end` and the CLI exits 1 on a refusal (a closed exchange and an already-completed date are
  0). That refusal is the normal outcome of this job until the user triggers the holdout reveal, and
  it is not a fault in the trigger.
- **Which region a real date is in, stated exactly, because an earlier version of this document got
  it wrong.** `dev_end` is 2025-12-31 and `holdout_end` is 2026-10-30 (story P4-05), so a date in
  November 2026 is in the **`live`** region and not in the holdout. It is refused all the same: the
  plan's only test is `as_of > dev_end`, and `live` is unreachable until the reveal because the
  sealed window lies between here and there. **The refusal's wording is wrong for this case** — it
  says "the holdout is sealed" for a live date too. That is a reporting defect, carried on the board
  and owned by P4-08b, not a defect in the refusal itself.

The date is taken inside the CLI as *today in New York*, so the schedule below has no date in it.
The exchange calendar decides what a closed day means: on a holiday the job runs, finds no session,
and exits 0.

## The time

The job fires at **18:30 New York time on weekdays**, which is after the 16:00 close with room for
the vendors to publish. Both schedulers read the *machine's* clock, and this machine is on Pacific
time, so 18:30 in New York is **15:30** here. If the machine's timezone changes, so must the hour.

If the machine is asleep at the scheduled moment, `launchd` runs the job when it wakes; `cron` does
not, and the day is simply missed. And if it wakes after midnight in New York, the CLI's default
date is the *next* day, not the one that was missed. A missed day is made up by running the CLI by
hand with an explicit `--as-of`.

## `launchd`

Save this as `~/Library/LaunchAgents/local.volab.daily.plist`. Paths are for the main checkout at
`/Users/dhruvshah/dev/Autonomous Volotility Forcasting/vol-lab`, **not a worktree**: a worktree is
by definition a tree that is mid-change. The program is the absolute path of `uv`, because a
`launchd` job's `PATH` is nearly empty, and the `PATH` set below is there so that `git` resolves
once `run` records the commit it ran at. Logs go to `~/Library/Logs`, never into the repository,
where they would dirty the tree.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>local.volab.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/uv</string>
        <string>run</string>
        <string>--locked</string>
        <string>python</string>
        <string>-m</string>
        <string>volab.daily</string>
        <string>plan</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/dhruvshah/dev/Autonomous Volotility Forcasting/vol-lab</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/bin:/bin</string>
    </dict>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>30</integer></dict>
    </array>
    <key>StandardOutPath</key>
    <string>/Users/dhruvshah/Library/Logs/volab-daily.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/dhruvshah/Library/Logs/volab-daily.log</string>
</dict>
</plist>
```

**To install it, you run:**

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.volab.daily.plist
```

**To check it, and to fire it once on demand,** run `launchctl print gui/$(id -u)/local.volab.daily`
and `launchctl kickstart gui/$(id -u)/local.volab.daily`.

**To uninstall it, you run:**

```sh
launchctl bootout gui/$(id -u)/local.volab.daily
rm ~/Library/LaunchAgents/local.volab.daily.plist
```

## `cron`

The same schedule and the same verb, as one line. The path has spaces in it, so it is quoted, and
the line has no `%`, which `cron` treats as a newline.

```cron
30 15 * * 1-5 cd "/Users/dhruvshah/dev/Autonomous Volotility Forcasting/vol-lab" && /opt/homebrew/bin/uv run --locked python -m volab.daily plan >> "$HOME/Library/Logs/volab-daily.log" 2>&1
```

**To install it, you run `crontab -e` and paste the line in.** Use that, and not `crontab <file>`:
the second form replaces your whole table. macOS may also ask you to grant `cron` Full Disk Access
before the job can read the checkout.

**To uninstall it, you run `crontab -e` again and delete the line.**

`cron` does not read `~/.zshrc` and starts jobs with a minimal `PATH`, which is why the line names
`uv` by its absolute path. `git` is in `/usr/bin`, which that minimal `PATH` covers; if something
else is not found under `cron` once `run` exists, put
`PATH=/opt/homebrew/bin:/usr/bin:/bin` on its own line above the job.

Use one of the two, not both: two triggers would run the day twice.

## What a scheduled `run` will do to the working tree — named here, not solved

**`volab.ingest backfill` writes `data/vendor_payloads/`, and that directory is tracked in git.**
`backfill` is one of the stages a daily run plans. So once `run` exists, an unattended daily run
dirties the working tree every morning, and **every record written that day carries
`git_dirty: true`** — the flag this project uses to tell a run against a modified tree from a clean
one (R70).

Today's `plan` verb writes nothing, so a scheduled `plan` does not do this. It begins with `run`.

This document does not fix it and does not decide it. The board owns the decision, in the table
"Found while building the first slice, and not fixed here" in `docs/phase4-sprint-board.md` (the row
names the story that owns it, which has moved once already, so read the board rather than trusting a
name quoted here): either the daily job commits the payloads it archives, or the dirty
flag's meaning has to be stated for a scheduled run. Until that row closes, do not rely on
`git_dirty` to tell *the job's own archive* from *somebody edited the code* on a scheduled day.
