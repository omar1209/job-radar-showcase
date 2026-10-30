"""Per-source numbers and source health."""
from radar import stats
from radar.models import Job


def job(label, task):
    j = Job(source=task, company="X", title="Analyst", location="Vancouver, BC", url="https://x/" + label)
    j.label, j.task = label, task
    return j


def test_credit_is_shared_between_the_sources_that_listed_a_job():
    s = stats.SourceStats()
    a, b, c = job("linkedin: research", "linkedin"), job("indeed: analyst", "indeed"), job("workday:RBC", "workday:RBC")
    s.listed([a, b, c, c])
    s.job([a, b], screened=True, match=True, alert=True, cost=0.02)
    s.job([c], screened=True, match=True, cost=0.01)
    s.job([a], screened=False, match=False, cost=0.0002)
    rows = {r["label"]: r for r in s.table()}
    assert rows["workday:RBC"]["listed"] == 2 and rows["workday:RBC"]["only"] == 1
    assert rows["linkedin: research"] == {"label": "linkedin: research", "task": "linkedin", "listed": 1, "new": 2,
                                          "screened": 1, "matches": 1, "only": 0, "alerts": 1, "branches": 0, "cost": 0.0102}
    assert rows["indeed: analyst"]["cost"] == 0.01


def test_health_history_and_problems():
    h = {}
    stats.update_health(h, {"workday:RBC": 40, "icims:VCH": 100, "ashby:Quiet": 0}, "2026-10-01")
    assert stats.stale(h, "2026-10-01") == []
    for day in ("2026-10-02", "2026-10-03"):
        stats.update_health(h, {"workday:RBC": "ERROR: 503", "icims:VCH": 0, "ashby:Quiet": 0}, day)
    assert h["workday:RBC"]["e"] == 2 and h["workday:RBC"]["ok"] == "2026-10-01"
    probs = {p["task"]: p["why"] for p in stats.stale(h, "2026-10-03")}
    assert probs == {"icims:VCH": "no postings for 2 days (it listed 100 before)"}
    for i in range(4):
        stats.update_health(h, {"workday:RBC": "ERROR: 503"}, "2026-10-03")
    assert "failing for 6 runs in a row: ERROR: 503" in stats.problem(h["workday:RBC"], "2026-10-03")
    assert stats.problem(h["ashby:Quiet"], "2026-10-14") is None
    assert stats.problem(h["ashby:Quiet"], "2026-10-15") == "no postings in 14 days of checks"
    stats.update_health(h, {"workday:RBC": 38}, "2026-10-04")
    assert stats.problem(h["workday:RBC"], "2026-10-04") is None
    st = stats.status(h, "2026-10-04")
    assert st["workday:RBC"] == {"ok": True, "note": "OK", "last_ok": "2026-10-04", "last_postings": "2026-10-04"}


def test_sources_removed_from_the_config_are_forgotten():
    h = {}
    stats.update_health(h, {"greenhouse:Gone": 5, "workday:RBC": 40}, "2026-10-01")
    stats.update_health(h, {"workday:RBC": 40}, "2026-10-09")
    assert set(h) == {"workday:RBC"}
