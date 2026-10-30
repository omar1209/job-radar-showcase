"""LinkedIn + Indeed via the open-source JobSpy library (no login, no account risk)."""
import logging
import random
import re
import threading
import time

from ..filters import pay_from_text, pay_range
from ..models import Job
from .common import get, html_to_text

log = logging.getLogger(__name__)
# Filled by linkedin() and the posting fetches: how often LinkedIn refused us this run, for the Runs tab.
LINKEDIN_STATS = {"throttled_queries": 0, "retried": 0, "still_throttled": 0, "pages_refused": 0, "pages_deferred": 0}
# LinkedIn refuses bursts of requests from GitHub's servers. Full postings are fetched two at a time, and
# after this many refusals in a row the rest wait for the next run (radar/main.py retries them by link).
PAGE_SLOTS = threading.Semaphore(2)
REFUSALS_BEFORE_PAUSE = 3
_refused_in_a_row = [0]
_lock = threading.Lock()


def new_run():
    """Clears this run's LinkedIn counters (main.run, before anything is read)."""
    LINKEDIN_STATS.update(dict.fromkeys(LINKEDIN_STATS, 0))
    _refused_in_a_row[0] = 0


def query_name(q):
    """Config queries are either "text" or {name: ..., q: ...}."""
    return (q["name"], q["q"]) if isinstance(q, dict) else (q[:40], q)


def _pay(r):
    """JobSpy's salary fields (Indeed, some LinkedIn cards) as "$55,000-$65,000 a year"."""
    nums = [v if isinstance(v, (int, float)) and v == v else None for v in (r.get("min_amount"), r.get("max_amount"))]
    interval = r.get("interval")
    return pay_range(*nums, interval if isinstance(interval, str) else "")


def _rows_to_jobs(df, source, label=None):
    out = []
    for r in df.to_dict("records"):
        def s(k):
            v = r.get(k)
            return "" if v is None or v != v else str(v)  # v != v catches NaN
        job = Job(source=source, company=s("company"), title=s("title"), location=s("location"),
                  url=s("job_url"), posted=s("date_posted")[:10], description=s("description"),
                  remote=s("is_remote").lower() == "true")
        job.label = label or source
        job.pay = _pay(r)
        # The employer's own posting, when the board shares it (Indeed does; LinkedIn hides it from
        # visitors who aren't signed in). Used to find employers' careers feeds (radar/discover.py).
        job.apply_url = s("job_url_direct")
        if source == "linkedin" and not job.description:
            job.fetch = _linkedin_detail(job)
        out.append(job)
    return out


class _CountThrottles(logging.Handler):
    """JobSpy logs LinkedIn rate-limit responses ("429") instead of raising; count them."""
    def __init__(self):
        super().__init__()
        self.hits = 0

    def emit(self, record):
        if "429" in record.getMessage():
            self.hits += 1


def linkedin(cfg, hours_old, remote_hours=None):
    """Local searches every run; the remote-in-Canada searches only when remote_hours is given
    (every few hours, reading back that far). LinkedIn ignores its remote filter for visitors who
    aren't signed in, so those searches ask for postings that mention remote work anywhere in Canada."""
    from jobspy import scrape_jobs
    li = cfg["linkedin"]
    runs = [("", hours_old, dict(location=li["location"], distance=li["distance_miles"],
                                 results_wanted=li["results_per_query"]))]
    if li.get("remote_canada") and remote_hours:
        runs.append((" (remote)", remote_hours, dict(location="Canada", results_wanted=li.get("remote_results_per_query", 25))))
    counter = _CountThrottles()
    jobspy_log = logging.getLogger("jobspy.linkedin")
    jobspy_log.addHandler(counter)
    out = []
    try:
        for q in li["queries"]:
            name, text = query_name(q)
            for suffix, hours, extra in runs:
                label = f"linkedin: {name}{suffix}"
                term = f"({text}) AND remote" if suffix else text
                found = {}
                for attempt in range(2):
                    counter.hits = 0
                    try:
                        df = scrape_jobs(site_name=["linkedin"], search_term=term, hours_old=hours,
                                         fetch_description=False, verbose=0, **extra)
                        for j in _rows_to_jobs(df, "linkedin", label):
                            found.setdefault(j.url, j)
                    except Exception as e:  # one failed query shouldn't sink the run
                        log.warning("linkedin query failed: %s", e)
                    if not counter.hits:
                        break
                    if attempt == 0:  # throttled: some results are missing - pause, then ask again
                        LINKEDIN_STATS["throttled_queries"] += 1
                        LINKEDIN_STATS["retried"] += 1
                        time.sleep(20 + random.uniform(0, 10))
                    else:
                        LINKEDIN_STATS["still_throttled"] += 1
                jobs = list(found.values())
                if suffix:
                    for j in jobs:
                        j.remote = True
                        if "remote" not in j.location.lower():
                            j.location += " (Remote)"
                        # Most of these are on-site jobs whose posting mentions remote work: confirmed
                        # from the full posting.
                        j.remote_unverified = True
                out += jobs
                time.sleep(2)
    finally:
        jobspy_log.removeHandler(counter)
    return out


# Remote only when the posting says so plainly. "Télétravail" (usually hybrid in Quebec), "virtual",
# "remote deposit" or "not a remote role" don't count, and with "hybrid" in the posting it takes one
# of the clearest phrases.
_CLEARLY_REMOTE = re.compile(
    r"\b(fully|100\s?%|completely|entirely|permanently)[- ]remote\b|\bremote[- ](first|only)\b"
    r"|\bremote\s+(position|role|opportunity|job)\b|\banywhere in canada\b|\bremote\s+(in|within|across)\s+canada\b"
    r"|\bremote\s*[(,-]\s*(canada|bc|british columbia)\b|\b(canada|bc|british columbia)\s*[(,-]\s*remote\b"
    r"|\blocation\s*:\s*remote\b|\b(100\s?%|entièrement|complètement)\s+(en\s+)?(télétravail|à distance)\b", re.I)
_REMOTE = re.compile(r"\bwork(ing)?\s+remotely\b|\bwork(ing)?[- ]from[- ]home\b|\bwfh\b|\bremote[- ](based|eligible)\b"
                     r"|\bhome[- ]based\b|\btelecommut\w*", re.I)
_NOT_REMOTE = re.compile(
    r"\b(not|isn't|no)\s+(a\s+)?(fully\s+)?remote\b|\bnon[- ]remote\b|\bremote work is not\b"
    r"|\bnot\s+(eligible|available|open)\s+(for|to)\s+remote\b", re.I)


def says_remote(title, text):
    if _NOT_REMOTE.search(f"{title}\n{text}"):
        return False
    if re.search(r"\bremote\b", title or "", re.I) or _CLEARLY_REMOTE.search(text or ""):
        return True
    return bool(_REMOTE.search(text or "")) and not re.search(r"\bhybrid\b", text or "", re.I)


def confirm_remote(job):
    """A result of LinkedIn's remote search that doesn't plainly say remote is an on-site job."""
    if not getattr(job, "remote_unverified", False):
        return
    job.remote_unverified = False
    if not says_remote(job.title, job.description):
        job.remote = False
        job.location = job.location.replace(" (Remote)", "")


def _criterion(page, name):
    """One line of a LinkedIn posting's criteria list ("Seniority level": "Entry level")."""
    m = re.search(rf'(?s)job-criteria-subheader">\s*{name}\s*</h3>\s*<span[^>]*>(.*?)</span>', page)
    return html_to_text(m.group(1)) if m else ""


def _linkedin_detail(job):
    def fetch():
        m = re.search(r"(\d{8,})", job.url)
        if not m:
            return
        with PAGE_SLOTS:
            if _refused_in_a_row[0] >= REFUSALS_BEFORE_PAUSE:  # LinkedIn is refusing us: not a real try
                job.fetch_deferred = True
                with _lock:
                    LINKEDIN_STATS["pages_deferred"] += 1
                return
            for wait in (0, 6):  # still refused: the job waits for the next run rather than holding this one up
                time.sleep(wait)
                try:
                    h = get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}", retries=0).text
                except Exception:
                    continue
                d = re.search(r'(?s)class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', h)
                if d:
                    job.description = html_to_text(d.group(1))
                    job.seniority = _criterion(h, "Seniority level")
                    pay = re.search(r'(?s)class="compensation__salary[^"]*"[^>]*>(.*?)</div>', h)
                    job.pay = getattr(job, "pay", "") or (pay_from_text(html_to_text(pay.group(1))) if pay else "")
                    confirm_remote(job)
                    _refused_in_a_row[0] = 0
                    return
        with _lock:
            _refused_in_a_row[0] += 1
            LINKEDIN_STATS["pages_refused"] += 1
    return fetch


def pending_info(job):
    """What a later run needs to fetch a LinkedIn posting again once no search lists it."""
    if job.source != "linkedin" or not re.search(r"\d{8,}", job.url or ""):
        return {}
    return {"p": {"u": job.url, "c": job.company, "t": job.title, "l": job.location, "d": job.posted,
                  "r": bool(job.remote), "ru": bool(getattr(job, "remote_unverified", False)),
                  "lb": getattr(job, "label", "") or "linkedin"}}


def revive(p):
    """The LinkedIn posting pending_info() described, ready to fetch its full text."""
    job = Job(source="linkedin", company=p["c"], title=p["t"], location=p["l"], url=p["u"], posted=p.get("d", ""),
              remote=p.get("r", False))
    job.label, job.task, job.apply_url, job.pay = p.get("lb") or "linkedin", "linkedin", "", ""
    if p.get("ru"):
        job.remote_unverified = True
    job.fetch = _linkedin_detail(job)
    return job


def indeed(cfg, hours_old, remote_hours=None):
    """Searches around Vancouver every run; remote-in-Canada searches only when remote_hours is given.
    Indeed tags remote jobs itself (hybrid ones left out), so those need no checking."""
    from jobspy import scrape_jobs
    ind = cfg["indeed"]
    runs = [(q, "", dict(location=ind["location"], distance=ind["distance_miles"], hours_old=hours_old,
                         results_wanted=ind["results_per_query"])) for q in ind["queries"]]
    if remote_hours:
        runs += [(q, " (remote)", dict(location="Canada", is_remote=True, hours_old=remote_hours,
                                       results_wanted=ind.get("remote_results_per_query", 50)))
                 for q in ind.get("remote_queries") or []]
    out = []
    for q, suffix, extra in runs:
        try:
            df = scrape_jobs(site_name=["indeed"], search_term=q, country_indeed="Canada", verbose=0, **extra)
            jobs = _rows_to_jobs(df, "indeed", f"indeed: {q}{suffix}")
        except Exception as e:
            log.warning("indeed query failed: %s", e)
            continue
        if suffix:
            jobs = [j for j in jobs if j.remote]
            for j in jobs:
                if "remote" not in j.location.lower():
                    j.location += " (Remote)"
        out += jobs
    return out
