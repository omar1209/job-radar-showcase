"""Free, instant filters applied before any posting reaches Claude."""
import hashlib
import json
import re

# Bump when filter *code* changes in a way that should re-check jobs dropped earlier.
FILTER_VERSION = 2

# "3 Locations" (Workday) or "Various locations" (GC Jobs): the real places are only in the full posting.
_AMBIGUOUS_LOCATION = re.compile(r"^\s*(\d+\s+locations?|various locations|multiple locations)?\s*$", re.I)


class Filters:
    def __init__(self, cfg):
        loc = cfg["locations"]
        self.cities = [c.lower() for c in loc["cities"]]
        self.reject = [r.lower() for r in loc["reject"]]
        self.remote_needs = [w.lower() for w in loc["remote_needs"]]
        t = cfg["titles"]
        self.seniority = re.compile(t["seniority"], re.I)
        self.rescue = re.compile(t["seniority_rescue"], re.I)
        self.keep_if = re.compile(t["keep_if"], re.I) if t.get("keep_if") else None
        self.title_reject = [re.compile(p, re.I) for p in t["reject"]]
        self.always_reject = [re.compile(p, re.I) for p in t.get("always_reject", [])]
        self.fingerprint = hashlib.sha1(json.dumps(
            [FILTER_VERSION, cfg["titles"], cfg["locations"]], sort_keys=True).encode()).hexdigest()[:12]

    @staticmethod
    def location_unknown(job):
        """True when the location can't be judged until the full posting is fetched."""
        return bool(_AMBIGUOUS_LOCATION.match(job.location or ""))

    def location_ok(self, job):
        where = f" {job.location} ".lower()
        if any(r in where for r in self.reject):
            return False
        if any(c in where for c in self.cities):
            return True
        if (job.remote or "remote" in where) and any(w in where for w in self.remote_needs):
            return True
        return any(_generic_bc(part) for part in re.split(r"[;|/]| or ", where))

    def title_ok(self, title):
        if any(p.search(title) for p in self.always_reject):
            return False
        if self.seniority.search(title) and not self.rescue.search(title):
            return False
        if self.keep_if and self.keep_if.search(title):
            return True
        return not any(p.search(title) for p in self.title_reject)

    def why_rejected(self, job):
        """Reason to drop the job now, or None. An unknown location is checked after the full posting
        is fetched (see main.run), so it never counts as a reason here."""
        if not self.title_ok(job.title):
            return "title"
        if not self.location_unknown(job) and not self.location_ok(job):
            return "location"
        return None


_BC_WORDS = re.compile(
    r"british columbia|\bbc\b|\bcanada\b|\bcan\b|\bca\b|multiple locations|various locations|"
    r"\bprimary\b|hybrid|on-?site|in-?office|remote|flexible|province-?wide|\b[a-z]\d[a-z] ?\d[a-z]\d\b|[^a-z]")


def _generic_bc(part):
    """True for 'British Columbia, Canada' or 'Multiple Locations, BC' (no other place named)."""
    if not re.search(r"british columbia|\bbc\b", part):
        return False
    return _BC_WORDS.sub("", part).strip() == ""


# Phrases worth flagging for Claude (it decides whether they are hard limits).
EXPERIENCE_RE = re.compile(
    r"(\b\d{1,2}\s*(?:\+|plus)?\s*(?:-|to|–)?\s*\d{0,2}\s*\+?\s*years?\b[^.\n]{0,80})", re.I
)


def experience_snippets(text, limit=6):
    return [m.group(1).strip() for m in EXPERIENCE_RE.finditer(text or "")][:limit]


# Common French and English words. A posting written mostly in French needs French, which the candidate
# doesn't have: the careful screen would only score it Avoid. Bilingual postings have plenty of both.
_FRENCH = re.compile(r"\b(le|la|les|des|du|de|une|et|est|pour|avec|vous|nous|dans|sur|votre|nos|vos|aux|sont|être|"
                     r"cette|qui|que)\b", re.I)
_ENGLISH = re.compile(r"\b(the|and|is|for|with|you|we|your|our|to|of|are|be|will|this|that)\b", re.I)


def mostly_french(text):
    fr, en = len(_FRENCH.findall(text or "")), len(_ENGLISH.findall(text or ""))
    return fr >= 20 and fr > 3 * en


# Pay ranges: "$65,000 - $75,000", "$28.50 to $32.10 per hour", LinkedIn's "CA$8,236.00/mo - CA$9,896.00/mo".
# BC postings must state one (Pay Transparency Act).
_AMOUNT = r"(?:CAD?\s?|C)?\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s?([kK]\b)?"
_PER = r"(?:\s*(?:/|per|an|a)\s*(hours?|hr|years?|yr|annum|months?|mo|weeks?|wk)\b)?"
PAY_RE = re.compile(_AMOUNT + _PER + r"\s*(?:-|–|—|to|and)\s*" + _AMOUNT + _PER, re.I)
_PERIODS = {"h": "an hour", "y": "a year", "a": "a year", "m": "a month", "w": "a week", "d": "a day"}
_PAY_WORDS = re.compile(r"salary|pay|wage|compensation|rate|range|earn|hourly|annual", re.I)
_NOT_PAY = re.compile(r"bonus|signing|stipend|allowance|referral|incentive|reimburse|in assets|million", re.I)


def pay_range(lo, hi, per=""):
    """'$65,000-$75,000 a year' from two amounts (either may be missing); per: 'yearly', 'hour', 'mo', ..."""
    nums = sorted(x for x in (lo, hi) if x and x > 0)
    if not nums:
        return ""
    lo, hi = nums[0], nums[-1]
    period = _PERIODS.get((per or "").lower()[:1], "")
    if not period:  # not stated: a year for salaries, an hour for wages, unsaid in between
        period = "a year" if hi >= 15000 else "an hour" if hi < 300 else ""
    fmt = (lambda x: f"${x:,.2f}") if period == "an hour" else (lambda x: f"${x:,.0f}")
    return f"{fmt(lo) if lo == hi else fmt(lo) + '-' + fmt(hi)} {period}".strip()


def _amount(whole, cents, k):
    x = float(whole.replace(",", "")) + (float("0." + cents) if cents else 0)
    return x * 1000 if k else x


def pay_from_text(text):
    """The first range in the posting that reads as pay (a period, a salary-sized number, or a pay word before
    it), skipping bonuses, allowances and the like."""
    for m in PAY_RE.finditer(text or ""):
        lo, hi, per = _amount(*m.group(1, 2, 3)), _amount(*m.group(5, 6, 7)), m.group(8) or m.group(4) or ""
        if _NOT_PAY.search(text[m.end():m.end() + 25]) or hi < lo:
            continue
        if per or hi >= 15000 or _PAY_WORDS.search(text[max(0, m.start() - 60):m.start()]):
            return pay_range(lo, hi, per)
    return ""
