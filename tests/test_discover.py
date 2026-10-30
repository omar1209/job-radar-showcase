"""Finding employers' careers feeds through job-board apply links."""
import pytest

from radar import discover
from radar.models import Job

LINKS = [
    ("https://acme.wd3.myworkdayjobs.com/en-US/Acme_Careers/job/Vancouver-BC/Analyst_R123?source=Indeed",
     ("workday", {"tenant": "acme", "wd": "wd3", "site": "Acme_Careers"})),
    ("https://wd5.myworkdaysite.com/recruiting/acme/External/job/Vancouver/Analyst_R1",
     ("workday", {"tenant": "acme", "wd": "wd5", "site": "External"})),
    ("https://job-boards.greenhouse.io/acmeco/jobs/4500123", ("greenhouse", {"slug": "acmeco"})),
    ("https://boards.greenhouse.io/embed/job_app?token=1&for=acmeco", ("greenhouse", {"slug": "acmeco"})),
    ("https://jobs.lever.co/acme-inc/1b2c3d", ("lever", {"slug": "acme-inc"})),
    ("https://jobs.ashbyhq.com/acme/12ab", ("ashby", {"slug": "acme"})),
    ("https://jobs.smartrecruiters.com/AcmeCorp/7443", ("smartrecruiters", {"slug": "AcmeCorp"})),
    ("https://careers-acme.icims.com/jobs/1234/analyst/job?in_iframe=1", ("icims", {"host": "careers-acme.icims.com"})),
    ("https://recruiting.ultipro.ca/ACM5000ACME/JobBoard/062C8FBA-7371-4cd7-9e8a-94a0b8019ffc/OpportunityDetail?opportunityId=1",
     ("ukg", {"domain": "recruiting.ultipro.ca", "tenant": "ACM5000ACME", "board": "062c8fba-7371-4cd7-9e8a-94a0b8019ffc"})),
    ("https://phh.tbe.taleo.net/phh01/ats/careers/v2/viewRequisition?org=ACME&cws=56&rid=1",
     ("taleo_be", {"host": "phh.tbe.taleo.net", "path": "phh01", "org": "ACME", "cws": 56})),
    ("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/123",
     ("oracle", {"host": "eeho.fa.us2.oraclecloud.com", "site": "CX_1"})),
    ("https://careers.acme.ca/job/Vancouver-Analyst-BC/1173450400/", ("successfactors", {"domain": "careers.acme.ca"})),
]


@pytest.mark.parametrize("url,expected", LINKS)
def test_parse_apply_links(url, expected):
    assert discover.parse(url) == expected


@pytest.mark.parametrize("url", ["https://ca.indeed.com/viewjob?jk=1", "https://www.linkedin.com/jobs/view/1/",
                                 "https://www.talent.com/view?id=1", "https://acme.bamboohr.com/careers/12", ""])
def test_links_we_cannot_read(url):
    assert discover.parse(url) is None


def test_system_hints():
    assert discover.system_hint("https://acme.bamboohr.com/careers/12") == "BambooHR"
    assert discover.system_hint("https://apply.workable.com/acme/j/ABC/") == "Workable"
    assert discover.system_hint("https://acme.com/careers") == ""


def test_configured_includes_names_and_identities():
    emp = {"workday": {"sites": [{"name": "PwC (campus)", "company": "PwC", "tenant": "pwc", "wd": "wd3", "site": "Campus"}]},
           "greenhouse": [{"name": "Hootsuite", "slug": "hootsuite"}], "jibe": [{"name": "KPMG", "host": "careers.kpmg.ca"}]}
    ids, names = discover.configured(emp)
    assert ("workday", "pwc", "campus") in ids and ("greenhouse", "hootsuite") in ids
    assert {"pwc", "hootsuite", "kpmg"} <= names


def board(company, title="Research Analyst", apply_url=""):
    j = Job(source="indeed", company=company, title=title, location="Vancouver, BC", url="https://ca.indeed.com/viewjob?jk=" + company)
    j.apply_url = apply_url
    return j


def test_discovery_adds_a_feed_for_an_employer_with_matches(tmp_path, monkeypatch):
    checked = []
    monkeypatch.setattr(discover, "check", lambda kind, p: checked.append((kind, p)) or True)
    d = discover.Discovered(str(tmp_path / "discovered.json"))
    covered = d.covered({"rbc"}, [board("BC Hydro")], "2026-10-22")
    assert "bc hydro" in covered and "province of british columbia" in covered
    d.note_matches([(board("Acme Analytics Inc."), [])], covered, "2026-10-22")
    d.note_matches([(board("RBC"), [])], covered, "2026-10-22")  # read directly already
    assert list(d.data["uncovered"]) == ["acme analytics"]
    added = d.discover([board("Acme Analytics", "Data Clerk", "https://jobs.lever.co/acme/1"),
                        board("Unrelated Co", "Analyst", "https://jobs.lever.co/unrelated/2")], set(), "2026-10-22")
    assert checked == [("lever", {"slug": "acme"})]
    assert added[0]["name"] == "Acme Analytics Inc. (auto)" and added[0]["company"] == "Acme Analytics Inc."
    emp = d.merge_into({"lever": [{"name": "Plooto", "slug": "plooto"}], "workday": {"search_terms": ["BC"], "sites": []}})
    assert [s["slug"] for s in emp["lever"]] == ["plooto", "acme"]
    assert d.tasks() == {"lever:Acme Analytics Inc. (auto)": ("lever", added[0])}
    d.save()
    again = discover.Discovered(str(tmp_path / "discovered.json"))
    assert again.data["feeds"]["lever"][0]["slug"] == "acme" and not again.data["uncovered"]


def test_feeds_that_fail_are_removed_and_not_added_again(tmp_path, monkeypatch):
    monkeypatch.setattr(discover, "check", lambda kind, p: True)
    d = discover.Discovered(str(tmp_path / "d.json"))
    d.note_matches([(board("Acme"), [])], set(), "2026-10-01")
    d.discover([board("Acme", apply_url="https://jobs.lever.co/acme/1")], set(), "2026-10-01")
    hist = {"lever:Acme (auto)": {"since": "2026-10-01", "e": 12, "err": "ERROR: 404"}}
    assert d.prune(hist, "2026-10-03") == ["lever:Acme (auto)"]
    assert d.data["feeds"] == {} and d.summary()["removed"][0]["why"].startswith("kept failing")
    d.note_matches([(board("Acme"), [])], set(), "2026-10-04")
    assert d.discover([board("Acme", apply_url="https://jobs.lever.co/acme/9")], set(), "2026-10-04") == []


def test_feeds_that_never_list_anything_are_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(discover, "check", lambda kind, p: True)
    d = discover.Discovered(str(tmp_path / "d.json"))
    d.note_matches([(board("Acme"), [])], set(), "2026-10-01")
    d.discover([board("Acme", apply_url="https://jobs.lever.co/acme/1")], set(), "2026-10-01")
    hist = {"lever:Acme (auto)": {"since": "2026-10-01", "e": 0, "ok": "2026-10-08"}}
    assert d.prune(hist, "2026-10-07") == []
    assert d.prune(hist, "2026-10-08") == ["lever:Acme (auto)"]


def test_checks_per_run_are_capped(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(discover, "check", lambda kind, p: calls.append(p) or False)
    d = discover.Discovered(str(tmp_path / "d.json"))
    names = [f"Employer {i}" for i in range(8)]
    d.note_matches([(board(n), []) for n in names], set(), "2026-10-01")
    d.discover([board(n, apply_url=f"https://jobs.lever.co/e{i}/1") for i, n in enumerate(names)], set(), "2026-10-01")
    assert len(calls) == discover.MAX_CHECKS
    assert d.data["uncovered"]["employer 0"]["system"] == "lever (its feed didn't answer)"
