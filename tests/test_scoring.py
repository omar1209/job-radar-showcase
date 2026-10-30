"""Claude calls: the cached prompt, the cost estimate, and the warm-up call that lets the others read the cache."""
import threading
import time
from types import SimpleNamespace

from radar import main, scoring
from radar.models import Job


def test_cost_uses_each_models_cache_prices():
    # Sonnet 5.5: input $2, output $10, cache read $0.10; a 5-minute cache write is 1.25x input.
    assert round(scoring.call_cost("claude-sonnet-5-5", {"cache_read": 1_000_000}), 4) == 0.10
    assert round(scoring.call_cost("claude-sonnet-5-5", {"cache_write": 1_000_000}), 4) == 2.50
    assert round(scoring.call_cost("claude-haiku-5-5", {"in": 1_000_000, "out": 1_000_000, "cache_read": 1_000_000}), 4) == 0.61
    assert scoring.call_cost("some-other-model", {"in": 1_000_000}) == 0


def test_the_prompt_is_cached_for_five_minutes(cfg):
    s = scoring.Scorer(cfg)
    sent = {}
    usage = SimpleNamespace(input_tokens=1000, output_tokens=50, cache_read_input_tokens=0, cache_creation_input_tokens=1100)
    out = scoring.Gate(in_scope=True, family="AN", likely_relevance=80, reason="analyst role")
    resp = SimpleNamespace(usage=usage, stop_reason="end_turn", parsed_output=out)
    s.client = SimpleNamespace(messages=SimpleNamespace(parse=lambda **kw: sent.update(kw) or resp))
    res = s.gate(Job("workday:RBC", "RBC", "Research Analyst", "Vancouver, BC", "u", description="Analyze data."))
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}  # default 5 minutes, written at 1.25x
    assert res["in_scope"] and round(res["_cost"], 7) == round((1000 * 0.10 + 1100 * 0.125 + 50 * 0.50) / 1e6, 7)


def test_one_call_per_prompt_finishes_before_the_rest_start():
    events, lock = [], threading.Lock()

    def fn(x):
        with lock:
            events.append(("start", x))
        time.sleep(0.01)
        with lock:
            events.append(("end", x))
        return x * 10

    items = [1, 2, 3, 4, 5, 6]
    out = main.cached_map(fn, items, lambda x: "odd" if x % 2 else "even", workers=4)
    assert out == [10, 20, 30, 40, 50, 60]
    first_rest = min(i for i, (kind, x) in enumerate(events) if kind == "start" and x > 2)
    assert {("end", 1), ("end", 2)} <= set(events[:first_rest])  # 1 and 2 wrote the two prompts' caches
    assert main.cached_map(fn, [], lambda x: x, workers=4) == []


def test_the_posting_header_shows_pay_closing_date_and_seniority():
    j = Job("linkedin", "Acme", "Data Analyst", "Vancouver, BC", "u", posted="2026-10-27", description="Analyze data.")
    j.pay, j.closing, j.seniority = "$60,000-$70,000 a year", "2026-11-04", "Entry level"
    text = scoring._posting(j)
    assert ("Pay: $60,000-$70,000 a year\nApplications close: 2026-11-04\nLinkedIn seniority level: Entry level\n"
            in text)
    assert "Pay:" not in scoring._posting(Job("workday:RBC", "RBC", "Analyst", "Vancouver, BC", "u", description="x"))
