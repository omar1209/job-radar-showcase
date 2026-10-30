"""Readers for employer careers systems. Each returns a list of Job objects.

Listing calls are cheap; the full description is fetched later (job.fetch)
only for jobs that pass the filters and haven't been seen before.
"""
import html
import logging
import re
from datetime import datetime
from urllib.parse import quote

from ..models import Job
from .common import company_of, get, html_to_text, post, relative_date

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- Workday
def workday(site, search_terms):
    t, wd, s, name = site["tenant"], site["wd"], site["site"], site["name"]
    base = f"https://{t}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{t}/{s}"
    jobs = {}
    for term in search_terms:
        offset = 0
        while offset < 400:
            body = {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term}
            d = post(f"{api}/jobs", json=body).json()
            batch = d.get("jobPostings", [])
            for p in batch:
                path = p.get("externalPath")
                if not path or path in jobs:
                    continue
                job = Job(source=f"workday:{name}", company=company_of(site), title=p.get("title", ""),
                          location=p.get("locationsText", ""), url=f"{base}/{s}{path}",
                          posted=relative_date(p.get("postedOn")),
                          remote="remote" in (p.get("locationsText") or "").lower())
                job.fetch = _workday_detail(f"{api}{path}", job)
                jobs[path] = job
            offset += 20
            if len(batch) < 20 or offset >= d.get("total", 0):
                break
    return list(jobs.values())


def _workday_detail(url, job):
    def fetch():
        info = get(url).json().get("jobPostingInfo", {})
        job.description = html_to_text(info.get("jobDescription", ""))
        # "Multiple locations" in the list view -> the real one is here
        locs = [info.get("location", "")] + (info.get("additionalLocations") or [])
        job.location = "; ".join(l for l in locs if l) or job.location
        if info.get("startDate"):
            job.posted = info["startDate"][:10]
    return fetch


# ------------------------------------------------------ SAP SuccessFactors
_SF_LINK = re.compile(r'<a[^>]*class="jobTitle-link[^"]*"[^>]*href="((?:/[\w-]+)?/job/[^"]+)"[^>]*>(.*?)</a>'
                      r'|<a[^>]*href="((?:/[\w-]+)?/job/[^"]+)"[^>]*class="jobTitle-link[^"]*"[^>]*>(.*?)</a>', re.S)
# Where a search result names its place: the table's location column, a tile's location field, or a
# location line ("Toronto, ON, CA +2 more" when there are several).
_SF_PLACE = (re.compile(r'(?s)<td class="colLocation[^"]*"[^>]*>(.*?)</td>'),
             re.compile(r'(?s)id="[^"]*-section-(?:location|multilocation)-value"[^>]*>(.*?)</div>'),
             re.compile(r'(?s)class="[^"]*\bjobLocation\b[^"]*"[^>]*>(.*?)</(?:span|div)>'))
_SF_MORE = re.compile(r"\+\s*(\d+)\s+more\b", re.I)
_SF_SEVERAL = re.compile(r"^\d+ locations$")


def successfactors(site, location_terms, pages=3):
    """SuccessFactors career sites, in their table or tile layout, or the newer one filled in by a
    JSON search (CN, Deloitte)."""
    name, dom = site["name"], site["domain"]
    jobs = {}
    for loc in location_terms:
        for page in range(pages):
            url = (f"https://{dom}/search/?q=&locationsearch={quote(loc)}"
                   f"&sortColumn=referencedate&sortDirection=desc&startrow={page * 25}")
            h = get(url).text
            if page == 0 and "rmk-jobs-search" in h and not _SF_LINK.search(h):
                for job in _sf_json(site, loc, pages, h):
                    jobs.setdefault(job.url, job)
                break
            # Tables list each link twice (desktop and phone views): a posting's fields run up to
            # the next posting's first link.
            first = {}
            for m in _SF_LINK.finditer(h):
                first.setdefault(m.group(1) or m.group(3), m)
            links = list(first.values())
            new_here = 0
            for i, m in enumerate(links):
                href, title = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
                if f"https://{dom}{href}" in jobs:
                    continue
                block = h[m.end(): links[i + 1].start() if i + 1 < len(links) else m.end() + 6000]
                date_m = re.search(r'class="[^"]*jobDate[^"]*"[^>]*>\s*([^<]+)', block)
                job = Job(source=f"successfactors:{name}", company=company_of(site), title=html_to_text(title),
                          location=_sf_place(block), url=f"https://{dom}{href}",
                          posted=_parse_date(date_m.group(1).strip()) if date_m else "")
                job.remote = "remote" in job.location.lower()
                job.fetch = _sf_detail(job)
                jobs[job.url] = job
                new_here += 1
            if new_here < 10:  # last page
                break
    return list(jobs.values())


def _sf_place(block):
    """A search result's place. "3 locations" when it names only the first of several, and "" when
    it names none (City of Vancouver): the full posting lists them all (_sf_detail)."""
    for pattern in _SF_PLACE:
        m = pattern.search(block)
        if m:
            text = re.sub(r"^(Other Locations|Location)\s*", "", " ".join(html_to_text(m.group(1)).split()))
            more = _SF_MORE.search(text)
            return f"{int(more.group(1)) + 1} locations" if more else text
    return ""


def _sf_json(site, term, pages, search_page):
    """The newer layout's search results, from the JSON call its page makes (10 at a time)."""
    dom = site["domain"]
    locale = (re.search(r"currentLocale:\s*'(\w+)'", search_page) or [None, "en_US"])[1]
    out, listed = [], 0
    for page in range(pages):
        body = {"locale": locale, "pageNumber": page, "sortBy": "date", "keywords": "", "location": term,
                "facetFilters": {}, "brand": "", "skills": [], "categoryId": 0, "alertId": "", "rcmCandidateId": ""}
        d = post(f"https://{dom}/services/recruiting/v1/jobs", json=body, headers={"Accept": "application/json"}).json()
        batch = [r.get("response") or {} for r in d.get("jobSearchResult") or []]
        for r in batch:
            if not r.get("id"):
                continue
            places = [" ".join(html_to_text(p).split()) for p in r.get("jobLocationShort") or []]
            job = Job(source=f"successfactors:{site['name']}", company=company_of(site),
                      title=html.unescape(r.get("unifiedStandardTitle") or ""), location="; ".join(p for p in places if p),
                      url=f"https://{dom}/job/{html.unescape(r.get('urlTitle') or 'job')}/{r['id']}-{locale}/",
                      posted=_parse_date(r.get("unifiedStandardStart") or ""))
            job.closing = _parse_date(r.get("unifiedStandardEnd") or "")
            job.remote = "remote" in f"{r.get('workModel') or ''} {job.location}".lower()
            job.fetch = _sf_detail(job)
            out.append(job)
        listed += len(batch)
        if not batch or listed >= (d.get("totalJobs") or 0):
            break
    return out


def _sf_detail(job):
    def fetch():
        h = get(job.url).text
        m = re.search(r'(?s)<span class="jobdescription">(.*?)</span>\s*</div>', h)
        # Some sites (Deloitte) split the posting into several description blocks.
        parts = [m.group(1)] if m else re.findall(r'(?s)itemprop="description"[^>]*>(.*?)</(?:span|div)>\s*</div>', h)
        job.description = "\n\n".join(html_to_text(p) for p in parts) if parts else html_to_text(h)
        places = _sf_places(h)
        if places and (not job.location or _SF_SEVERAL.match(job.location)):
            job.location = "; ".join(places)
        posted = re.search(r'itemprop="datePosted" content="\w+ (\w+) (\d+) [\d:]+ \w+ (\d{4})"', h)
        if posted and not job.posted:
            job.posted = _parse_date(f"{posted[1]} {posted[2]}, {posted[3]}")
    return fetch


def _sf_places(h):
    """Every place in a posting's schema.org address data: ["Toronto, ON", "Vancouver, BC"]."""
    out = []
    for block in re.split(r'itemtype="https?://schema\.org/PostalAddress"', h)[1:]:
        meta = dict(re.findall(r'<meta itemprop="(\w+)" content="([^"]*)"', block[:800]))
        region = meta.get("addressRegion", "")
        place = ", ".join(html.unescape(x) for x in (meta.get("addressLocality") or meta.get("streetAddress"),
                                                     "BC" if region == "Brit" else region) if x)
        if place and place not in out:
            out.append(place)
    return out


def _parse_date(s):
    for fmt in ("%b %d, %Y", "%d %b %Y", "%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


# ------------------------------------------------------------- Greenhouse
def greenhouse(site):
    d = get(f"https://boards-api.greenhouse.io/v1/boards/{site['slug']}/jobs?content=true").json()
    out = []
    for p in d.get("jobs", []):
        loc = (p.get("location") or {}).get("name", "")
        out.append(Job(source=f"greenhouse:{site['name']}", company=company_of(site), title=p["title"],
                       location=loc, url=p["absolute_url"], posted=(p.get("first_published") or p.get("updated_at") or "")[:10],
                       description=html_to_text(html.unescape(p.get("content", ""))),
                       remote="remote" in loc.lower()))
    return out


# ------------------------------------------------------------------ Lever
def lever(site):
    d = get(f"https://api.lever.co/v0/postings/{site['slug']}?mode=json").json()
    out = []
    for p in d:
        cat = p.get("categories", {})
        loc = "; ".join(cat.get("allLocations") or [cat.get("location", "")])
        desc = p.get("descriptionPlain", "") + "\n" + "\n".join(
            l.get("text", "") + "\n" + html_to_text(l.get("content", "")) for l in p.get("lists", []))
        out.append(Job(source=f"lever:{site['name']}", company=company_of(site), title=p["text"],
                       location=loc + (" (" + p.get("workplaceType", "") + ")" if p.get("workplaceType") else ""),
                       url=p["hostedUrl"],
                       posted=datetime.utcfromtimestamp(p.get("createdAt", 0) / 1000).date().isoformat(),
                       description=desc, remote=p.get("workplaceType") == "remote"))
    return out


# ------------------------------------------------------------------ Ashby
def ashby(site):
    d = get(f"https://api.ashbyhq.com/posting-api/job-board/{site['slug']}?includeCompensation=true").json()
    out = []
    for p in d.get("jobs", []):
        if not p.get("isListed", True):
            continue
        locs = [p.get("location", "")] + [s.get("location", "") for s in p.get("secondaryLocations", [])]
        addr = ((p.get("address") or {}).get("postalAddress") or {})
        loc = "; ".join(l for l in locs if l) + (f" ({addr.get('addressCountry', '')})" if addr.get("addressCountry") else "")
        out.append(Job(source=f"ashby:{site['name']}", company=company_of(site), title=p["title"], location=loc,
                       url=p["jobUrl"], posted=(p.get("publishedAt") or "")[:10],
                       description=p.get("descriptionPlain") or html_to_text(p.get("descriptionHtml", "")),
                       remote=bool(p.get("isRemote"))))
    return out


# -------------------------------------------------------- SmartRecruiters
def smartrecruiters(site):
    slug = site["slug"]
    d = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100").json()
    out = []
    for p in d.get("content", []):
        l = p.get("location", {})
        loc = ", ".join(x for x in [l.get("city"), l.get("region"), l.get("country")] if x)
        job = Job(source=f"smartrecruiters:{site['name']}", company=company_of(site), title=p["name"],
                  location=loc + (" (remote)" if l.get("remote") else ""),
                  url=f"https://jobs.smartrecruiters.com/{slug}/{p['id']}",
                  posted=(p.get("releasedDate") or "")[:10], remote=bool(l.get("remote")))
        job.fetch = _sr_detail(slug, p["id"], job)
        out.append(job)
    return out


def _sr_detail(slug, pid, job):
    def fetch():
        d = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{pid}").json()
        secs = (d.get("jobAd") or {}).get("sections", {})
        job.description = "\n\n".join(html_to_text(s.get("text", "")) for s in secs.values() if isinstance(s, dict))
    return fetch


# ------------------------------------------------------------ Oracle HCM
def oracle(site):
    host, sn = site["host"], site["site"]
    url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
           f"&expand=requisitionList&finder=findReqs;siteNumber={sn},limit=100,sortBy=POSTING_DATES_DESC")
    items = get(url).json().get("items", [])
    reqs = items[0].get("requisitionList", []) if items else []
    out = []
    for r in reqs:
        loc = "; ".join(x for x in [r.get("PrimaryLocation", "")] + [
            s.get("Name", "") for s in (r.get("secondaryLocations") or [])] if x)
        job = Job(source=f"oracle:{site['name']}", company=company_of(site), title=r.get("Title", ""),
                  location=loc + (f" ({r['WorkplaceType']})" if r.get("WorkplaceType") else ""),
                  url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{sn}/job/{r['Id']}",
                  posted=(r.get("PostedDate") or "")[:10],
                  remote="remote" in (r.get("WorkplaceType") or "").lower())
        job.fetch = _oracle_detail(host, sn, r["Id"], job)
        out.append(job)
    return out


def _oracle_detail(host, sn, rid, job):
    def fetch():
        u = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all"
             f"&onlyData=true&finder=ById;Id=%22{rid}%22,siteNumber={sn}")
        items = get(u).json().get("items", [])
        if items:
            it = items[0]
            job.description = html_to_text("\n".join(it.get(k) or "" for k in (
                "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")))
    return fetch


# ---------------------------------------------------- BC Public Service
BCPS = "https://bcpublicservice.hua.hrsmart.com"


def bc_public_service(pages=4):
    out = []
    for page in range(1, pages + 1):
        url = f"{BCPS}/hr/ats/JobSearch/viewAll" + (f"/jobSearchPaginationExternal_page:{page}" if page > 1 else "")
        h = get(url).text
        body = h.split('id="jobSearchResultsGrid_table"', 1)[-1]
        for row in re.findall(r"(?s)<tr>\s*<td>.*?</tr>", body):
            cells = re.findall(r"(?s)<td[^>]*>(.*?)</td>", row)
            m = re.search(r'href="(/hr/ats/Posting/view/\d+)"', row)
            if not m or len(cells) < 7:
                continue
            ministry, title, work, loc, opened = (html_to_text(cells[i]) for i in (0, 2, 4, 5, 6))
            job = Job(source="bcps", company=f"BC Public Service ({ministry})", title=title,
                      location=loc.replace("\n", "; ") + (f" ({work})" if work else ""),
                      url=BCPS + m.group(1), posted=_parse_date(opened), remote="remote" in work.lower())
            job.closing = _parse_date(html_to_text(cells[7])) if len(cells) > 7 else ""
            job.fetch = _bcps_detail(job)
            out.append(job)
    return out


def _bcps_detail(job):
    def fetch():
        h = get(job.url).text
        m = re.search(r'(?s)(Job Summary|Job Description).*?(?=<footer|$)', h)
        job.description = html_to_text(m.group(0) if m else h)[:20000]
    return fetch
