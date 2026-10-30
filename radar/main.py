"""One run: collect -> group postings by job -> filter -> fetch details -> score -> send to Sheet.

    python -m radar.main              # normal run
    python -m radar.main --dry-run    # no Sheet upload, writes data/last_run.json
    python -m radar.main --no-boards  # skip LinkedIn/Indeed (fast test)
"""
import argparse
import json
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import anthropic
import yaml

from . import discover, guard, rollup, sink
from . import stats as srcstats
from .filters import Filters, mostly_french, pay_from_text
from .scoring import Scorer
from .sources import ats, boards, more_ats, volunteer
from .state import Seen

log = logging.getLogger("radar")
ROOT = os.path.join(os.path.dirname(__file__), "..")
MAX_DETAIL_TRIES = 3
# LinkedIn/Indeed look back to the last successful run (+ margin), so overnight and
# weekend gaps between runs never leave postings unseen.
WINDOW_MARGIN_H, WINDOW_MIN_H, WINDOW_MAX_H = 2, 3, 48
MAX_PLACE_FETCHES = 2  # per tracked job: new "3 Locations" postings fetched to see where they are
NOT_TASKS = ("claude_api", "sheet_script")  # health entries that aren't sources
# LinkedIn postings whose full text couldn't be fetched are asked for again by link in later runs (no
# search lists them once they're a few hours old): this many per run, freshest first, for this many days.
MAX_RETRIES, RETRY_DAYS = 30, 14


def load(name):
    with open(os.path.join(ROOT, "config", name)) as f:
        return yaml.safe_load(f)


def board_window(seen, now=None, key="__boards__"):
    """Hours of LinkedIn/Indeed history to read: since the last good board run, plus a margin."""
    now = now or datetime.now(timezone.utc)
    last = seen.data.get(key, {}).get("t")
    if not last:
        return 24
    hours = (now - datetime.fromisoformat(last)).total_seconds() / 3600
    return max(WINDOW_MIN_H, min(WINDOW_MAX_H, math.ceil(hours) + WINDOW_MARGIN_H))


def due(seen, key, every_hours, now=None):
    """For sources read every few hours: is it time? (15 minutes early counts.)"""
    last = seen.data.get(key, {}).get("t")
    if not last:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - datetime.fromisoformat(last)).total_seconds() / 3600 >= every_hours - 0.25


def collect(cfg, emp, use_boards, with_volunteer=False, window=6, with_gc=False, remote_window=None):
    tasks = {}
    terms = (emp.get("workday") or {}).get("search_terms", ["British Columbia", "Vancouver", "Remote Canada"])
    for s in discover.sites_of(emp, "workday"):
        tasks[f"workday:{s['name']}"] = lambda s=s: ats.workday(s, terms)
    places = (emp.get("successfactors") or {}).get("location_terms", ["BC", "Remote"])
    for s in discover.sites_of(emp, "successfactors"):
        tasks[f"successfactors:{s['name']}"] = lambda s=s: ats.successfactors(s, s.get("location_terms") or places)
    for kind in ("greenhouse", "lever", "ashby", "smartrecruiters", "oracle"):
        for s in emp.get(kind) or []:
            tasks[f"{kind}:{s['name']}"] = lambda s=s, kind=kind: getattr(ats, kind)(s)
    for kind in ("icims", "jibe", "ukg", "taleo_be"):
        for s in emp.get(kind) or []:
            tasks[f"{discover.PREFIX.get(kind, kind)}:{s['name']}"] = lambda s=s, kind=kind: getattr(more_ats, kind)(s)
    if emp.get("bc_public_service"):
        tasks["bcps"] = ats.bc_public_service
    if emp.get("translink"):
        tasks["translink"] = more_ats.translink
    if with_gc:
        tasks["gcjobs"] = more_ats.gcjobs
    if use_boards:
        tasks["linkedin"] = lambda: boards.linkedin(cfg, window, remote_window)
        tasks["indeed"] = lambda: boards.indeed(cfg, window, remote_window)
    if with_volunteer:
        tasks["volunteerconnector"] = volunteer.volunteer_connector
        if use_boards:
            tasks["indeed:volunteer"] = lambda: volunteer.indeed_volunteer(cfg, 30)

    def timed(name, fn):
        t = time.time()
        try:
            return fn()
        finally:
            log.info("source %s took %.0fs", name, time.time() - t)

    jobs, health = [], {}
    with ThreadPoolExecutor(10) as ex:
        futs = {ex.submit(timed, name, fn): name for name, fn in tasks.items()}
        for f in as_completed(futs):
            name = futs[f]
            try:
                got = f.result()
            except Exception as e:
                health[name] = f"ERROR: {str(e)[:120]}"
                log.warning("%s failed: %s", name, e)
                continue
            for j in got:
                j.task = name
                j.label = getattr(j, "label", None) or name
            jobs += got
            health[name] = len(got)
    return jobs, health


def pending_retries(seen, listed, today):
    """LinkedIn postings parked as pending that no search listed this run, rebuilt to fetch again."""
    cutoff = (date.fromisoformat(today) - timedelta(days=RETRY_DAYS)).isoformat()
    waiting = [v["p"] for k, v in seen.data.items() if v.get("s", "").startswith("pending:") and "p" in v
               and k not in listed and (v["p"].get("d") or today) >= cutoff]
    waiting.sort(key=lambda p: p.get("d") or "", reverse=True)
    return [boards.revive(p) for p in waiting[:MAX_RETRIES]]


def cached_map(fn, items, prompt, workers):
    """fn over items, results in order. One item per prompt goes first, on its own: calls sent together
    can't read a prompt another call is still caching, so each of them would pay to cache it."""
    firsts = {}
    for i, it in enumerate(items):
        firsts.setdefault(prompt(it), i)
    first = list(firsts.values())
    rest = [i for i in range(len(items)) if i not in set(first)]
    out = [None] * len(items)
    with ThreadPoolExecutor(workers) as ex:
        for batch in (first, rest):
            for i, res in zip(batch, ex.map(lambda i: fn(items[i]), batch)):
                out[i] = res
    return out


SCREEN_LOG_DAYS = 14


def log_screens(groups, rows, today, path=None):
    """One line per job scored: the quick check's estimate beside the careful screen's verdict.
    Used to tune the quick check so fewer jobs need the expensive screen (see docs/todo.md)."""
    path = path or os.path.join(ROOT, "data", "screen_log.jsonl")
    cutoff = (date.fromisoformat(today) - timedelta(days=SCREEN_LOG_DAYS)).isoformat()
    try:
        with open(path) as f:
            kept = [l for l in f if l.strip() and json.loads(l)["d"] >= cutoff]
    except FileNotFoundError:
        kept = []
    for g in groups:
        j, gate, t, r = g.job, getattr(g.job, "gate", {}), g.job.triage or {}, rows[g.key]
        lvl = getattr(j, "seniority", "")  # LinkedIn's "Seniority level", to see if it could drop jobs for free
        kept.append(json.dumps({"d": today, "src": rollup.kind(j), "title": j.title[:60], "company": j.company[:30],
                                "in_scope": gate.get("in_scope"), "family": gate.get("family"),
                                "quick": gate.get("likely_relevance"), "relevance": t.get("relevance"),
                                "attain": t.get("attainability"), "score": r["score"], "verdict": r["verdict"],
                                "tab": r["sheet"], **({"lvl": lvl} if lvl else {})}, separators=(",", ":")) + "\n")
    with open(path, "w") as f:
        f.writelines(kept)


def run(args):
    cfg, emp = load("search.yaml"), load("employers.yaml")
    sc = cfg["scoring"]
    seen = Seen()
    filters = Filters(cfg)
    found = discover.Discovered()
    emp = found.merge_into(emp)
    t0 = time.time()
    started = datetime.now(timezone.utc)
    today = date.today().isoformat()

    # Filters changed since jobs were dropped? Then look at those jobs again (re-filtering is free).
    if seen.data.get("__filters__", {}).get("s") != filters.fingerprint:
        dropped = [k for k, v in seen.data.items() if v["s"].startswith("f:")]
        for k in dropped:
            del seen.data[k]
        log.info("filters changed: re-checking %d previously dropped jobs", len(dropped))
    seen.add("__filters__", filters.fingerprint)

    # Volunteer roles: once a day (first run of the day), or on demand. GC Jobs: every few hours.
    with_vol = cfg.get("volunteer", {}).get("enabled", True) and (
        args.volunteer or seen.data.get("__volunteer__", {}).get("d") != today)
    gc = emp.get("gcjobs")
    with_gc = bool(gc) and (args.gcjobs or due(seen, "__gcjobs__", (gc if isinstance(gc, dict) else {}).get("every_hours", 3)))
    window = board_window(seen)
    # Remote-in-Canada searches (LinkedIn and Indeed): every few hours, reading back to their last good read.
    remote_due = due(seen, "__remote__", cfg["linkedin"].get("remote_every_hours", 3))
    remote_window = board_window(seen, key="__remote__") if remote_due else None
    log.info("job boards: reading the last %dh%s%s", window, f"; remote searches: last {remote_window}h" if remote_due else "",
             "; GC Jobs due" if with_gc else "")
    boards.new_run()
    raw, health = collect(cfg, emp, not args.no_boards, with_vol, window, with_gc, remote_window)
    sheet_version = sink.SCRIPT_VERSION if args.dry_run else sink.script_version()
    if sheet_version < sink.SCRIPT_VERSION:
        health["sheet_script"] = ("ERROR: the Sheet's Apps Script is out of date - paste apps_script/Code.gs, "
                                  "run setup, then Deploy > Manage deployments > New version")
    retry = pending_retries(seen, {j.key for j in raw}, today)

    # One job, possibly several postings (branches, or copies on LinkedIn/Indeed).
    groups = rollup.group(raw + retry)
    st = {"listed": len(raw), "unique": len(groups), "already_seen": 0, "filtered": 0, "board_window_h": window,
          "retried": len(retry), "pending": 0}
    todo, tracked = [], []  # new jobs; jobs already in the Sheet that have postings we haven't examined
    for key, posts in groups.items():
        prev = seen.data.get(key)
        old = next((p.legacy_key for p in posts if p.legacy_key != key and p.legacy_key in seen.data), None)
        if prev is None and old:  # tracked under the key its employer's name gave it before Oct 22
            prev = seen.data[key] = {**seen.data[old], "k": seen.data[old].get("k", old)}
        status = prev["s"] if prev else ""
        if status == "done":
            st["already_seen"] += 1
            if "u" not in prev:  # tracked before postings were remembered: start now, flag nothing
                prev.update(rollup.first_memory(posts, filters))
            else:
                fresh = rollup.in_area(rollup.unseen(posts, prev), filters)
                if fresh:
                    tracked.append((key, fresh))
            continue
        if status in ("f:title", "f:language"):
            st["already_seen"] += 1
            continue
        if status == "f:location":  # seen only out of the area so far: any new posting in it?
            cands = rollup.in_area(rollup.unseen(posts, prev), filters)
            if not cands:
                st["already_seen"] += 1
                continue
        else:  # new (or retrying one whose full posting couldn't be fetched)
            if not rollup.is_vol(posts[0]) and not filters.title_ok(posts[0].title):
                seen.add(key, "f:title")
                st["filtered"] += 1
                continue
            cands = rollup.in_area(posts, filters)
            if not cands:
                seen.add(key, "f:location")
                st["filtered"] += 1
                continue
        todo.append(rollup.Group(key, posts, cands, prev if status == "f:location" else None))
    log.info("listed=%d jobs=%d seen=%d filtered=%d new=%d tracked-with-new-postings=%d", len(raw), len(groups),
             st["already_seen"], st["filtered"], len(todo), len(tracked))

    # Full postings, only for the survivors.
    def fetch(j):
        try:
            j.fetch()
        except Exception as e:
            log.info("detail failed %s: %s", j.url, e)

    def place(item):
        """Unseen in-area postings of a tracked job -> (key, in the area, out of the area once fetched)."""
        key, fresh = item
        ok, out, fetches = [], [], 0
        for p in fresh:
            if rollup.unconfirmed(p):  # an unchecked LinkedIn "remote" copy: not worth a fetch
                out.append(p)
                continue
            if p.location_deferred and hasattr(p, "fetch"):
                if fetches == MAX_PLACE_FETCHES:
                    continue  # looked at next run
                fetches += 1
                fetch(p)
                if rollup.out_of_area_once_fetched(p, filters):
                    out.append(p)
                    continue
            ok.append(p)
        return key, ok, out

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda g: g.resolve(fetch, filters), todo))
        placed = list(ex.map(place, tracked))
    kept = []
    for g in todo:
        if g.job is None:  # every posting turned out to be out of the area
            seen.add(g.key, "f:location", **g.memory())
            st["filtered"] += 1
        else:
            kept.append(g)
    for g in kept:
        g.job.description, g.job.guard_removed = guard.clean_text(g.job.description)
        if not rollup.is_vol(g.job):
            g.job.pay = getattr(g.job, "pay", "") or pay_from_text(g.job.description)
    st["guard_flagged"] = sum(1 for g in kept if g.job.guard_removed)
    if cfg.get("skip_french_postings", True):  # needs French, which the screen counts as a blocker anyway
        french = {id(g) for g in kept if mostly_french(g.job.description)}
        for g in kept:
            if id(g) in french:
                seen.add(g.key, "f:language")
        kept = [g for g in kept if id(g) not in french]
        st["filtered"] += len(french)
        st["french"] = len(french)
    ready = []
    for g in kept:
        if g.job.description:
            ready.append(g)
            continue
        status = seen.data.get(g.key, {}).get("s", "")
        tries = int(status.split(":")[1]) if status.startswith("pending:") else 0
        if not getattr(g.job, "fetch_deferred", False):  # LinkedIn was refusing pages this run: not a real try
            tries += 1
        if tries < MAX_DETAIL_TRIES:
            seen.add(g.key, f"pending:{tries}", **boards.pending_info(g.job))  # tried again next run
            st["pending"] += 1
        else:
            g.job.description = "(Full description unavailable - judge from title, company and location only.)"
            ready.append(g)
    ready.sort(key=lambda g: g.job.posted or "0000", reverse=True)  # freshest first
    overflow = ready[sc["max_triage_per_run"]:]
    for g in overflow:
        if "p" not in seen.data.get(g.key, {}):  # a LinkedIn posting waiting for a retry keeps its place
            seen.data.pop(g.key, None)  # overflow: picked up next run
    vol_overflow = any(rollup.is_vol(g.job) for g in overflow)  # re-read volunteer feed next run
    ready = ready[: sc["max_triage_per_run"]]
    st["scored"] = len(ready)

    scorer = Scorer(cfg)
    fatal = []

    def call(stage, j):
        """Runs one Claude stage; stops everything on auth/billing errors."""
        if fatal:
            return None
        try:
            res = getattr(scorer, stage)(j)
            j.cost = getattr(j, "cost", 0.0) + res.pop("_cost", 0.0)
            return res
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            fatal.append(e)
        except anthropic.BadRequestError as e:
            if "credit" in str(e).lower():
                fatal.append(e)
            log.warning("%s failed for %s: %s", stage, j.title, e)
        except Exception as e:
            log.warning("%s failed for %s: %s", stage, j.title, e)
        return None

    # Stage 1: is it even in a target career track?
    gate_of = lambda g: "vol_gate" if rollup.is_vol(g.job) else "gate"
    gates = cached_map(lambda g: call(gate_of(g), g.job), ready, gate_of, 8)
    # In scope, but the quick check's relevance estimate is below the cutoff: the careful screen almost never
    # finds a match there (docs/todo.md). A sample is screened anyway, so the screen log keeps checking that.
    min_quick, audit_pct = sc.get("min_quick", 0), sc.get("quick_audit_pct", 0)
    in_scope, off_track, unlikely = [], [], []
    for g, res in zip(ready, gates):
        if res is None:
            continue
        g.job.gate = res
        if not res["in_scope"]:
            off_track.append(g)
        elif res.get("likely_relevance", 100) < min_quick and int(g.key[:4], 16) % 100 >= audit_pct:
            unlikely.append(g)
        else:
            in_scope.append(g)
    st["off_track"], st["quick_skipped"] = len(off_track), len(unlikely)

    # Stage 2: careful screen of everything in scope.
    screen_of = lambda g: "vol_screen" if rollup.is_vol(g.job) else "screen"
    screens = cached_map(lambda g: call(screen_of(g), g.job), in_scope, screen_of, 6)
    screened = []
    for g, res in zip(in_scope, screens):
        if res is not None:
            g.job.triage = res
            screened.append(g)
    answered = off_track + unlikely + screened  # everything that got a final answer
    st["screened"] = len(screened)
    st["claude_failed"] = len(ready) - len(answered)
    if fatal:
        log.error("Claude API unusable, stopping scoring: %s", fatal[0])
        health["claude_api"] = f"ERROR: {str(fatal[0])[:150]}"

    def notes(j):
        if not getattr(j, "guard_removed", None):
            return ""
        return ("Posting contained text aimed at AI tools (removed before scoring) - the employer may be "
                "screening for AI-written applications: " + " | ".join(r[:160] for r in j.guard_removed[:3]))

    def row(g, **fields):
        j = g.job
        return {"key": g.key, "company": j.company, "title": j.title, "location": j.location, "url": j.url,
                "posted": j.posted, "closes": getattr(j, "closing", ""), "pay": getattr(j, "pay", ""),
                "source": rollup.kind(j), "notes": notes(j), "postings": [rollup.link(p, today) for p in g.others()],
                **fields}

    def facts(j):
        """Pay and closing date lines for the Posting text cell, when the posting has them."""
        return "".join(f"{name}: {v}\n" for name, v in (("Pay", getattr(j, "pay", "")), ("Closes", getattr(j, "closing", ""))) if v)

    rows = []
    for g in off_track + unlikely:
        vol, gate = rollup.is_vol(g.job), g.job.gate
        why = ("Low career value: " if vol else "Off-track: " if not gate["in_scope"]
               else f"Unlikely fit (quick check {gate.get('likely_relevance')}/100, not screened): ")
        rows.append(row(g, score=0, verdict="Avoid", family="VOL" if vol else gate.get("family", ""),
                        reason=why + gate.get("reason", ""), blockers="", years="", keywords="", missing="",
                        sheet="Screened out", alert=False, posting_text=""))
    vol_alert = cfg.get("volunteer", {}).get("alert_min_score", 75)
    for g in screened:
        j, t = g.job, g.job.triage
        if rollup.is_vol(j):
            keep = t["verdict"] != "Skip"
            rows.append(row(
                g, score=t["career_value"], verdict=t["verdict"], family="VOL",
                reason=f"{t['reason']} Time: {t['time_commitment']}. Resume line: {t['resume_line']}",
                blockers="", years=t["time_commitment"], keywords=", ".join(t.get("skills_built", [])),
                missing=", ".join(t.get("helps_with", [])), sheet="Volunteer" if keep else "Screened out",
                alert=keep and t["career_value"] >= vol_alert, kind="volunteer",
                posting_text=(f"VOLUNTEER ROLE\nOrganization: {j.company}\nRole: {j.title}\nLocation: {j.location}\n"
                              f"Link: {j.url}\n{facts(j)}Job Radar: career value {t['career_value']}/100, {t['verdict']}. "
                              f"{t['reason']}\n\n" + j.description)[:40000] if keep else ""))
            continue
        on_jobs = t["verdict"] != "Avoid" and t["fit_score"] >= sc["sheet_min_score"]
        rows.append(row(
            g, score=t["fit_score"], verdict=t["verdict"], family=t.get("job_family", ""),
            reason=f"{t['reason']} (relevance {t['relevance']}, attainability {t['attainability']})",
            blockers="; ".join(t.get("hard_blockers", [])), years=t.get("years_required", ""),
            keywords=", ".join(t.get("key_requirements", [])), missing=", ".join(t.get("unsupported", [])),
            sheet="Jobs" if on_jobs else "Screened out", alert=on_jobs and t["fit_score"] >= sc["alert_min_score"],
            # One cell to paste into the Claude Project.
            posting_text=(f"Company: {j.company}\nTitle: {j.title}\nLocation: {j.location}\nLink: {j.url}\n{facts(j)}"
                          f"Job Radar screening: {t['fit_score']}/100, {t['verdict']} (relevance {t['relevance']}, "
                          f"attainability {t['attainability']}). {t['reason']}\n"
                          f"Job family: {t.get('job_family', '')}\n"
                          f"Key requirements: {', '.join(t.get('key_requirements', []))}\n"
                          f"Not on resume yet: {', '.join(t.get('unsupported', [])) or 'none'}\n"
                          f"Experience asked: {t.get('years_required', '')}\n\n"
                          "--- POSTING ---\n" + j.description)[:40000] if on_jobs else ""))
    rows.sort(key=lambda r: -r["score"])

    # Tracked jobs posted again: new places are flagged on their row; new company links added quietly.
    sstats = srcstats.SourceStats()
    sstats.listed(raw)
    updates, remember = [], {}
    for key, ok, out in placed:
        prev = seen.data[key]
        changes = rollup.branch_changes(prev, ok, filters)
        if changes:  # "k": the key its Sheet row has, if it was tracked under an older one
            updates.append({"key": prev.get("k", key), "postings": [rollup.link(p, today, new) for p, new in changes]})
            for p, new in changes:
                if new:
                    sstats.branch(p)
        remember[key] = {"u": rollup.merge_ids(prev.get("u", []), [rollup.pid(p) for p in ok + out]),
                         "l": sorted(set(prev.get("l", [])) | {i for p in ok for i in rollup.remembered(p, filters)})[:rollup.MAX_IDS]}
    st["updated_jobs"] = len(updates)
    st["new_places"] = sum(1 for u in updates for p in u["postings"] if p["new"])
    # Rows that reached the Sheet while its script was too old to keep their other postings: send them now.
    backlog = [k for k, v in seen.data.items() if v.get("o")] if sheet_version >= sink.SCRIPT_VERSION else []
    updates += [{"key": seen.data[k].get("k", k), "postings": seen.data[k]["o"]} for k in backlog]

    by_key = {r["key"]: r for r in rows}
    matched = []
    for g in answered:
        r = by_key[g.key]
        match = r["sheet"] in ("Jobs", "Volunteer")
        sstats.job(g.passing, screened=bool(g.job.triage), match=match, alert=r["alert"], cost=getattr(g.job, "cost", 0.0))
        if match and not rollup.is_vol(g.job) and all(rollup.is_board(p) for p in g.passing):
            matched.append((g.job, g.passing))
    st["matches"] = sum(1 for r in rows if r["sheet"] in ("Jobs", "Volunteer"))
    st["alerts"] = sum(1 for r in rows if r["alert"])

    # Source health, and careers feeds found through job-board apply links.
    hist = seen.meta("__health__").setdefault("h", {})
    srcstats.update_health(hist, {k: v for k, v in health.items() if k not in NOT_TASKS}, today)
    for task in found.prune(hist, today):
        hist.pop(task, None)
    known_ids, names = discover.configured(emp)
    covered = found.covered(names, [j for j in raw if not rollup.is_board(j) and not rollup.is_vol(j)], today)
    found.note_matches(matched, covered, today)
    st["feeds_added"] = len(found.discover([j for j in raw if rollup.is_board(j) and getattr(j, "apply_url", "")],
                                           known_ids, today))

    cost = scorer.cost()
    st.update(seconds=round(time.time() - t0), est_cost_usd=round(cost, 3), tokens=scorer.usage)
    run_info = {"stats": st, "health": health, "sources": sstats.table(), "source_status": srcstats.status(hist, today),
                "stale": srcstats.stale(hist, today), "discovery": found.summary(), "linkedin": dict(boards.LINKEDIN_STATS),
                "alert_min_score": sc["alert_min_score"], "vol_alert_min_score": vol_alert}
    log.info("run: %s", json.dumps(st))
    payload = {"run": run_info, "jobs": rows, "updates": updates}
    with open(os.path.join(ROOT, "data", "last_run.json"), "w") as f:
        json.dump(payload, f, indent=1)

    keep_state = not args.dry_run or args.save_state
    try:
        sent = args.dry_run or sink.send(payload)
        # Only remember scored jobs once they safely reached the Sheet.
        if sent:
            for g in answered:
                mem = g.memory()
                if sheet_version < sink.SCRIPT_VERSION and g.others():  # the old script drops these: kept to resend
                    mem["o"] = [rollup.link(p, today, False) for p in g.others()]
                seen.add(g.key, "done", **mem)
            if sheet_version >= sink.SCRIPT_VERSION:  # an older Sheet script ignores updates: resend later
                for key, mem in remember.items():
                    seen.add(key, "done", **mem)
                for k in backlog:
                    seen.data[k].pop("o", None)
            if with_vol and not fatal and not vol_overflow and isinstance(health.get("volunteerconnector"), int):
                seen.add("__volunteer__", "meta")
            # Next run's LinkedIn/Indeed window starts here - only if both boards actually worked.
            if not args.no_boards and not fatal and all(isinstance(health.get(b), int) for b in ("linkedin", "indeed")):
                seen.meta("__boards__")["t"] = started.isoformat()
                if remote_due:
                    seen.meta("__remote__")["t"] = started.isoformat()
            if with_gc and isinstance(health.get("gcjobs"), int):
                seen.meta("__gcjobs__")["t"] = started.isoformat()
            if keep_state:
                found.save()
                log_screens(answered, by_key, today)
    finally:
        if keep_state:
            seen.save()
    if fatal:
        raise SystemExit("Claude API error - see the Runs tab / log above")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-boards", action="store_true")
    p.add_argument("--save-state", action="store_true", help="persist seen.json even in a dry run")
    p.add_argument("--volunteer", action="store_true", help="include volunteer roles even if already read today")
    p.add_argument("--gcjobs", action="store_true", help="read GC Jobs even if it was read recently")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("JobSpy").setLevel(logging.ERROR)
    run(p.parse_args())


if __name__ == "__main__":
    main()
