from datetime import datetime, timedelta, timezone

from radar.main import board_window


class FakeSeen:
    def __init__(self, last=None):
        self.data = {"__boards__": {"t": last.isoformat()}} if last else {}


NOW = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc)


def test_first_run_reads_a_full_day():
    assert board_window(FakeSeen(), NOW) == 24


def test_hourly_runs_read_three_hours():
    assert board_window(FakeSeen(NOW - timedelta(minutes=60)), NOW) == 3


def test_overnight_gap_is_covered():
    # last run 8:17pm, next 7:17am (11h) -> at least 11h back
    assert board_window(FakeSeen(NOW - timedelta(hours=11)), NOW) >= 11


def test_window_is_capped():
    assert board_window(FakeSeen(NOW - timedelta(days=10)), NOW) == 48


def fake_jobspy(monkeypatch, calls, record):
    import sys, types
    import pandas as pd
    from radar.sources import boards
    fake = types.SimpleNamespace(scrape_jobs=lambda **kw: calls.append(record(kw)) or pd.DataFrame())
    monkeypatch.setitem(sys.modules, "jobspy", fake)
    monkeypatch.setattr(boards.time, "sleep", lambda s: None)
    return boards


def test_linkedin_remote_searches_only_when_due(cfg, monkeypatch):
    # LinkedIn ignores its remote filter for visitors who aren't signed in: the remote searches ask for
    # postings anywhere in Canada that mention remote work.
    calls = []
    boards = fake_jobspy(monkeypatch, calls, lambda kw: (kw["location"] == "Canada" and kw["search_term"].endswith(") AND remote"),
                                                         kw["hours_old"], "is_remote" in kw))
    n = len(cfg["linkedin"]["queries"])
    boards.linkedin(cfg, 3)
    assert calls == [(False, 3, False)] * n
    calls.clear()
    boards.linkedin(cfg, 3, remote_hours=5)
    assert sorted(calls) == sorted([(False, 3, False)] * n + [(True, 5, False)] * n)


def test_indeed_remote_searches_only_when_due(cfg, monkeypatch):
    calls = []
    boards = fake_jobspy(monkeypatch, calls, lambda kw: (kw.get("is_remote", False), kw["location"], kw["hours_old"]))
    boards.indeed(cfg, 3)
    assert calls == [(False, cfg["indeed"]["location"], 3)] * len(cfg["indeed"]["queries"])
    calls.clear()
    boards.indeed(cfg, 3, remote_hours=5)
    assert calls.count((True, "Canada", 5)) == len(cfg["indeed"]["remote_queries"]) > 0
