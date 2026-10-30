"""One job, several postings.

An employer may advertise the same role at several branches, and one posting is often copied to
LinkedIn and Indeed. All of them share the job's key (employer + title), so the Sheet gets one
row: the best posting, with the others listed beside it. Postings for new places that appear
later are added to the existing row and flagged, instead of being skipped as already seen.

Memory per job (in seen.json): "u" = ids of postings already examined, "l" = places posted in.
Postings out of the area are only remembered when finding that out took a page fetch; the
free location check is simply repeated.
"""
import copy
import hashlib
import re

from .models import norm

# Company sites first: their postings are the canonical ones.
PRIORITY = ["workday", "successfactors", "oracle", "icims", "jibe", "ukg", "taleo", "bcps", "gcjobs", "translink",
            "greenhouse", "lever", "ashby", "smartrecruiters", "volunteerconnector", "linkedin", "indeed"]
BOARDS = {"linkedin", "indeed"}
MAX_IDS = 100      # per job: plenty for a role posted at every branch in the region
MAX_FETCHES = 4        # full postings fetched per job while looking for one in the area
MAX_PLACE_FETCHES = 6  # then, other "3 Locations" postings fetched to list their real places


def kind(job):
    return job.source.split(":")[0]


def is_board(job):
    return kind(job) in BOARDS


def is_vol(job):
    return getattr(job, "kind", "") == "volunteer"


def pid(job):
    return hashlib.sha1((job.url or "").encode()).hexdigest()[:8]


_WORK_MODE = re.compile(r"\([^)]*\b(?:remote|hybrid|on-?site|in-?office|flexible)\b[^)]*\)|\b(?:remote|hybrid)\b", re.I)


def loc_ids(location):
    """One id per place: 'Vancouver, BC; Burnaby, BC (Hybrid)' -> ['burnaby', 'vancouver']."""
    ids = set()
    for part in re.split(r"[;|]|\s/\s", location or ""):
        place = norm(_WORK_MODE.sub(" ", part).split(",")[0])
        if place:
            ids.add(place)
    return sorted(ids) or ["?"]


_TRACKING = re.compile(r"^(utm_\w+|source|src|ref|refid|gh_src|lever-\w+|trk|trackingid|codes|iis|iisn)$", re.I)


def _clean(url):
    """URL without tracking parameters (a posting's id may be in the query, so the rest stays)."""
    base, _, query = url.partition("?")
    keep = [kv for kv in query.split("&") if kv and not _TRACKING.match(kv.split("=")[0])]
    return base.rstrip("/") + ("?" + "&".join(keep) if keep else "")


def _urls(p):
    """A posting's URL, and the employer posting it links to (Indeed, GC Jobs)."""
    return {_clean(u) for u in (p.url, getattr(p, "apply_url", "")) if u}


def places_of(p, filters):
    """Where a posting matters to you: {"remote"} for a remote job (one place, whatever city a board
    files it under), else its in-area places. Empty when none is known ("3 Locations")."""
    if p.remote or "remote" in (p.location or "").lower():
        return {"remote"}
    out = set()
    for part in re.split(r"[;|]|\s/\s", p.location or ""):
        probe = copy.copy(p)
        probe.location, probe.remote = part, False
        if part.strip() and not filters.location_unknown(probe) and filters.location_ok(probe):
            out |= set(loc_ids(part))
    return out


def unconfirmed(p):
    """A LinkedIn "remote" result whose posting wasn't fetched: probably on-site somewhere else."""
    return getattr(p, "remote_unverified", False) and getattr(p, "location_deferred", False)


def remembered(p, filters):
    """Place ids kept for a posting: every place it names, plus where it matters to you."""
    return set(loc_ids(p.location)) | places_of(p, filters)


def merge_ids(old, new):
    out = list(old)
    have = set(out)
    for i in new:
        if i not in have:
            out.append(i)
            have.add(i)
    return out[-MAX_IDS:]


def group(jobs):
    """key -> that job's postings, in listing order."""
    out = {}
    for j in jobs:
        out.setdefault(j.key, []).append(j)
    return out


def best_first(posts, cities):
    """Company-site postings first, then known locations, then the earliest city in the list."""
    def rank(j):
        where = (j.location or "").lower()
        city = next((i for i, c in enumerate(cities) if c in where), len(cities))
        k = kind(j)
        return (is_board(j), bool(getattr(j, "location_deferred", False)), city,
                PRIORITY.index(k) if k in PRIORITY else len(PRIORITY))
    return sorted(posts, key=rank)


def _remote_unconfirmed(p, filters):
    """A LinkedIn "remote" result outside the area: in the area only if the posting says it's remote."""
    if not getattr(p, "remote_unverified", False):
        return False
    on_site = copy.copy(p)
    on_site.remote, on_site.location = False, p.location.replace(" (Remote)", "")
    return not filters.location_ok(on_site)


def in_area(posts, filters):
    """Postings in the area, or whose area is only in the full posting ("3 Locations", or a "remote"
    LinkedIn result that may be on-site elsewhere), best first."""
    out = []
    for p in posts:
        vol = is_vol(p)
        p.location_deferred = not vol and (filters.location_unknown(p) or _remote_unconfirmed(p, filters))
        if vol or p.location_deferred or filters.location_ok(p):
            out.append(p)
    return best_first(out, filters.cities)


def out_of_area_once_fetched(job, filters):
    return job.location_deferred and not filters.location_unknown(job) and not filters.location_ok(job)


class Group:
    """One job's postings in this run: the in-area ones (`cands`) and the one its row uses (`job`)."""

    def __init__(self, key, posts, cands, prev=None):
        self.key, self.posts, self.cands = key, posts, cands
        self.prev = prev or {}
        self.job = None
        self._filters = None
        self._out = set()  # id()s of postings found out of the area once fetched

    def resolve(self, fetch, filters):
        """Picks the row's posting: the first candidate that (fetched where needed) is in the area
        and has a description; failing that, the first in-area one (its text is retried later).
        Then fetches the other "3 Locations" postings, so the row lists their real places."""
        self._filters = filters
        fetched = set()

        def get(j):
            fetched.add(id(j))
            fetch(j)

        fallback = None
        for j in self.cands:
            if (not j.description or j.location_deferred) and hasattr(j, "fetch"):
                if len(fetched) == MAX_FETCHES:
                    break
                get(j)
            if out_of_area_once_fetched(j, filters):
                self._out.add(id(j))
                continue
            if j.description:
                self.job = j
                break
            fallback = fallback or j
        self.job = self.job or fallback
        if self.job:  # job-board copies aren't worth a fetch just to be listed
            others = [j for j in self.cands if j.location_deferred and id(j) not in fetched and hasattr(j, "fetch")
                      and not is_board(j)]
            for j in others[:MAX_PLACE_FETCHES]:
                get(j)
                if out_of_area_once_fetched(j, filters):
                    self._out.add(id(j))
        return self.job

    @property
    def passing(self):
        return [p for p in self.cands if id(p) not in self._out]

    def others(self):
        """Postings listed beside the row's link: every other company-site posting, and board copies
        only for places no listed posting covers (one link per place)."""
        if not self.job:
            return []
        f = self._filters
        covered, urls, out = remembered(self.job, f), _urls(self.job), []
        for p in sorted(self.passing, key=is_board):  # stable: company postings first
            if _urls(p) & urls or unconfirmed(p):  # the same posting, or a "remote" result never checked
                continue
            ids = places_of(p, f) or set(loc_ids(p.location))
            if is_board(p) and ids <= covered:  # a copy: same place, or another copy of a remote job
                continue
            urls |= _urls(p)
            covered |= ids
            out.append(p)
        return out

    def memory(self):
        out = [pid(p) for p in self.cands if id(p) in self._out]
        if self.job is None:  # nothing in the area (yet): only remember what took a fetch to rule out
            return {"u": merge_ids(self.prev.get("u", []), out)}
        examined = [pid(p) for p in self.passing] + out
        places = set(self.prev.get("l", []))
        for p in self.passing:
            if not unconfirmed(p):
                places |= remembered(p, self._filters)
        return {"u": merge_ids(self.prev.get("u", []), examined), "l": sorted(places)[:MAX_IDS]}


def first_memory(posts, filters):
    """For a job tracked before postings were remembered: note what's listed now, flag nothing."""
    ok = [p for p in posts if is_vol(p) or filters.location_unknown(p) or filters.location_ok(p)]
    return {"u": merge_ids([], [pid(p) for p in ok]),
            "l": sorted({i for p in ok for i in remembered(p, filters)})[:MAX_IDS]}


def unseen(posts, prev):
    known = set(prev.get("u", []))
    return [p for p in posts if pid(p) not in known]


def branch_changes(prev, fresh, filters):
    """fresh: unseen in-area postings of a job already in the Sheet. Returns [(posting, new_place)]:
    postings in an in-area place the job wasn't posted in before, or remote for the first time
    (flagged), plus other company-site postings (added quietly). Board copies are left out unless
    they're the new place: a remote job reposted under another city is the same job."""
    places = set(prev.get("l", []))
    if "remote" not in places and any(not _in_area_id(i, filters) for i in places):
        places.add("remote")  # remembered before "remote" was: an out-of-area place came from a remote copy
    out = []
    for p in sorted(fresh, key=is_board):
        here = places_of(p, filters)
        if here - places:
            out.append((p, True))
        elif not is_board(p):
            out.append((p, False))
        places |= remembered(p, filters)
    return out


def _in_area_id(place, filters):
    from .models import Job
    probe = Job(source="", company="", title="", location=place, url="")
    return filters.location_unknown(probe) or filters.location_ok(probe) or _generic_place(place)


def _generic_place(place):
    return place in ("?", "bc", "british columbia", "canada", "can", "multiple locations", "various locations") \
        or place.endswith(" locations")


def link(p, today, new=None):
    d = {"u": p.url, "l": (p.location or "")[:80], "s": kind(p), "d": today}
    if new is not None:
        d["new"] = new
    return d
