import html
import re
import time
from datetime import date, timedelta

import requests

from .. import guard

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en-CA,en;q=0.9"})


def company_of(site):
    """Employer name for a careers site entry; `company` lets two sites of one employer
    (e.g. "PwC" and "PwC (campus)") count as the same employer."""
    return site.get("company") or site["name"]


def get(url, retries=2, **kw):
    return _req("GET", url, retries, **kw)


def post(url, retries=2, **kw):
    return _req("POST", url, retries, **kw)


def _req(method, url, retries, **kw):
    kw.setdefault("timeout", 25)
    for attempt in range(retries + 1):
        try:
            r = session.request(method, url, **kw)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(3 * (attempt + 1))


def html_to_text(s):
    if not s:
        return ""
    s = guard.clean_html(s)
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "\n- ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def relative_date(text):
    """'Posted Today' / 'Posted 3 Days Ago' / '30+ Days Ago' -> ISO date."""
    t = (text or "").lower()
    today = date.today()
    if "today" in t or "just" in t or "hour" in t:
        return today.isoformat()
    if "yesterday" in t:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*day", t)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    return ""
