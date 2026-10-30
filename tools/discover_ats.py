"""Scan employer careers pages for ATS links (Workday, Greenhouse, Lever, ...).

Usage: python tools/discover_ats.py urls.txt   (lines: "Name|https://careers.url")
"""
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
PATTERNS = {
    "workday": r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)",
    "greenhouse": r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)",
    "lever": r"jobs\.lever\.co/([A-Za-z0-9_-]+)",
    "ashby": r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)",
    "smartrecruiters": r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)",
    "successfactors": r"(career\d*\.successfactors\.(?:com|eu)/[^\"'\s<>]*company=[A-Za-z0-9_]+)",
    "taleo": r"([a-z0-9-]+\.taleo\.net)",
    "icims": r"(careers-[a-z0-9-]+\.icims\.com)",
    "ukg": r"recruiting2?\.ultipro\.(?:com|ca)/([A-Z0-9]+)/JobBoard/([a-f0-9-]+)",
    "dayforce": r"jobs\.dayforcehcm\.com/(?:en-US/)?([a-z0-9-]+)",
    "phenom": r"(cdn\.phenompeople\.com)",
    "oracle": r"([a-z0-9-]+\.fa\.[a-z0-9-]+\.oraclecloud\.com/hcmUI/CandidateExperience/[^\"'\s<>]*sites/[A-Za-z0-9_]+)",
}
SKIP = {"workday": {"wday", "Images", "assets"}, "greenhouse": {"embed", "js"}}


def scan(html):
    found = set()
    for kind, pat in PATTERNS.items():
        for m in re.finditer(pat, html):
            g = m.groups()
            if g[-1] in SKIP.get(kind, ()):
                continue
            found.add((kind,) + g)
    return found


def fetch(url):
    try:
        r = requests.get(url, headers=UA, timeout=15, allow_redirects=True)
        return r.url, r.text
    except Exception as e:
        return url, ""


def probe(name, url):
    final, html = fetch(url)
    found = scan(final + " " + html)
    if not found and html:
        # follow likely "search jobs" links one level deep
        links = re.findall(r'href=["\']([^"\']+)["\']', html)
        cand = [urljoin(final, l) for l in links if re.search(r"job|career|search|opening|position|opportunit", l, re.I)]
        for l in list(dict.fromkeys(cand))[:12]:
            f2, h2 = fetch(l)
            found |= scan(f2 + " " + h2)
            if found:
                break
    return name, url, final, sorted(found)


if __name__ == "__main__":
    rows = [l.strip().split("|", 1) for l in open(sys.argv[1]) if "|" in l]
    with ThreadPoolExecutor(16) as ex:
        for name, url, final, found in ex.map(lambda r: probe(*r), rows):
            print(f"{name}\t{final}\t{found if found else 'NONE'}")
