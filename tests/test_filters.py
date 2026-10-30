"""The free filters must never drop the kinds of jobs the radar exists to find."""
import pytest

from radar.filters import Filters
from radar.models import Job

TARGET_TITLES = """Research Analyst|Economic Analyst|Policy Analyst|Junior Economist|Research Assistant|Research Officer 18R
|Data Analyst I|Business Analyst|Reporting Analyst|BI Analyst|Operations Analyst|Pricing Analyst|Retail Pricing Analyst
|Retail Credit Analyst|Retail Credit Adjudicator|Retail Lending Specialist|Credit Analyst|Risk Analyst|Financial Analyst
|FP&A Analyst|Budget Analyst|Service Delivery Analyst|Delivery Analyst, Client Reporting|Client Reporting Analyst
|Performance Analyst|Portfolio Assistant|Investment Associate|Associate, Investment Operations|Trade Support Analyst
|Middle Office Associate|Settlements Associate|Fund Accounting Associate|Personal Banking Associate|Personal Banker
|Financial Services Representative|Client Service Representative, Contact Centre|Contact Centre Advisor - Banking
|Client Advisor|Customer Experience Associate|Member Service Representative|Banking Advisor|Mobile Mortgage Specialist
|Financial Advisor|Associate Investment Advisor|Sales Assistant, Wealth Management|Sales Associate, Wealth Management
|Investment Sales Associate|Administrative Associate - Wealth|Client Onboarding Specialist|KYC Analyst|AML Analyst
|Analyst, Know Your Client|Program Evaluation Analyst|Market Research Analyst|Insights Analyst|Analyst Program 2027
|Graduate Program - Finance|Management Trainee|Rotational Analyst|Credit Counsellor|Financial Counsellor
|Staff Accountant|Junior Accountant|Analyst, Head Office Operations|Lead Generation Analyst|New Accounts Administrator
|Registered Plans Administrator|Teller|Bank Teller|Analyst, Store Planning|Retail Analytics Analyst|Data Coordinator
|Research Coordinator|Statistical Assistant|Economist|Associate Economist|Planning Analyst, Distribution Centre
|Supply Chain Analyst|Procurement Analyst|Student Financial Assistance Analyst|Assistant Branch Manager|Associate Relationship Manager|Retail Banking Advisor
""".replace("\n", "").split("|")

JUNK_TITLES = """Gas Station Attendant|Lumber & Building Materials Sales Associate|Store Associate|Retail Sales Associate
|Retail Store Manager|Assistant Store Manager|Barista|Line Cook|Cashier|Delivery Driver|Warehouse Associate
|Mental Health Counsellor|Registered Nurse|Software Engineer|Senior Financial Analyst|Manager, Risk|Director of Research
|Summer Student - Finance|Student, Relationship Management|Student - Finance|Co-op Student, Data|Finance Intern|Partner, Corporate Restructuring|Telemarketing Agent|Front Desk Agent|Security Guard|Forklift Operator|Sales Associate
""".replace("\n", "").split("|")


@pytest.fixture(scope="module")
def f(cfg):
    return Filters(cfg)


@pytest.mark.parametrize("title", TARGET_TITLES)
def test_target_titles_kept(f, title):
    assert f.title_ok(title), title


@pytest.mark.parametrize("title", JUNK_TITLES)
def test_junk_titles_dropped(f, title):
    assert not f.title_ok(title), title


def job(loc, remote=False):
    return Job("x", "Co", "Analyst", loc, "u", remote=remote)


@pytest.mark.parametrize("loc,ok", [
    ("Vancouver, BC, CAN", True), ("VANCOUVER, British Columbia, Canada", True), ("Burnaby, BC, Canada (On-site)", True),
    ("British Columbia, Canada", True), ("Multiple Locations, BC, CA (Primary) (On-Site)", True),
    ("Hybrid - Kamloops or Vancouver, BC, CA", True), ("Canada (Remote)", True), ("Remote (Canada)", True),
    ("Victoria, BC, CA V8W 9V1 (Hybrid)", False), ("Kelowna, British Columbia", False), ("Toronto, ON", False),
    ("Vancouver, WA", False), ("Remote, US", False),
    ("CAN, British Columbia - Full Time Remote", True), ("Remote - BC", True), ("Remote, United States", False),
])
def test_locations(f, loc, ok):
    assert f.location_ok(job(loc, remote="remote" in loc.lower())) is ok


@pytest.mark.parametrize("loc", ["3 Locations", "2 Locations", "1 Location", "", "  "])
def test_multi_location_postings_are_deferred_not_dropped(f, loc):
    j = job(loc)
    assert f.location_unknown(j)
    assert f.why_rejected(j) is None


def test_fingerprint_changes_with_filters(cfg):
    import copy
    other = copy.deepcopy(cfg)
    other["titles"]["reject"].append("x")
    assert Filters(cfg).fingerprint != Filters(other).fingerprint


@pytest.mark.parametrize("a,b", [("BMO Financial Group", "BMO"), ("Canaccord Genuity Corp.", "Canaccord Genuity"),
                                 ("Royal Bank of Canada", "RBC"), ("TD Bank", "TD"), ("Desjardins Group", "Desjardins"),
                                 ("Vancouver City Savings Credit Union", "Vancity"), ("Intact Financial Corporation", "Intact")])
def test_same_employer_same_key(a, b):
    assert Job("x", a, "Analyst", "", "u").key == Job("y", b, "Analyst", "", "u").key


@pytest.mark.parametrize("a,b", [("Bank of Canada", "Canada Life"), ("TD", "TD Synnex"), ("Coast Capital", "Capital Power")])
def test_different_employers_different_keys(a, b):
    assert Job("x", a, "Analyst", "", "u").key != Job("y", b, "Analyst", "", "u").key


def test_suffix_stripping_keeps_distinct_names():
    from radar.models import canon_company
    assert canon_company("Sun Life Financial Inc.") == "sun life"
    assert canon_company("Financial Group of Canada") == "financial group of canada"  # only trailing suffixes


@pytest.mark.parametrize("text,pay", [
    ("Salary range: $65,000 - $75,000", "$65,000-$75,000 a year"),
    ("$28.50 to $32.10 per hour", "$28.50-$32.10 an hour"),
    ("Hourly: $24.00–$27.50", "$24.00-$27.50 an hour"),
    ("The pay range is $60k-$70k", "$60,000-$70,000 a year"),
    ("Base pay range CA$8,236.00/mo - CA$9,896.00/mo", "$8,236-$9,896 a month"),
    ("between $72,000 and $85,000, depending on experience", "$72,000-$85,000 a year"),
    ("Compensation: $52,500 – $61,800 CAD per annum", "$52,500-$61,800 a year"),
    ("Earn a $1,000 - $2,000 signing bonus", ""),
    ("You will manage $5 million - $10 million in assets", ""),
    ("No pay stated.", ""),
])
def test_pay_ranges(text, pay):
    from radar.filters import pay_from_text
    assert pay_from_text(text) == pay


def test_postings_mostly_in_french():
    from radar.filters import mostly_french
    en = "You will analyze data and support the team with reporting. We are looking for a graduate with SQL. " * 3
    fr = "Vous serez responsable de la gestion des dossiers et de la relation avec les clients pour notre équipe. " * 3
    assert mostly_french(fr) and not mostly_french(en)
    assert not mostly_french(en + fr)  # bilingual postings are kept
    assert not mostly_french("Le poste") and not mostly_french("")
