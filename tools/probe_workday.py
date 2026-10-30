"""Brute-force likely Workday tenant/site combos via the public CXS jobs endpoint."""
import itertools
import sys
from concurrent.futures import ThreadPoolExecutor

import requests

SITES = ["External", "Careers", "careers", "External_Careers", "ExternalCareers", "Search", "jobs", "Jobs",
         "{t}", "{T}", "{t}careers", "{T}_Careers", "{T}Careers", "{t}_careers", "{T}_External", "en-US/{T}"]
WD = ["wd1", "wd3", "wd5", "wd10", "wd12", "wd103"]


def try_one(t, wd, site):
    url = f"https://{t}.{wd}.myworkdayjobs.com/wday/cxs/{t}/{site}/jobs"
    try:
        r = requests.post(url, json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}, timeout=8)
        if r.ok and "jobPostings" in r.text:
            return t, wd, site, r.json().get("total")
    except Exception:
        pass
    return None


def probe(t):
    combos = [(t, wd, s.format(t=t, T=t.upper() if len(t) <= 4 else t.capitalize())) for wd, s in itertools.product(WD, SITES)]
    with ThreadPoolExecutor(24) as ex:
        hits = [h for h in ex.map(lambda c: try_one(*c), combos) if h]
    return t, hits


if __name__ == "__main__":
    with ThreadPoolExecutor(4) as ex:
        for t, hits in ex.map(probe, sys.argv[1:]):
            print(t, hits or "NONE", flush=True)
