"""Finds employers' own careers feeds through the apply links of job-board postings.

When an employer we don't read directly has a good match on LinkedIn or Indeed, and one of its
Indeed postings links to a careers system we can read (Workday, Greenhouse, iCIMS, ...), that feed
is checked and saved in data/discovered.json. From the next run on it's read like the employers in
config/employers.yaml: earlier than the boards, and including the postings they miss. Feeds that
later fail, or never list anything, are dropped again. Employers whose system we can't read are
listed in the Sheet's Sources tab instead.
"""
import json
import logging
import os
import re
from datetime import date

from .models import canon_company
from .sources.common import get, post

log = logging.getLogger(__name__)
PATH = os.path.join(os.path.dirname(__file__), "..", "data", "discovered.json")
MAX_FEEDS = 40
MAX_CHECKS = 5            # feeds tested per run
KEEP_UNCOVERED = 60
KEEP_COVERED_DAYS = 30
REMOVE_AFTER_ERRORS = 12  # runs in a row
REMOVE_IF_EMPTY_DAYS = 7

# Indeed and LinkedIn name the provincial government differently from its own job board.
ALWAYS_COVERED = {"bc public service", "province of british columbia", "government of british columbia",
                  "government of bc", "british columbia public service"}

PATTERNS = [  # (employers.yaml section, apply-link pattern, feed settings)
    ("workday", re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/job/"),
     lambda m: {"tenant": m[1], "wd": m[2], "site": m[3]}),
    ("workday", re.compile(r"https?://(wd\d+)\.myworkdaysite\.com/(?:[a-z]{2}-[A-Z]{2}/)?recruiting/([\w-]+)/([\w-]+)/job/"),
     lambda m: {"tenant": m[2], "wd": m[1], "site": m[3]}),
    ("greenhouse", re.compile(r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?(?:[^#\s]*&)?for=)?([\w-]+)"),
     lambda m: {"slug": m[1]}),
    ("lever", re.compile(r"https?://jobs\.lever\.co/([\w.-]+)/"), lambda m: {"slug": m[1]}),
    ("ashby", re.compile(r"https?://jobs\.ashbyhq\.com/([\w.%-]+)/"), lambda m: {"slug": m[1]}),
    ("smartrecruiters", re.compile(r"https?://(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)/"), lambda m: {"slug": m[1]}),
    ("icims", re.compile(r"https?://([\w-]+\.icims\.com)/jobs/\d+"), lambda m: {"host": m[1].lower()}),
    ("ukg", re.compile(r"https?://(recruiting2?\.ultipro\.(?:ca|com))/(\w+)/JobBoard/([0-9a-f-]{36})", re.I),
     lambda m: {"domain": m[1].lower(), "tenant": m[2], "board": m[3].lower()}),
    ("taleo_be", re.compile(r"https?://(\w+\.tbe\.taleo\.net)/(\w+)/ats/careers/v2/\w+\?(?:[^#\s]*&)?org=(\w+)&cws=(\d+)"),
     lambda m: {"host": m[1], "path": m[2], "org": m[3], "cws": int(m[4])}),
    ("oracle", re.compile(r"https?://([\w.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z]{2}(?:-[A-Z]{2})?/sites/(\w+)/"),
     lambda m: {"host": m[1], "site": m[2]}),
    # SuccessFactors career sites live on the employer's own domain: /job/<title-slug>/<id>/
    ("successfactors", re.compile(r"https?://([\w-]+(?:\.[\w-]+)+)/job/[^/?#\s]+/\d{6,}/?"), lambda m: {"domain": m[1].lower()}),
]
IDENTITY = {"workday": ("tenant", "site"), "greenhouse": ("slug",), "lever": ("slug",), "ashby": ("slug",),
            "smartrecruiters": ("slug",), "icims": ("host",), "ukg": ("tenant", "board"),
            "taleo_be": ("host", "org", "cws"), "oracle": ("host", "site"), "successfactors": ("domain",)}
_NOT_EMPLOYERS = re.compile(r"(^|\.)(indeed|linkedin|glassdoor|ziprecruiter|monster|talent|jobbank\.gc|workopolis|eluta|"
                            r"simplyhired|jooble|adzuna|careerbeacon|appcast|recruit\.net)\.", re.I)
HINTS = [("BambooHR", r"\.bamboohr\.com"), ("Workable", r"workable\.com"), ("Jobvite", r"jobvite\.com"),
         ("Taleo", r"taleo\.net/careersection"), ("SuccessFactors (classic)", r"successfactors\.(com|eu)"),
         ("ADP", r"adp\.com"), ("Dayforce", r"dayforcehcm\.com"), ("Breezy", r"breezy\.hr"),
         ("Recruitee", r"recruitee\.com"), ("Pinpoint", r"pinpointhq\.com"), ("Rippling", r"rippling\.com"),
         ("Teamtailor", r"teamtailor\.com"), ("JazzHR", r"applytojob\.com"), ("Paycor", r"paycor"),
         ("Njoyn", r"njoyn\.com"), ("Avature", r"avature\.net"), ("PeopleAdmin", r"peopleadmin"),
         ("Paylocity", r"paylocity\.com"), ("Bullhorn", r"bullhorn"), ("Collage", r"collage\.co")]
PREFIX = {"taleo_be": "taleo"}  # collection task names, as in main.collect


def parse(url):
    """(section, feed settings) for an apply link into a careers system we can read, else None."""
    if not url or _NOT_EMPLOYERS.search(url.split("/")[2] if "//" in url else url):
        return None
    for kind, pat, params in PATTERNS:
        m = pat.search(url)
        if m:
            return kind, params(m)
    return None


def system_hint(url):
    for name, pat in HINTS:
        if url and re.search(pat, url, re.I):
            return name
    return ""


def identity(kind, p):
    return (kind,) + tuple(str(p[k]).lower() for k in IDENTITY[kind])


SECTIONS = ("workday", "successfactors", "greenhouse", "lever", "ashby", "smartrecruiters", "oracle",
            "icims", "jibe", "ukg", "taleo_be")


def sites_of(emp, kind):
    sec = emp.get(kind)
    return (sec or {}).get("sites", []) if isinstance(sec, dict) else (sec or [])


def configured(emp):
    """Identities and employer names of every feed already read (config + discovered)."""
    ids, names = set(), set()
    for kind in SECTIONS:
        for s in sites_of(emp, kind):
            names.add(canon_company(s.get("company") or s["name"]))
            if kind in IDENTITY:
                ids.add(identity(kind, s))
    return ids, names


def check(kind, p):
    """One light request: does the feed answer the way that careers system does?"""
    try:
        if kind == "workday":
            d = post(f"https://{p['tenant']}.{p['wd']}.myworkdayjobs.com/wday/cxs/{p['tenant']}/{p['site']}/jobs",
                     json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}, retries=0).json()
            return "jobPostings" in d
        if kind == "greenhouse":
            return "jobs" in get(f"https://boards-api.greenhouse.io/v1/boards/{p['slug']}/jobs", retries=0).json()
        if kind == "lever":
            return isinstance(get(f"https://api.lever.co/v0/postings/{p['slug']}?mode=json&limit=1", retries=0).json(), list)
        if kind == "ashby":
            return "jobs" in get(f"https://api.ashbyhq.com/posting-api/job-board/{p['slug']}", retries=0).json()
        if kind == "smartrecruiters":
            return "content" in get(f"https://api.smartrecruiters.com/v1/companies/{p['slug']}/postings?limit=1",
                                    retries=0).json()
        if kind == "icims":
            return "iCIMS_JobCardItem" in get(f"https://{p['host']}/jobs/search?ss=1&in_iframe=1", retries=0).text
        if kind == "ukg":
            body = {"opportunitySearch": {"Top": 1, "Skip": 0, "QueryString": "", "OrderBy": [], "Filters": []},
                    "matchCriteria": {"PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [],
                                      "Skills": [], "hasNoLicenses": False, "SkippedSkills": []}}
            d = post(f"https://{p['domain']}/{p['tenant']}/JobBoard/{p['board']}/JobBoardView/LoadSearchResults",
                     json=body, retries=0).json()
            return "opportunities" in d
        if kind == "taleo_be":
            return "oracletaleocwsv2" in get(f"https://{p['host']}/{p['path']}/ats/careers/v2/searchResults"
                                             f"?org={p['org']}&cws={p['cws']}", retries=0).text
        if kind == "oracle":
            return "items" in get(f"https://{p['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
                                  f"?onlyData=true&finder=findReqs;siteNumber={p['site']},limit=1", retries=0).json()
        if kind == "successfactors":  # results in the page, or (newer sites) the module that fetches them
            page = get(f"https://{p['domain']}/search/?q=&locationsearch=BC", retries=0).text
            return "jobTitle-link" in page or "rmk-jobs-search" in page
    except Exception as e:
        log.info("feed check failed for %s %s: %s", kind, p, e)
    return False


def _days(since, today):
    return (date.fromisoformat(today) - date.fromisoformat(since)).days


class Discovered:
    def __init__(self, path=PATH):
        self.path = path
        try:
            with open(path) as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}
        for k in ("feeds", "uncovered", "covered"):
            self.data.setdefault(k, {})
        self.data.setdefault("removed", [])
        self.changed = False

    def count(self):
        return sum(len(v) for v in self.data["feeds"].values())

    def merge_into(self, emp):
        """employers.yaml plus the auto-added feeds."""
        emp = dict(emp)
        for kind, sites in self.data["feeds"].items():
            if kind in ("workday", "successfactors"):
                sec = dict(emp.get(kind) or {})
                sec["sites"] = list(sec.get("sites", [])) + sites
                emp[kind] = sec
            else:
                emp[kind] = list(emp.get(kind) or []) + sites
        return emp

    def tasks(self):
        """Collection task name -> (section, feed) for each auto-added feed."""
        return {f"{PREFIX.get(k, k)}:{s['name']}": (k, s) for k, sites in self.data["feeds"].items() for s in sites}

    def covered(self, emp_names, company_postings, today):
        """Employers read directly: configured, or listed by a careers site in the last 30 days."""
        seen = self.data["covered"]
        for j in company_postings:
            c = canon_company(j.company)
            if c and seen.get(c) != today:
                seen[c] = today
                self.changed = True
        for c in [c for c, d in seen.items() if _days(d, today) > KEEP_COVERED_DAYS]:
            del seen[c]
            self.changed = True
        return set(emp_names) | set(seen) | ALWAYS_COVERED

    def note_matches(self, matches, covered, today):
        """matches: [(posting, its job's other board postings)] for good matches seen only on job
        boards. Employers we don't read directly are remembered as candidates."""
        for job, posts in matches:
            c = canon_company(job.company)
            if not c or c in covered:
                continue
            u = self.data["uncovered"].setdefault(c, {"name": job.company, "matches": 0, "first": today})
            u.update(matches=u["matches"] + 1, last=today, title=job.title[:80])
            hint = next((h for h in (system_hint(getattr(p, "apply_url", "")) for p in posts) if h), "")
            if hint:
                u["system"] = hint
            self.changed = True
        keep = sorted(self.data["uncovered"].items(), key=lambda kv: (kv[1].get("last", ""), kv[1]["matches"]), reverse=True)
        self.data["uncovered"] = dict(keep[:KEEP_UNCOVERED])

    def discover(self, board_postings, known_ids, today):
        """Adds feeds for remembered employers whose Indeed postings link into a system we read."""
        added, tried, checks = [], set(), 0
        blocked = {tuple(r["id"]) for r in self.data["removed"] if r.get("id")}
        for p in board_postings:
            c = canon_company(p.company)
            u = self.data["uncovered"].get(c)
            url = getattr(p, "apply_url", "") or ""
            if not u or not url:
                continue
            found = parse(url)
            if not found:
                if system_hint(url) and not u.get("system"):
                    u["system"] = system_hint(url)
                    self.changed = True
                continue
            kind, params = found
            ident = identity(kind, params)
            if ident in known_ids or ident in tried or ident in blocked:
                continue
            if checks == MAX_CHECKS or self.count() >= MAX_FEEDS:
                break
            tried.add(ident)
            checks += 1
            self.changed = True
            if not check(kind, params):
                u["system"] = f"{kind} (its feed didn't answer)"
                continue
            del self.data["uncovered"][c]
            entry = {"name": f"{u['name']} (auto)", "company": u["name"], **params, "added": today,
                     "why": f"{u['matches']} good match{'es' if u['matches'] > 1 else ''} on LinkedIn/Indeed"}
            self.data["feeds"].setdefault(kind, []).append(entry)
            known_ids.add(ident)
            added.append(entry)
            log.info("auto-added careers feed: %s (%s)", entry["name"], kind)
        return added

    def prune(self, health_hist, today):
        """Drops auto-added feeds that keep failing or never list anything (and won't re-add them)."""
        dropped = []
        for task, (kind, s) in self.tasks().items():
            r = health_hist.get(task)
            if not r:
                continue
            why = None
            if r.get("e", 0) >= REMOVE_AFTER_ERRORS:
                why = f"kept failing: {r.get('err', '')[:80]}"
            elif not r.get("nz") and _days(r["since"], today) >= REMOVE_IF_EMPTY_DAYS:
                why = f"listed no postings in {REMOVE_IF_EMPTY_DAYS} days"
            if why:
                self.data["feeds"][kind].remove(s)
                self.data["removed"].append({"name": s.get("company", s["name"]), "system": kind, "when": today,
                                             "why": why, "id": list(identity(kind, s))})
                dropped.append(task)
                self.changed = True
        self.data["feeds"] = {k: v for k, v in self.data["feeds"].items() if v}
        self.data["removed"] = self.data["removed"][-20:]
        return dropped

    def summary(self):
        """For the Sheet's Sources tab."""
        feeds = [{"name": s.get("company", s["name"]), "system": k, "added": s.get("added", ""), "why": s.get("why", ""),
                  "task": f"{PREFIX.get(k, k)}:{s['name']}"} for k, sites in self.data["feeds"].items() for s in sites]
        unc = sorted(self.data["uncovered"].values(), key=lambda u: (u["matches"], u.get("last", "")), reverse=True)
        return {"feeds": feeds, "uncovered": unc[:25],
                "removed": [{k: v for k, v in r.items() if k != "id"} for r in self.data["removed"][-10:]]}

    def save(self):
        if not self.changed:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
