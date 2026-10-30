"""Readers for careers systems added in the coverage pass: iCIMS, Jibe (iCIMS's newer
career sites), UKG, Taleo Business Edition, GC Jobs (federal public service) and TransLink.

Like ats.py, listing calls are cheap and the full description is fetched later
(job.fetch) only for postings that pass the free filters.
"""
import html
import json
import re
import threading
import time
from datetime import date, datetime

import requests

from ..models import Job
from .common import UA, company_of, get, html_to_text, post

_BC = {"province": "BC"}


# ------------------------------------------------------------------- iCIMS
def icims(site, pages=2):
    """Classic iCIMS portals (careers-xxx.icims.com): 50 newest postings per page."""
    host, name = site["host"], site["name"]
    province = site.get("province", "")
    out, seen = [], set()
    for page in range(pages):
        h = get(f"https://{host}/jobs/search?ss=1&in_iframe=1&pr={page}").text
        cards = re.findall(r'(?s)<li class="iCIMS_JobCardItem">(.*?)</li>', h)
        for c in cards:
            m = re.search(r'(?s)href="(https://[^"]+/jobs/(\d+)/[^"]+/job)[^"]*"[^>]*>.*?<h3[^>]*>\s*(.*?)\s*</h3>', c)
            if not m or m.group(2) in seen:
                continue
            seen.add(m.group(2))
            city = re.search(r'(?s)field-label">City</span>\s*<span[^>]*>\s*([^<]+)', c)
            loc = (city.group(1).strip() if city else "")
            job = Job(source=f"icims:{name}", company=company_of(site), title=html_to_text(m.group(3)),
                      location=", ".join(x for x in (loc, province) if x), url=m.group(1))
            job.fetch = _icims_detail(job)
            out.append(job)
        if len(cards) < 50:
            break
    return out


def _icims_detail(job):
    def fetch():
        h = get(job.url + "?in_iframe=1").text
        start = h.find("iCIMS_JobContent")  # description blocks are nested; take everything from here on
        job.description = html_to_text(h[start:] if start > 0 else h)[:20000]
    return fetch


# -------------------------------------------------------------------- Jibe
def jibe(site, pages=2, limit=100):
    """iCIMS Jibe career sites (jobs.fraserhealth.ca, careers.kpmg.ca): JSON, newest first."""
    host, name = site["host"], site["name"]
    out = []
    for page in range(1, pages + 1):
        # No Accept-Language: Jibe treats it as a filter, and postings are tagged en-US.
        d = get(f"https://{host}/api/jobs?page={page}&limit={limit}&sortBy=posted_date&descending=true&internal=false",
                headers={"Accept": "application/json", "Accept-Language": None}).json()
        batch = d.get("jobs") or []
        for item in batch:
            j = item.get("data", item)
            loc = j.get("full_location") or ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
            if j.get("state") and j["state"] not in loc:
                loc = f"{loc} ({j['state']})"
            desc = "\n\n".join(html_to_text(j.get(k) or "") for k in ("description", "responsibilities", "qualifications"))
            out.append(Job(source=f"jibe:{name}", company=company_of(site), title=j.get("title", ""), location=loc,
                           url=f"https://{host}/jobs/{j.get('slug') or j.get('req_id')}",
                           posted=(j.get("posted_date") or "")[:10], description=desc.strip(),
                           remote="remote" in (j.get("location_type") or "").lower()))
        if len(batch) < limit:
            break
    return out


# --------------------------------------------------------------------- UKG
def ukg(site, pages=2, top=50):
    """UKG (UltiPro) job boards: recruiting.ultipro.ca/<tenant>/JobBoard/<board>."""
    dom, tenant, board, name = site.get("domain", "recruiting.ultipro.ca"), site["tenant"], site["board"], site["name"]
    base = f"https://{dom}/{tenant}/JobBoard/{board}"
    out = []
    for page in range(pages):
        body = {"opportunitySearch": {"Top": top, "Skip": page * top, "QueryString": "",
                                      "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False}],
                                      "Filters": []},
                "matchCriteria": {"PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [], "Skills": [],
                                  "hasNoLicenses": False, "SkippedSkills": []}}
        d = post(f"{base}/JobBoardView/LoadSearchResults", json=body).json()
        ops = d.get("opportunities") or []
        for o in ops:
            locs = []
            for l in o.get("Locations") or []:
                a = l.get("Address") or {}
                parts = [a.get("City"), (a.get("State") or {}).get("Name"), (a.get("Country") or {}).get("Name")]
                locs.append(", ".join(p for p in parts if p) or l.get("LocalizedName") or "")
            job = Job(source=f"ukg:{name}", company=company_of(site), title=o.get("Title", ""), location="; ".join(l for l in locs if l),
                      url=f"{base}/OpportunityDetail?opportunityId={o['Id']}", posted=(o.get("PostedDate") or "")[:10],
                      description=html_to_text(o.get("BriefDescription") or ""))
            job.remote = "remote" in str(o.get("JobLocationType") or "").lower()
            job.fetch = _ukg_detail(job)
            out.append(job)
        if len(ops) < top:
            break
    return out


def _ukg_detail(job):
    def fetch():
        h = get(job.url).text
        m = re.search(r'"Description"\s*:\s*"((?:[^"\\]|\\.)*)"', h)
        if m:
            job.description = html_to_text(json.loads('"' + m.group(1) + '"'))
    return fetch


# ----------------------------------------------------- Taleo Business Edition
_PLACE = re.compile(r"\b(BC|AB|SK|MB|ON|QC|NB|NS|PE|NL|YT|NT|NU|British Columbia|Alberta|Ontario|Quebec|Canada|Remote|"
                    r"Vancouver|Burnaby|Surrey|Richmond|Victoria|Kelowna|Toronto|Calgary|Montreal|Ottawa)\b")
def taleo_be(site, max_pages=15):
    """Taleo Business Edition (xxx.tbe.taleo.net): 10 per page, paged with a session."""
    host, path, org, cws, name = site["host"], site["path"], site["org"], site["cws"], site["name"]
    base = f"https://{host}/{path}/ats/careers/v2"
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    out, seen, today = [], set(), date.today().isoformat()
    h = s.get(f"{base}/searchResults?org={org}&cws={cws}", timeout=25).text
    for page in range(max_pages):
        added = 0
        for block in re.split(r'<h4 class="oracletaleocwsv2-head-title">', h)[1:]:
            m = re.search(r'href="([^"]*viewRequisition\?org=[^"]*rid=(\d+))" class="viewJobLink">([^<]+)', block)
            if not m or m.group(2) in seen:
                continue
            seen.add(m.group(2))
            fields = [html_to_text(x) for x in re.findall(r'(?s)<div tabindex="0"\s*>(.*?)</div>', block.split("</h4>", 1)[-1])[:4]]
            # Field order differs per site (SFU: dept, duration, location; Grant Thornton: location first), and
            # some name no place at all (City of Burnaby: a facility), so employers.yaml can give `location`.
            loc = site.get("location") or next((f for f in fields if _PLACE.search(f)),
                                               next((f for f in reversed(fields) if f and not _mdy(f)), ""))
            job = Job(source=f"taleo:{name}", company=company_of(site), title=html.unescape(m.group(3)).strip(),
                      location=loc + ("" if re.search(r"\b(BC|British Columbia)\b", loc) else ", BC" if site.get("province") == "BC" else ""),
                      url=html.unescape(m.group(1)))
            # A date still to come is when applications close (New Westminster lists it).
            job.closing = next((d for d in map(_mdy, fields) if d and d > today), "")
            job.fetch = _taleo_detail(job)
            out.append(job)
            added += 1
        if not added:
            break
        h = s.get(f"{base}/searchResults?next&rowFrom={(page + 1) * 10}&act=null&sortColumn=null&sortOrder=null",
                  timeout=25).text
    return out


def _mdy(s):
    """'12/14/26' or '11/1/2026' -> '2026-12-14'; '' for anything else."""
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime((s or "").strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _taleo_detail(job):
    def fetch():
        h = get(job.url).text
        m = re.search(r'(?s)<h1.*?</h1>(.*?)(<footer|class="oracletaleocwsv2-footer)', h)
        job.description = html_to_text(m.group(1) if m else h)[:20000]
    return fetch


# ------------------------------------------------------------------ GC Jobs
GC = "https://emploisfp-psjobs.cfp-psc.gc.ca/psrs-srfp/applicant/"


class _GCSession:
    """GC Jobs pages load in two steps (page, then its results) inside one server session,
    so all requests - including full postings - go through one session, one at a time."""

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-CA,en;q=0.9"})
        self.lock = threading.Lock()
        self._get(GC + "page1710?toggleLanguage=en", 30)
        self._get(GC + "page2440?fromMenu=true", 30)
        self.sid = self.s.cookies.get("JSESSIONID")

    def _get(self, url, timeout):
        for attempt in range(3):  # the site drops a connection now and then
            try:
                r = self.s.get(url, timeout=timeout)
                r.raise_for_status()
                return r.text
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))

    def first_results(self):
        return self._get(f"{GC}page2440;jsessionid={self.sid}?fromMenu=true&isSecondPartOfPage=1&isInitialNetworkCheck=1", 90)

    def two_step(self, page, query):
        with self.lock:
            self._get(f"{GC}{page}?{query}", 60)
            return self._get(f"{GC}{page};jsessionid={self.sid}?{query}&isSecondPartOfPage=1", 90)


def gcjobs(site=None, max_pages=40):
    gc = _GCSession()
    out = []
    h = gc.first_results()
    last = int((re.search(r"of (\d+)\s*\[", re.sub(r"<[^>]+>", " ", h)) or [0, 1])[1])
    for page in range(1, min(last, max_pages) + 1):
        if page > 1:
            h = gc.two_step("page2440", f"requestedPage={page}&fromPage=1&tab=1&log=false")
        for item in re.findall(r'(?s)<li class="searchResult">(.*?)</li>', h):
            m = re.search(r'(?s)poster=(\d+)">(.*?)</a>', item)
            if not m:
                continue
            cell = (re.findall(r'(?s)<div class="tableCell">(.*?)</div>', item) or [""])[0]
            lines = [html_to_text(x) for x in re.split(r"<br\s*/?>", cell) if html_to_text(x)]
            closing = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("closing date")), "")
            rest = [l for l in lines if not l.lower().startswith("closing date")]
            # "National Defence - Canadian Forces Housing Agency": the department is the employer,
            # named as on Indeed and LinkedIn.
            dept = re.split(r"\s+-\s+|\n", rest[0])[0].strip() if rest else ""
            loc = "; ".join(rest[1:])
            job = Job(source="gcjobs", company=dept or "Government of Canada",
                      title=html_to_text(m.group(2)), location=loc or "Various locations",
                      url=f"{GC}page1800?poster={m.group(1)}&toggleLanguage=en")
            job.closing = closing
            job.fetch = _gc_detail(gc, m.group(1), job)
            out.append(job)
    return out


_GC_ELSEWHERE = re.compile(r'(?s)You will leave the.*?<a href="(https?://[^"]+)"')


def _gc_detail(gc, poster, job):
    def fetch():
        h = gc.two_step("page1800", f"poster={poster}")
        elsewhere = _GC_ELSEWHERE.search(h)
        if elsewhere:  # advertised on the department's own site: GC Jobs only links to it
            job.apply_url = html.unescape(elsewhere.group(1))
            page = get(job.apply_url, retries=0, timeout=15).text  # slow sites: retried next run instead
            body = re.search(r'(?s)<span class="jobdescription">(.*?)</span>\s*</div>', page)  # SuccessFactors
            job.description = html_to_text(body.group(1) if body else page)[:20000]
        else:
            job.description = html_to_text(h)[:20000]
    return fetch


# ---------------------------------------------------------------- TransLink
TRANSLINK = "https://getaway.translink.ca/api/jobpostings"
# The companies TransLink hires for, by the code its list gives, named as on LinkedIn and Indeed (so
# their copies of a posting count as the same job).
_TRANSLINK_COMPANIES = {"001": "TransLink", "BCR": "British Columbia Rapid Transit Company",
                        "BCT": "Coast Mountain Bus Company", "TSM": "TransLink Security Mgmt Ltd"}


def translink(site=None):
    """TransLink and its operating companies (SkyTrain, Coast Mountain Bus, Transit Security): one JSON
    list of open postings, each linking to a PeopleSoft posting page that needs no browser."""
    out = []
    for it in get(TRANSLINK, headers={"Accept": "application/json"}).json():
        if not it.get("url") or not it.get("title"):
            continue
        job = Job(source="translink", company=_TRANSLINK_COMPANIES.get(it.get("company"), "TransLink"),
                  title=it["title"].strip(), location="Metro Vancouver, BC", url=it["url"],
                  posted=(it.get("startDate") or "")[:10])
        end = it.get("endDate") or ""
        job.closing = end[:10] if re.match(r"\d{4}-\d\d-\d\d", end) else ""  # most say "N/A"
        job.fetch = _translink_detail(job)
        out.append(job)
    return out


def _translink_detail(job):
    def fetch():
        h = get(job.url).text
        # The posting's sections ("Responsibilities", "Qualifications", ...) run up to the Apply button.
        start, end = h.find("win0divHRS_SCH_PSTDSC_row$0"), h.find("win0divHRS_SCH_WRK_HRS_APPLY")
        if start > 0:
            job.description = html_to_text(h[h.rfind("<", 0, start):h.rfind("<", 0, end) if end > start else None])[:20000]
    return fetch
