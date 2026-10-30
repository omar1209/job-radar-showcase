"""Volunteer opportunities: Volunteer Connector (powers GoVolunteer.ca) + Indeed.

Read once a day; volunteer postings don't move as fast as jobs.
"""
import logging
import math
from concurrent.futures import ThreadPoolExecutor

from ..models import Job
from .common import get, html_to_text

log = logging.getLogger(__name__)
VC = "https://www.volunteerconnector.org/api/search/"
VANCOUVER = (49.2827, -123.1207)
MAX_KM = 45  # Metro Vancouver, incl. Langley and Maple Ridge


def _km(lat, lon):
    a, b = map(math.radians, VANCOUVER)
    c, d = math.radians(lat), math.radians(lon)
    h = math.sin((c - a) / 2) ** 2 + math.cos(a) * math.cos(c) * math.sin((d - b) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _where(r):
    """Location label if the opportunity is reachable from Vancouver, else None."""
    aud = r.get("audience") or {}
    remote = str(r.get("remote_or_online")).lower() == "true"
    if remote:
        return "Remote / online"
    scope = aud.get("scope")
    if scope == "national":
        return "Canada-wide"
    if scope == "regional" and "British Columbia" in (aud.get("regions") or []):
        return "British Columbia (regional)"
    if scope == "local" and aud.get("latitude") is not None and _km(aud["latitude"], aud["longitude"]) <= MAX_KM:
        city = r.get("url", "").split("/")[3:4]
        return (city[0].replace("-", " ").title() if city else "Metro Vancouver") + ", BC"
    return None


def volunteer_connector():
    first = get(VC).json()
    pages = math.ceil(first.get("count", 0) / max(len(first.get("results", [])), 1))

    def page(p):
        try:
            return get(f"{VC}?page={p}").json().get("results", [])
        except Exception as e:
            log.info("volunteer connector page %s failed: %s", p, e)
            return []

    with ThreadPoolExecutor(8) as ex:
        results = first.get("results", []) + [r for pg in ex.map(page, range(2, pages + 1)) for r in pg]
    out = []
    for r in results:
        where = _where(r)
        if not where:
            continue
        acts = ", ".join(a.get("name", "") for a in r.get("activities") or [])
        desc = html_to_text(r.get("description", ""))
        desc += f"\n\nActivities: {acts}\nTime commitment: {r.get('duration', '')}\nDates: {r.get('dates', '')}"
        job = Job(source="volunteerconnector", company=(r.get("organization") or {}).get("name", ""),
                  title=r.get("title", ""), location=where, url=r.get("url", ""), description=desc,
                  remote=where.startswith("Remote"))
        job.kind = "volunteer"
        out.append(job)
    return out


def indeed_volunteer(cfg, hours_old):
    from jobspy import scrape_jobs
    from .boards import _rows_to_jobs
    out = []
    for q in cfg.get("volunteer", {}).get("indeed_queries", ["volunteer"]):
        try:
            df = scrape_jobs(site_name=["indeed"], search_term=q, location=cfg["indeed"]["location"],
                             distance=cfg["indeed"]["distance_miles"], hours_old=hours_old,
                             results_wanted=100, country_indeed="Canada", verbose=0)
            for j in _rows_to_jobs(df, "indeed", f"indeed: {q}"):
                j.kind = "volunteer"
                out.append(j)
        except Exception as e:
            log.warning("indeed volunteer query failed: %s", e)
    return out
