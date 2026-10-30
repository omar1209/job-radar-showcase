import hashlib
import re
from dataclasses import asdict, dataclass, field


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# Job boards spell employers differently from their careers sites ("BMO Financial Group" vs "BMO",
# "KPMG Canada" vs "KPMG"). "Bank of Canada" keeps its "Canada".
_LEGAL_SUFFIX = re.compile(r"(?:\s+(?:inc|incorporated|ltd|limited|corp|corporation|co|llc|llp|lp|plc|financial|holdings|"
                           r"group|authority)|(?<!\bof)\s+canada)+$")
_ALIASES = {"royal bank of canada": "rbc", "rbc royal bank": "rbc", "rbc wealth management": "rbc",
            "rbc dominion securities": "rbc", "toronto dominion": "td", "td bank": "td", "td bank group": "td",
            "td securities": "td", "td asset management": "td", "bank of montreal": "bmo", "bmo nesbitt burns": "bmo",
            "bmo capital markets": "bmo", "canadian imperial bank of commerce": "cibc", "cibc wood gundy": "cibc",
            "bank of nova scotia": "scotiabank", "scotia wealth management": "scotiabank",
            "vancouver city savings credit union": "vancity", "insurance corporation of british columbia": "icbc",
            "british columbia investment management": "bci", "university of british columbia": "ubc",
            "simon fraser university": "sfu", "pricewaterhousecoopers": "pwc",
            "canada mortgage and housing corporation": "cmhc", "cmhc schl": "cmhc",
            "coast capital savings": "coast capital", "coast capital savings credit union": "coast capital",
            "coast capital savings federal credit union": "coast capital",
            "blueshore": "beem credit union", "beem": "beem credit union"}


def canon_company(name):
    c = re.sub(r"^the\s+", "", norm(name))
    c = _LEGAL_SUFFIX.sub("", c)
    return _ALIASES.get(c, c)


# Employer matching before 2026-10-22, so jobs already tracked keep their memory (main.run).
# Remove after 2026-12-22, when seen.json has forgotten the old keys anyway.
_OLD_SUFFIX = re.compile(r"(\s+(inc|incorporated|ltd|limited|corp|corporation|co|llc|llp|lp|plc|financial|holdings|group))+$")
_OLD_ALIASES = {k: v for k, v in _ALIASES.items() if k in (
    "royal bank of canada", "rbc royal bank", "toronto dominion", "td bank", "td bank group", "bank of montreal",
    "canadian imperial bank of commerce", "bank of nova scotia", "vancouver city savings credit union",
    "insurance corporation of british columbia", "british columbia investment management", "university of british columbia")}
_OLD_ALIASES["the bank of nova scotia"] = "scotiabank"


def _old_canon(name):
    c = _OLD_SUFFIX.sub("", norm(name))
    return _OLD_ALIASES.get(c, c)


@dataclass
class Job:
    source: str            # e.g. "workday:RBC", "linkedin", "indeed"
    company: str
    title: str
    location: str
    url: str
    posted: str = ""       # ISO date (YYYY-MM-DD) when known
    description: str = ""  # plain text
    remote: bool = False
    # filled in later
    triage: dict = field(default_factory=dict)
    deep: dict = field(default_factory=dict)

    @property
    def key(self):
        """Same job seen on LinkedIn and on the company site collapses to one key."""
        return self._key(canon_company)

    @property
    def legacy_key(self):
        return self._key(_old_canon)

    def _key(self, canon):
        base = ("vol|" if getattr(self, "kind", "") == "volunteer" else "") + canon(self.company) + "|" + norm(self.title)
        return hashlib.sha1(base.encode()).hexdigest()[:16]

    def to_dict(self):
        d = asdict(self)
        d["key"] = self.key
        return d
