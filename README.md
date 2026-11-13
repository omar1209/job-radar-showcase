# Job Radar

[![Tests](https://github.com/omar1209/job-radar-showcase/actions/workflows/tests.yml/badge.svg)](https://github.com/omar1209/job-radar-showcase/actions/workflows/tests.yml)

An hourly pipeline that finds new entry-level jobs in Metro Vancouver (and remote in Canada), screens
each one with Claude against a candidate's resume and career goals, and tracks every application in a
Google Sheet that updates itself from email.

> I built this for my own job search and run it every day. This is a public copy: the candidate files
> in `config/` are fictional samples, and my real resume, screening data and run history stay in a
> private repo. Omar Ibrahim, Vancouver, BC.

## What it does

- **Collects** postings every hour from LinkedIn, Indeed, the BC and federal (GC Jobs) public service
  boards and about 85 employers' own careers sites (Workday, Greenhouse, Lever, iCIMS, Taleo and others).
- **Screens** each new posting in two stages: a cheap Claude Haiku check throws out jobs outside the
  target career tracks, then Claude Sonnet scores relevance to the candidate's goals and the chance of
  being shortlisted, and lists the skills the posting wants that the resume can't back up yet.
- **Reports** matches to a Google Sheet with an email alert, including pay and closing date when the
  posting gives them, plus a Monday summary of applications, response rates and recurring skill gaps.
- **Tracks** applications by reading confirmation, rejection and interview emails in Gmail and moving
  each row to the right status.

## How it works

```
 LinkedIn + Indeed ─┐   one row per job     Claude Haiku        Claude Sonnet          Google Sheet
 ~85 employers' own ├─► (branches listed ─► "is this in one ─►  relevance to your  ─►  + email alert
 careers sites      │   together), drop     of my career        career x chance of     (+ posting text to
 BC + federal (GC   │   wrong titles,       tracks, and how     being shortlisted       paste into Claude)
 Jobs) public svc. ─┘   places, French      likely a fit?"      (likely fits only)
 Outlook ──forward──► Gmail ──► Claude reads application emails ──► Status column updates itself
```

A run is started hourly by the Sheet's Apps Script (GitHub's own cron skipped too many runs), executes
on GitHub Actions, and commits what it has already seen back to the repo so the next run only looks at
new postings.

## Design notes

- **Cost control.** Free filters (titles, locations, pay, language) run before any API call. Only
  postings that pass Haiku's quick check reach Sonnet, and the screening prompt is cached across calls.
  The Sheet also tracks GitHub Actions minutes and drops to core hours before the free tier runs out.
- **Untrusted input.** Postings are text written by strangers, and some hide instructions aimed at AI
  screeners. [`radar/guard.py`](radar/guard.py) strips hidden HTML and zero-width characters and pulls
  instruction-like lines out before scoring, and flags the posting so the candidate knows.
- **One job, many postings.** The same role is often posted at several branches and copied to
  LinkedIn and Indeed. [`radar/rollup.py`](radar/rollup.py) groups them into one row and flags new
  locations that appear later, since a new branch is often a second chance at the same job.
- **Finding new sources.** When an employer keeps showing up on LinkedIn or Indeed,
  [`radar/discover.py`](radar/discover.py) follows its apply links and, if they lead to a careers
  system the radar can read, starts reading that site directly.
- **Monitoring.** A watchdog emails when scheduled runs stop, a careers site fails several runs in a
  row, or the GitHub token is about to expire. Each run's findings and cost go to a Runs tab.

## Stack

Python 3.12 · Claude API (Haiku and Sonnet) · GitHub Actions · Google Apps Script and Sheets ·
Gmail · JobSpy for LinkedIn and Indeed

About 2,900 lines of Python and 1,200 lines of Apps Script.

## Repository layout

| Path | Contents |
|---|---|
| [`radar/`](radar) | The pipeline: [`main.py`](radar/main.py) runs collect, group, filter, score and upload |
| [`radar/sources/`](radar/sources) | Readers for job boards and careers systems |
| [`apps_script/Code.gs`](apps_script/Code.gs) | The Sheet side: tracker tabs, alerts, email parsing, scheduling, watchdog |
| [`config/`](config) | Search terms and employers, plus sample resume and evidence files |
| [`tests/`](tests) | 267 Python test cases and 23 Apps Script tests |
| [`tools/`](tools) | One-off scripts for finding employers' careers systems |
| [`docs/`](docs) | [Setup guide](docs/setup.md), [Claude Project workflow](docs/claude_project.md) for tailoring resumes, [volunteering notes](docs/volunteering.md) |

## Tests

```bash
python -m pytest -q tests                 # filters, grouping, careers-site readers, full runs with fake sources
node --test tests/apps_script/code.test.js  # Apps Script against a stand-in for Google Sheets
```

Both run on GitHub Actions after every change to code or config.

## Running your own copy

Setup takes about 20 minutes: a Google Sheet with the Apps Script, three GitHub secrets and an
Anthropic API key. Step-by-step instructions, day-to-day use and tuning are in
[docs/setup.md](docs/setup.md).

In the first week I measured, the Claude API cost about $2.70 per weekday. Prompt caching changes and
the Haiku cutoff since then should bring it to roughly $20 to $30 a month. GitHub Actions stays within
the free tier.
