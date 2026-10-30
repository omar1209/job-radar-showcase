"""LinkedIn and Indeed: what counts as remote, what a posting page gives us, Indeed's remote searches."""
import sys
import types

import pandas as pd
import pytest

from radar.models import Job
from radar.sources import boards


@pytest.mark.parametrize("title,text,remote", [
    ("Data Analyst", "This role is fully remote across Canada.", True),
    ("Data Analyst (Remote)", "Join our analytics team.", True),
    ("Analyst", "You can work from anywhere in Canada.", True),
    ("Analyst", "This is a remote position open to BC residents.", True),
    ("Analyst", "Work from home with a laptop provided.", True),
    ("Analyst", "Job type: Full-time. Work location: Remote", True),
    ("Client Service Representative", "This is a home-based position.", True),
    ("Analyste", "Poste 100% télétravail.", True),
    ("Analyste", "Mode hybride : télétravail deux jours par semaine.", False),  # Quebec's usual hybrid wording
    ("Analyst", "This is not a remote role. Work from our Toronto office.", False),
    ("Analyst", "Hybrid: work from home two days a week.", False),
    ("Banking Advisor", "Help clients with remote deposit and virtual banking.", False),
    ("Analyst", "Join our branch team in Brantford.", False),
])
def test_what_counts_as_remote(title, text, remote):
    assert boards.says_remote(title, text) is remote


def test_a_posting_page_gives_text_seniority_and_pay(monkeypatch):
    page = ('<h3 class="description__job-criteria-subheader">Seniority level</h3>'
            '<span class="description__job-criteria-text description__job-criteria-text--criteria"> Associate </span>'
            '<div class="compensation__salary">Base pay range CA$8,236.00/mo - CA$9,896.00/mo</div>'
            '<div class="show-more-less-html__markup">This role is fully remote across Canada.</div>')
    monkeypatch.setattr(boards, "get", lambda url, **kw: types.SimpleNamespace(text=page))
    boards.new_run()
    j = Job("linkedin", "Acme", "Analyst", "Toronto, Ontario, Canada (Remote)", "https://www.linkedin.com/jobs/view/1234567890")
    j.remote, j.remote_unverified = True, True
    boards._linkedin_detail(j)()
    assert (j.description, j.seniority, j.pay) == ("This role is fully remote across Canada.", "Associate", "$8,236-$9,896 a month")
    assert j.remote and not j.remote_unverified


def test_a_pending_posting_comes_back_as_it_was():
    j = Job("linkedin", "Acme", "Data Analyst", "Toronto, Ontario, Canada (Remote)", "https://www.linkedin.com/jobs/view/1234567890",
            posted="2026-10-27", remote=True)
    j.label, j.remote_unverified = "linkedin: data (remote)", True
    back = boards.revive(boards.pending_info(j)["p"])
    assert (back.key, back.url, back.location, back.posted, back.label, back.task) == (
        j.key, j.url, j.location, "2026-10-27", "linkedin: data (remote)", "linkedin")
    assert back.remote and back.remote_unverified and callable(back.fetch)
    assert boards.pending_info(Job("indeed", "Acme", "Analyst", "Vancouver, BC", "https://ca.indeed.com/viewjob?jk=1")) == {}


def test_indeed_remote_searches_keep_only_jobs_indeed_tags_remote(cfg, monkeypatch):
    rows = pd.DataFrame([
        {"title": "Data Analyst", "company": "A", "location": "Canada", "job_url": "https://ca.indeed.com/viewjob?jk=1",
         "is_remote": True, "description": "Analyze.", "min_amount": 60000.0, "max_amount": 70000.0, "interval": "yearly"},
        {"title": "Data Analyst", "company": "B", "location": "Toronto, ON, Canada", "job_url": "https://ca.indeed.com/viewjob?jk=2",
         "is_remote": False, "description": "Analyze.", "min_amount": float("nan"), "max_amount": float("nan"), "interval": None},
    ])
    monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(scrape_jobs=lambda **kw: rows if kw.get("is_remote") else rows[:0]))
    cfg = {**cfg, "indeed": {**cfg["indeed"], "remote_queries": ["analyst"]}}
    (j,) = boards.indeed(cfg, 3, remote_hours=5)
    assert (j.company, j.location, j.remote, j.label, j.pay) == ("A", "Canada (Remote)", True, "indeed: analyst (remote)",
                                                                  "$60,000-$70,000 a year")
