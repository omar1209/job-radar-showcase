"""A whole run with fake sources, Claude and Sheet: new jobs, then branches posted later."""
import argparse
import json
from datetime import datetime, timedelta, timezone

import pytest

from radar import discover, main, sink, state
from radar.models import Job


def posting(source, location, url, title="Client Advisor", company="RBC", description="Full posting"):
    j = Job(source=source, company=company, title=title, location=location, url=url, description=description)
    if source == "indeed":
        j.label = "indeed: banking"
    return j


class FakeScorer:
    def __init__(self, cfg):
        self.usage = {}

    def cost(self):
        return 0.05

    def gate(self, j):
        return {"in_scope": "Barista" not in j.title, "family": "CL", "reason": "test", "_cost": 0.001,
                "likely_relevance": 20 if "Unlikely" in j.title else 80}

    def screen(self, j):
        return {"relevance": 85, "attainability": 75, "verdict": "Apply", "job_family": "CL", "hard_blockers": [],
                "years_required": "", "reason": "test", "key_requirements": ["Sales"], "unsupported": [], "fit_score": 80,
                "_cost": 0.02}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Runs main.run with these postings; returns what reached the Sheet."""
    sent, listing = [], {"jobs": []}
    monkeypatch.setattr(main, "Scorer", FakeScorer)
    monkeypatch.setattr(main, "Seen", lambda: state.Seen(str(tmp_path / "seen.json")))
    monkeypatch.setattr(discover.Discovered, "__init__",
                        lambda self, path=None, _i=discover.Discovered.__init__: _i(self, str(tmp_path / "disc.json")))
    monkeypatch.setattr(main, "ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "config").symlink_to(main.os.path.join(main.os.path.dirname(main.__file__), "..", "config"))

    def collect(cfg, emp, *a, **k):
        for j in listing["jobs"]:
            j.task = j.source
            j.label = getattr(j, "label", None) or j.source
        return listing["jobs"], {"workday:RBC": len(listing["jobs"]), "linkedin": 1, "indeed": 1}
    monkeypatch.setattr(main, "collect", collect)
    version = {"v": sink.SCRIPT_VERSION}
    monkeypatch.setattr(sink, "script_version", lambda: version["v"])
    monkeypatch.setattr(sink, "send", lambda payload: sent.append(json.loads(json.dumps(payload))) or True)

    def run(jobs, sheet_version=sink.SCRIPT_VERSION):
        listing["jobs"], version["v"] = jobs, sheet_version
        main.run(argparse.Namespace(dry_run=False, no_boards=False, save_state=False, volunteer=False, gcjobs=False))
        return sent[-1]
    run.path = tmp_path / "seen.json"
    run.seen = lambda: json.loads(run.path.read_text())
    return run


def test_branches_roll_up_then_new_places_are_sent_as_updates(world):
    first = world([posting("workday:RBC", "Vancouver, BC", "https://rbc/job/1"),
                   posting("workday:RBC", "Surrey, BC", "https://rbc/job/2"),
                   posting("workday:RBC", "Kelowna, BC", "https://rbc/job/3"),
                   posting("linkedin", "Surrey, BC", "https://www.linkedin.com/jobs/view/4"),
                   posting("workday:RBC", "Vancouver, BC", "https://rbc/job/9", title="Barista")])
    (row,) = [r for r in first["jobs"] if r["sheet"] == "Jobs"]
    assert (row["url"], row["score"]) == ("https://rbc/job/1", 80)
    assert [p["l"] for p in row["postings"]] == ["Surrey, BC"]  # Kelowna is out of the area; LinkedIn copies Surrey
    assert first["updates"] == []
    run = first["run"]
    assert run["stats"]["matches"] == 1 and run["stats"]["alerts"] == 1
    li = next(s for s in run["sources"] if s["label"] == "linkedin")
    assert li["new"] == 1 and li["only"] == 0
    seen = world.seen()
    assert seen[row["key"]]["s"] == "done" and sorted(seen[row["key"]]["l"]) == ["surrey", "vancouver"]

    # Next run: same postings plus a new branch and a repost on Indeed.
    second = world([posting("workday:RBC", "Vancouver, BC", "https://rbc/job/1"),
                    posting("workday:RBC", "Surrey, BC", "https://rbc/job/2"),
                    posting("workday:RBC", "Richmond, BC", "https://rbc/job/5"),
                    posting("indeed", "Vancouver, BC", "https://ca.indeed.com/viewjob?jk=6")])
    assert second["jobs"] == []
    assert second["updates"] == [{"key": row["key"], "postings": [
        {"u": "https://rbc/job/5", "l": "Richmond, BC", "s": "workday", "d": second["updates"][0]["postings"][0]["d"], "new": True}]}]
    assert second["run"]["stats"]["new_places"] == 1
    # Third run: nothing new.
    assert world([posting("workday:RBC", "Richmond, BC", "https://rbc/job/5")])["updates"] == []


def test_updates_are_resent_until_the_sheet_script_is_updated(world):
    world([posting("workday:RBC", "Vancouver, BC", "https://rbc/job/1")])
    later = [posting("workday:RBC", "Vancouver, BC", "https://rbc/job/1"), posting("workday:RBC", "Burnaby, BC", "https://rbc/job/7")]
    old = world(later, sheet_version=0)
    assert len(old["updates"]) == 1 and "sheet_script" in old["run"]["health"]
    assert len(world(later)["updates"]) == 1  # not remembered while the old script ignored it
    assert world(later)["updates"] == []


def test_a_job_seen_only_out_of_the_area_is_reopened_when_posted_nearby(world):
    first = world([posting("workday:RBC", "Kelowna, BC", "https://rbc/job/1")])
    assert first["jobs"] == []
    second = world([posting("workday:RBC", "Kelowna, BC", "https://rbc/job/1"),
                    posting("workday:RBC", "Burnaby, BC", "https://rbc/job/2")])
    assert [r["location"] for r in second["jobs"]] == ["Burnaby, BC"]


def test_jobs_tracked_before_this_version_start_remembering_silently(world):
    first = world([posting("workday:RBC", "Vancouver, BC", "https://rbc/job/1")])
    key = first["jobs"][0]["key"]
    seen = world.seen()
    seen[key] = {"d": seen[key]["d"], "s": "done"}  # an entry from before postings were remembered
    world.path.write_text(json.dumps(seen))
    again = world([posting("workday:RBC", "Vancouver, BC", "https://rbc/job/1"), posting("workday:RBC", "Surrey, BC", "https://rbc/job/2")])
    assert again["updates"] == []  # no flood of "new" places on the first run
    assert world([posting("workday:RBC", "Surrey, BC", "https://rbc/job/2"),
                  posting("workday:RBC", "Delta, BC", "https://rbc/job/3")])["updates"][0]["postings"][0]["l"] == "Delta, BC"


def test_due():
    seen = state.Seen("/nonexistent/seen.json")
    now = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
    assert main.due(seen, "__gcjobs__", 3, now)
    seen.data["__gcjobs__"] = {"d": "2026-10-22", "s": "meta", "t": (now - timedelta(hours=2, minutes=50)).isoformat()}
    assert main.due(seen, "__gcjobs__", 3, now)  # 15 minutes early is fine (runs drift)
    seen.data["__gcjobs__"]["t"] = (now - timedelta(hours=2)).isoformat()
    assert not main.due(seen, "__gcjobs__", 3, now)


def test_other_postings_sent_to_an_old_sheet_script_are_resent_once_it_is_updated(world):
    jobs = [posting("workday:RBC", "Vancouver, BC", "https://rbc/job/1"), posting("workday:RBC", "Surrey, BC", "https://rbc/job/2")]
    first = world(jobs, sheet_version=0)
    key = first["jobs"][0]["key"]
    assert world.seen()[key]["o"][0]["u"] == "https://rbc/job/2"
    assert world(jobs, sheet_version=0)["updates"] == []  # still the old script: kept for later
    second = world(jobs)
    assert second["updates"] == [{"key": key, "postings": [{"u": "https://rbc/job/2", "l": "Surrey, BC", "s": "workday",
                                                            "d": second["updates"][0]["postings"][0]["d"], "new": False}]}]
    assert "o" not in world.seen()[key] and world(jobs)["updates"] == []


def test_jobs_tracked_under_an_older_employer_name_match_keep_their_memory(world):
    first = world([posting("workday:KPMG", "Vancouver, BC", "https://kpmg/job/1", title="Audit Associate", company="KPMG")])
    new_key = first["jobs"][0]["key"]
    j = posting("linkedin", "Vancouver, BC", "https://www.linkedin.com/jobs/view/8", title="Audit Associate", company="KPMG Canada")
    assert j.key == new_key and j.legacy_key != new_key
    seen = world.seen()
    seen[j.legacy_key] = dict(seen.pop(new_key))  # as if it had been tracked under the old key
    world.path.write_text(json.dumps(seen))
    again = world([posting("workday:KPMG", "Burnaby, BC", "https://kpmg/job/2", title="Audit Associate", company="KPMG"), j])
    assert again["jobs"] == []  # not screened again, so no duplicate row
    assert again["updates"][0]["key"] == j.legacy_key  # the key its Sheet row has


def test_scored_jobs_are_logged_for_tuning_the_quick_check(world, tmp_path):
    world([posting("workday:RBC", "Vancouver, BC", "https://rbc/job/1"),
           posting("workday:RBC", "Vancouver, BC", "https://rbc/job/9", title="Barista Data Analyst")])  # off-track
    lines = [json.loads(l) for l in (tmp_path / "data" / "screen_log.jsonl").read_text().splitlines()]
    assert sorted((l["title"], l["in_scope"], l["tab"]) for l in lines) == [("Barista Data Analyst", False, "Screened out"),
                                                                            ("Client Advisor", True, "Jobs")]
    old = dict(lines[0], d="2026-01-01")
    (tmp_path / "data" / "screen_log.jsonl").write_text(json.dumps(old) + "\n")
    world([posting("workday:RBC", "Vancouver, BC", "https://rbc/job/2", title="Credit Analyst")])
    lines = [json.loads(l) for l in (tmp_path / "data" / "screen_log.jsonl").read_text().splitlines()]
    assert [l["title"] for l in lines] == ["Credit Analyst"]  # older than 14 days: dropped


PAGE = ('<ul><li><h3 class="description__job-criteria-subheader">Seniority level</h3>'
        '<span class="description__job-criteria-text">Entry level</span></li></ul>'
        '<div class="compensation__salary">Base pay range CA$60,000.00/yr - CA$70,000.00/yr</div>'
        '<div class="show-more-less-html__markup">Analyze data for our Vancouver team.</div>')


def linkedin_posting(n, title="Data Analyst"):
    from radar.sources import boards
    j = Job(source="linkedin", company="Acme", title=title, location="Vancouver, British Columbia, Canada",
            url=f"https://www.linkedin.com/jobs/view/{1000000000 + n}", posted="2026-10-27")
    j.label = "linkedin: data"
    j.fetch = boards._linkedin_detail(j)
    return j


@pytest.fixture
def linkedin_pages(monkeypatch):
    """LinkedIn's posting pages: refused until ok is set."""
    import types
    from radar.sources import boards
    state = {"ok": False}

    def fake_get(url, **kw):
        if not state["ok"]:
            raise RuntimeError("response status code 429")
        return types.SimpleNamespace(text=PAGE)
    monkeypatch.setattr(boards, "get", fake_get)
    monkeypatch.setattr(boards.time, "sleep", lambda s: None)
    return state


def test_linkedin_postings_that_could_not_be_fetched_are_retried_by_link(world, linkedin_pages):
    first = world([linkedin_posting(1)])
    assert first["jobs"] == [] and first["run"]["stats"]["pending"] == 1
    (entry,) = [v for v in world.seen().values() if v["s"].startswith("pending")]
    assert entry["s"] == "pending:1" and entry["p"]["u"].endswith("/1000000001")
    linkedin_pages["ok"] = True
    second = world([])  # no search lists it any more
    assert second["run"]["stats"]["retried"] == 1
    (row,) = second["jobs"]
    assert (row["title"], row["pay"]) == ("Data Analyst", "$60,000-$70,000 a year")
    assert "Pay: $60,000-$70,000 a year\n" in row["posting_text"]
    entry = world.seen()[row["key"]]
    assert entry["s"] == "done" and "p" not in entry
    assert world([])["run"]["stats"]["retried"] == 0


def test_once_linkedin_refuses_pages_the_rest_wait_without_using_a_try(world, linkedin_pages):
    out = world([linkedin_posting(i, title=f"Data Analyst {i}") for i in range(8)])
    li, pending = out["run"]["linkedin"], [v["s"] for v in world.seen().values() if v["s"].startswith("pending")]
    assert li["pages_refused"] >= 3 and li["pages_deferred"] >= 1 and li["pages_refused"] + li["pages_deferred"] == 8
    assert sorted(pending) == ["pending:0"] * li["pages_deferred"] + ["pending:1"] * li["pages_refused"]


def test_unlikely_jobs_skip_the_careful_screen_but_a_sample_is_still_screened(world):
    sc = main.load("search.yaml")["scoring"]
    if not sc.get("min_quick"):
        pytest.skip("cutoff turned off in config/search.yaml")
    sampled = lambda t: int(posting("workday:RBC", "Vancouver, BC", "u", title=t).key[:4], 16) % 100 < sc["quick_audit_pct"]
    titles = [f"Unlikely Analyst {i}" for i in range(200)]
    skipped, audited = next(t for t in titles if not sampled(t)), next(t for t in titles if sampled(t))
    out = world([posting("workday:RBC", "Vancouver, BC", "https://rbc/job/1", title=skipped),
                 posting("workday:RBC", "Vancouver, BC", "https://rbc/job/2", title=audited)])
    rows = {r["title"]: r for r in out["jobs"]}
    assert rows[skipped]["sheet"] == "Screened out" and rows[skipped]["score"] == 0
    assert rows[skipped]["reason"] == "Unlikely fit (quick check 20/100, not screened): test"
    assert rows[audited]["sheet"] == "Jobs"  # screened anyway, so the screen log keeps measuring the cutoff
    assert (out["run"]["stats"]["quick_skipped"], out["run"]["stats"]["screened"]) == (1, 1)


def test_postings_written_in_french_are_dropped_before_claude(world):
    french = "Vous serez responsable de la gestion des dossiers et de la relation avec les clients pour notre équipe. " * 3
    fr = posting("workday:BMO", "Vancouver, BC", "https://bmo/job/1", title="Conseiller, services bancaires", company="BMO",
                 description=french)
    out = world([fr, posting("workday:RBC", "Vancouver, BC", "https://rbc/job/1")])
    assert [r["title"] for r in out["jobs"]] == ["Client Advisor"] and out["run"]["stats"]["french"] == 1
    assert world.seen()[fr.key]["s"] == "f:language"
    assert world([fr])["run"]["stats"]["already_seen"] == 1  # not looked at again


def test_closing_dates_and_pay_reach_the_sheet(world):
    j = posting("gcjobs", "Vancouver (British Columbia)", "https://gc/job/1", title="Junior Economist", company="CMHC",
                description="Salary range: $62,000 - $70,000. Analyze housing data.")
    j.closing = "2026-11-04"
    (row,) = world([j])["jobs"]
    assert (row["closes"], row["pay"]) == ("2026-11-04", "$62,000-$70,000 a year")
    assert "Pay: $62,000-$70,000 a year\nCloses: 2026-11-04\n" in row["posting_text"]


def test_collect_gives_each_careers_site_its_own_search_terms(cfg, monkeypatch):
    asked = {}
    monkeypatch.setattr(main.ats, "successfactors", lambda s, terms: asked.setdefault(s["name"], terms) and [])
    monkeypatch.setattr(main.more_ats, "translink", lambda: [Job("translink", "TransLink", "Analyst", "Metro Vancouver, BC", "u")])
    emp = {"successfactors": {"location_terms": ["BC", "Remote"], "sites": [
        {"name": "ICBC", "domain": "careers.icbc.com"},
        {"name": "Canada Life", "domain": "jobs.canadalife.com", "location_terms": ["British Columbia"]}]}, "translink": True}
    jobs, health = main.collect(cfg, emp, use_boards=False)
    assert asked == {"ICBC": ["BC", "Remote"], "Canada Life": ["British Columbia"]}
    assert health["translink"] == 1 and jobs[0].task == "translink"
