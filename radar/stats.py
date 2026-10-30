"""Per-source numbers for the Sheet's Sources tab, and source health for the watchdog.

A source is a "label": one careers site ("workday:RBC") or one board search ("linkedin: research",
"indeed: analyst"). Health is tracked per collection task (a careers site, or all LinkedIn searches).
"""
from datetime import date

FIELDS = ("listed", "new", "screened", "matches", "only", "alerts", "branches")
ERROR_RUNS = 6          # failing this many runs in a row -> reported
SILENT_DAYS_BUSY = 2    # a site that usually lists 5+ postings, empty for this long -> reported
SILENT_DAYS_QUIET = 10  # a site that usually lists a few
NEVER_DAYS = 14         # never listed anything in this long
FORGET_DAYS = 7         # tasks not run for this long were removed from the config


def label(job):
    return getattr(job, "label", None) or job.source


class SourceStats:
    def __init__(self):
        self.rows = {}

    def _row(self, job):
        return self.rows.setdefault(label(job), {"label": label(job), "task": getattr(job, "task", job.source),
                                                 **dict.fromkeys(FIELDS, 0), "cost": 0.0})

    def listed(self, jobs):
        for j in jobs:
            self._row(j)["listed"] += 1

    def job(self, postings, screened=False, match=False, alert=False, cost=0.0):
        """One job new this run; every source that listed it shares the credit (and the cost)."""
        by_label = {label(p): p for p in postings}
        for p in by_label.values():
            r = self._row(p)
            r["new"] += 1
            r["screened"] += bool(screened)
            r["matches"] += bool(match)
            r["alerts"] += bool(alert)
            r["only"] += bool(match) and len(by_label) == 1
            r["cost"] += cost / len(by_label)

    def branch(self, posting):
        self._row(posting)["branches"] += 1

    def table(self):
        return [{**r, "cost": round(r["cost"], 4)} for r in sorted(self.rows.values(), key=lambda r: r["label"])]


def _days(since, today):
    return (date.fromisoformat(today) - date.fromisoformat(since)).days


def update_health(h, health, today):
    """h: per-task history (kept in seen.json); health: this run's {task: count or "ERROR: ..."}."""
    for task, v in health.items():
        r = h.setdefault(task, {"since": today})
        r["last"] = today
        if isinstance(v, int):
            r["ok"], r["e"] = today, 0
            r.pop("err", None)
            if v:
                r["nz"], r["n"] = today, v
        else:
            r["e"] = r.get("e", 0) + 1
            r["err"] = str(v)[:150]
    for task in [t for t, r in h.items() if _days(r.get("last", r["since"]), today) > FORGET_DAYS]:
        del h[task]
    return h


def problem(r, today):
    """Why a task needs attention, or None."""
    if r.get("e", 0):  # failing: reported once it keeps failing, not as "no postings"
        return f"failing for {r['e']} runs in a row: {r.get('err', '')}" if r["e"] >= ERROR_RUNS else None
    if r.get("nz"):
        quiet = _days(r["nz"], today)
        if quiet >= (SILENT_DAYS_BUSY if r.get("n", 0) >= 5 else SILENT_DAYS_QUIET):
            return f"no postings for {quiet} days (it listed {r['n']} before)"
    elif _days(r["since"], today) >= NEVER_DAYS:
        return f"no postings in {_days(r['since'], today)} days of checks"
    return None


def status(h, today):
    """{task: {"ok": bool, "note": str, "last_ok": date, "last_postings": date}} for the Sources tab."""
    out = {}
    for task, r in sorted(h.items()):
        why = problem(r, today)
        note = why or (f"error last run: {r['err']}" if r.get("e") else "OK")
        out[task] = {"ok": why is None, "note": note, "last_ok": r.get("ok", ""), "last_postings": r.get("nz", "")}
    return out


def stale(h, today):
    return [{"task": t, "why": w} for t, w in ((t, problem(r, today)) for t, r in sorted(h.items())) if w]
