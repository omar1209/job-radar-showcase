# Job Radar

> Public showcase copy of a system I built and run daily for my own job search. The candidate
> files in `config/` are fictional samples; my real resume, screening data and run history stay in a
> private repo. Built by Omar Ibrahim, Vancouver, BC.

Finds new entry-level jobs in Metro Vancouver (and remote in Canada) every hour,
screens each one against your career goals and your master resume with Claude, emails you
the good ones, and tracks every application in a Google Sheet. Resume and cover-letter
tailoring happens in your Claude Project: see [docs/claude_project.md](docs/claude_project.md).

```
 LinkedIn + Indeed ─┐   one row per job     Claude Haiku        Claude Sonnet          Google Sheet
 ~85 employers' own ├─► (branches listed ─► "is this in one ─►  relevance to your  ─►  + email alert
 careers sites      │   together), drop     of my career        career x chance of     (+ posting text to
 BC + federal (GC   │   wrong titles,       tracks, and how     being shortlisted       paste into Claude)
 Jobs) public svc. ─┘   places, French      likely a fit?"      (likely fits only)
 Outlook ──forward──► Gmail ──► Claude reads application emails ──► Status column updates itself
```

Employers that keep posting good jobs on LinkedIn/Indeed get their own careers site added
automatically when it's one Job Radar can read, and a watchdog emails you if runs or sources stop working.

## One-time setup (about 20 minutes)

### 1. Tracker Sheet (in your work Gmail address)

1. Sign in to the **work Gmail** account, then go to <https://sheets.new>. Name the sheet **Job Radar**.
2. **Extensions → Apps Script.** Delete everything in `Code.gs`, paste in the full contents of
   [`apps_script/Code.gs`](apps_script/Code.gs), and press **Save** (💾).
3. **Project Settings** (⚙️ on the left):
   - Tick **Show "appsscript.json"**. Then open `appsscript.json` in the editor and set
     `"timeZone": "America/Vancouver"`. Save.
   - Under **Script properties → Add script property**, add:
     - `ANTHROPIC_API_KEY` = your Anthropic key
     - `ALERT_EMAIL` = the address that should receive alerts
4. Back in the editor, choose **setup** in the function dropdown and press **Run**.
   Google asks for permission. Choose your work account, then **Advanced → Go to (unsafe) → Allow**.
   The warning appears because you wrote the script yourself and Google hasn't reviewed it. That's normal.
5. Open the **Execution log** at the bottom. It prints `SHARED_SECRET = ...`. Copy that value.
6. **Deploy → New deployment →** ⚙️ **Web app.** Set *Execute as*: **Me** and *Who has access*: **Anyone**.
   Press **Deploy** and copy the **Web app URL**.
   (Only requests carrying your secret are accepted, so "Anyone" doesn't expose the sheet.)
7. Optional: choose **testAlert** → **Run** to send yourself a sample alert.

### 2. GitHub secrets

In this repo: **Settings → Secrets and variables → Actions → New repository secret.** Add three:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your Anthropic key |
| `SHEET_WEBAPP_URL` | the Web app URL from step 1.6 |
| `SHARED_SECRET` | the value from step 1.5 |

### 2b. Reliable hourly runs (GitHub's own schedule often skips runs)

1. On GitHub: your profile picture → **Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**.
   - Name: `job-radar scheduler`. Expiration: **Custom**, a year from now.
   - Repository access: **Only select repositories → job-radar**.
   - Permissions → Repository permissions → **Actions: Read and write**.
   - **Generate token** and copy it. You won't see it again.
2. In Apps Script, under **Project Settings → Script properties**, add `GITHUB_TOKEN` = the token.
3. Run **setup** once. From then on the Sheet starts a run every hour during search hours,
   unless one is running or started in the last 30 minutes.

### 3. First run

Go to **Actions → Job Radar → Run workflow**. The first run handles the whole current backlog,
up to 400 postings, so it takes 10–15 minutes and costs about $1–2. Later runs take 3–4 minutes.
Check the Sheet's **Runs** tab: one row is added per run.

### 4. Outlook forwarding (done)

Outlook forwards to the work Gmail. Every 15 minutes the Sheet reads new application emails
("Thanks for applying", "Unfortunately…", interview invites) and updates **Status**.
That includes applications you sent outside the radar.

## Import applications you sent before the radar

1. In Outlook, search for `thank you for applying`, then `application received`, `your application`,
   and `unfortunately`. Do this for anything older than when you set up forwarding.
2. Tick the emails (the circle next to each). Do about 20 at a time.
3. Click **Forward**. Outlook attaches them all to one email. Send it to **your work Gmail address**.
4. Within 15 minutes each one becomes a row in **Jobs**, with its original date. A later rejection
   for the same job updates that row, and the radar won't re-add jobs you've already applied to.
   A big batch can take two or three 15-minute rounds.

## Day to day

1. An email arrives: **"Job match 82: Research Analyst at RBC"**, with the score, the reasoning
   (including separate *relevance* and *attainability* scores), the skills the posting stresses,
   the ones your resume doesn't show yet, and the pay and closing date when the posting gives them.
2. In the Sheet, click that row's **Posting text** cell, copy it (⌘C), and paste it into a new chat
   in your Claude Project. The [Claude Project guide](docs/claude_project.md) covers the rest:
   keywords, questions, tailored resume and cover letter.
3. Apply. The confirmation email changes Status to **Applied** within about 15 minutes, and the row
   moves to the **Applied** tab. Later emails move it on to **Assessment / Interview / Offer / Rejected**.
   You can also set Status yourself; the row moves as soon as you change it.
4. If a job isn't for you, set Status to **Not applying**. It moves to **Screened out**, so **Jobs**
   and **Volunteer** only ever show fresh leads, highest score first.
5. **Volunteer roles** go to the **Volunteer** tab once a day, scored on career value. The reason
   includes the time commitment and a sample resume line. Roles scoring 75+ arrive as "Volunteer match"
   emails. See [docs/volunteering.md](docs/volunteering.md) for programs worth joining directly.
6. **Other postings** lists the same job's other branches and copies, each linked to its posting
   (one row per job, not one per branch). When a job you're tracking is posted in a **new place**
   later, it's added there with a ★, and if the row is in **Applied** or scores 70+ you get a
   *"New location"* email: often a second chance at the same job.
7. When the Project gives you **Evidence bank additions**, paste them into
   [`config/evidence_bank.md`](config/evidence_bank.md) (edit it on github.com with the ✏️ button).
   Screening uses that file too, so scores improve as you answer questions.
8. **Every Monday at 8am**, a *Job Radar week* email starts with **your week**: applications sent,
   responses and interviews, response and interview rates by job family and by where the job came from,
   how fast you apply to 80+ jobs, and strong jobs still sitting at New. It then compares the screening
   with your own decisions: scores of jobs you applied to versus skipped, jobs scored 75+ that you
   skipped and jobs under 60 that you applied to. The **Progress** tab has the details. When you skip a
   job, pick a reason in **Why not** (far right): that alone marks it Not applying, and the reasons show
   what the screening keeps getting wrong. Last, the email (and the **Skill gaps** tab) shows which
   skills keep appearing in good postings that your resume can't back up yet, as a share of
   matches, with a concrete next step for each. It also shows which blockers keep ruling jobs out.
   To see it early, run **weeklySkillReport** in the Apps Script editor once you have a few days of results.
9. A **Notes** entry starting *"Posting contained text aimed at AI tools"* means the posting hid
   instructions for AI tools. They're removed before scoring. These are often traps to catch AI-written
   cover letters, so read that posting yourself and personalise your letter.
10. Sometimes scroll the **Screened out** tab. Everything Claude rejected is there with a reason,
   so you can check it isn't too strict. Rows starting *"Unlikely fit (quick check N/100, not screened)"*
   skipped the careful screen; if one of those looks good, apply anyway and tell Claude, so the cutoff
   (`min_quick`) can be lowered.
11. **Sources** tab: for each careers site and each LinkedIn/Indeed search, how many new jobs and good
   matches it found in the last 7 and 30 days, how many *only it* found, and what that cost. Useful
   before trimming searches: a search with no "only found here" matches for a month adds nothing.
   Below the table: employers whose good matches came only from LinkedIn/Indeed, careers sites added
   automatically, and any removed because they stopped working.
12. **Watchdog emails** (rare): *no successful run since...* (two or more scheduled runs missed),
   *GitHub token expires in N days* (two weeks ahead, with the steps to renew it), *could not start a
   run*, and *N sources need attention* (a careers site failing for 6 runs in a row, or suddenly listing
   nothing). LinkedIn and Indeed still cover those employers meanwhile.
13. **Runs** tab: one line per run with what it found and cost, and any problems spelled out.

## Updating the Apps Script after a code change

1. Paste the new [`apps_script/Code.gs`](apps_script/Code.gs) over the old one and **Save**.
2. Run **setup** again. It keeps your data and secret, refreshes the timers, and adds any new columns
   and tabs (the October 22 update adds **Other postings**, **Sources**, and a new **Runs** layout; the old
   Runs history stays in a tab named *Runs (before ...)*; the October 25 update adds **Applied on**,
   **Furthest stage** and **Why not**, filled in for past applications from their emails; the October 28
   update adds **Pay** and **Closes**, also shown in alert emails).
3. **Deploy → Manage deployments →** ✏️ (edit) **→ Version: New version → Deploy.** The web-app URL
   stays the same, so the GitHub secrets don't change. Until you do this, the old code keeps handling uploads.

## Tuning

- **Search terms, locations, title filters, score thresholds:** [`config/search.yaml`](config/search.yaml).
  It also holds `min_quick` (Haiku's relevance estimate below which a job skips the Sonnet screen; 0 turns
  that off) and `skip_french_postings` (postings written mostly in French are dropped; bilingual ones stay).
- **Employers read directly:** [`config/employers.yaml`](config/employers.yaml). It explains how to add one.
  Sites added automatically are in `data/discovered.json` (the radar maintains it; delete an entry to drop one).
- **Resume source:** [`config/master_resume.md`](config/master_resume.md). Keep it in sync with your Claude Project copy.
- **Schedule:** the Sheet's Apps Script (`kickRadar` in `Code.gs`) starts a run hourly from 7am to 5pm and at 7pm Vancouver time on weekdays, and at 9am, 1pm and 5pm on weekends. The remote-in-Canada searches on LinkedIn and Indeed run every 3 hours (`remote_every_hours` in `config/search.yaml`). Edit the hours in `isSearchHour_`.

## Costs and limits

- **Claude API:** measured October 22–28: about $2.70 per weekday, roughly $60–70 a month, and over half
  of it went on re-caching the screening prompt (runs an hour apart outlived the 1-hour cache, and the
  first screens of each run all wrote it at once). The October 28 update fixes that, which should halve
  the bill without changing any score, and skips the Sonnet screen for jobs Haiku's quick check rates
  under 50: expect roughly $20–30 a month. The Haiku check is a fraction of a cent per posting; each
  Sonnet screen is about 1¢. Set a monthly limit in the Anthropic Console. The Sheet's Runs tab shows
  each run's estimated cost. `screen_effort: low` saves little (screen outputs are short); for more
  careful screening, set `high`.
- **Claude Pro:** resume and cover-letter tailoring happens in your Claude Project, so it uses your
  subscription, not API credit.
- **GitHub Actions:** stays within the 2,000 free minutes a month. Before each run, `kickRadar` adds up
  the minutes used in the last 31 days: above about 1,570 it keeps only the core hours (7, 9, 11am, 1, 3
  and 5pm on weekdays; 9am and 5pm on weekends), and at 1,850 it pauses and emails you until older runs
  drop out of the window. No spending limit or payment method needed.
- **LinkedIn** limits automated searching. Rate-limited searches are retried once after a pause; if
  the Runs tab often says *"LinkedIn rate-limited N searches"*, company sites and Indeed still run, and
  LinkedIn job alerts in its app are a good backup. Full postings are fetched two at a time; once LinkedIn
  refuses three in a row, the rest wait for the next run, and any posting it refused is asked for again
  by its link in later runs (*"N jobs waiting for their full posting"* on the Runs tab), so it isn't lost
  when no search lists it any more.
- **First run after an update that adds sources** screens every relevant posting on them once, so it
  costs a few dollars and takes longer. Later runs only look at what's new.

## To do

Planned improvements and things to check are in [docs/todo.md](docs/todo.md).

## Tests

`python -m pytest -q tests` checks the filters (target titles kept, junk dropped, locations, pay ranges,
French postings), how a job's postings are grouped and new places detected, the careers-site readers,
what counts as remote, source health, finding careers feeds from apply links, a whole run with fake
sources (including retries of postings LinkedIn refused and the quick-check cutoff), the prompt cache
and cost estimate, the posting guard and the LinkedIn/Indeed look-back window. `node --test tests/apps_script/code.test.js` checks the Sheet's Apps Script (rows,
links, alerts, Sources and Runs tabs, watchdog) against a stand-in for Google Sheets. Both run on GitHub
after every code or config change (Actions tab, "Tests"). If you edit `config/search.yaml` and the check
turns red, a rule you changed now drops a title that should be kept.

## Running it on your Mac (optional)

```bash
pip3 install -r requirements.txt
export ANTHROPIC_API_KEY=...   # and SHEET_WEBAPP_URL, SHARED_SECRET to upload
python3 -m radar.main --dry-run   # writes data/last_run.json, uploads nothing
```
