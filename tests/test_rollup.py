"""One job, several postings: grouping, the row's posting, other postings, and new places later."""
import pytest

from radar import rollup
from radar.filters import Filters
from radar.models import Job


@pytest.fixture(scope="module")
def filters(cfg):
    return Filters(cfg)


def post(source, location, url, description="text", company="RBC", title="Client Advisor", fetch=None):
    j = Job(source=source, company=company, title=title, location=location, url=url, description=description)
    j.label = source
    if fetch:
        j.fetch = lambda j=j: fetch(j)
    return j


def test_loc_ids():
    assert rollup.loc_ids("Vancouver, BC; Burnaby, BC (Hybrid)") == ["burnaby", "vancouver"]
    assert rollup.loc_ids("Vancouver, British Columbia, Canada") == ["vancouver"]
    assert rollup.loc_ids("Canada (Remote)") == ["canada"]
    assert rollup.loc_ids("Remote - Canada") == ["canada"]
    assert rollup.loc_ids("") == ["?"]


def test_group_and_best_first(filters):
    posts = [post("linkedin", "Vancouver, BC", "https://www.linkedin.com/jobs/view/1"),
             post("workday:RBC", "Surrey, BC", "https://rbc.wd3.myworkdayjobs.com/x/job/Surrey/2"),
             post("workday:RBC", "Vancouver, BC", "https://rbc.wd3.myworkdayjobs.com/x/job/Vancouver/3"),
             post("workday:RBC", "Kelowna, BC", "https://rbc.wd3.myworkdayjobs.com/x/job/Kelowna/4")]
    groups = rollup.group(posts)
    assert len(groups) == 1
    cands = rollup.in_area(next(iter(groups.values())), filters)
    # Company site first, nearest city first among those; out-of-area Kelowna dropped.
    assert [c.location for c in cands] == ["Vancouver, BC", "Surrey, BC", "Vancouver, BC"]
    assert rollup.is_board(cands[-1])


def test_others_lists_branches_once_each(filters):
    posts = [post("workday:RBC", "Vancouver, BC", "https://x/job/1"),
             post("workday:RBC", "Surrey, BC", "https://x/job/2"),
             post("linkedin", "Surrey, BC", "https://www.linkedin.com/jobs/view/9"),        # copy of Surrey
             post("indeed", "Burnaby, BC", "https://ca.indeed.com/viewjob?jk=abc"),        # a place only Indeed has
             post("indeed", "Burnaby, BC", "https://ca.indeed.com/viewjob?jk=def")]        # repost of it
    g = rollup.Group("k", posts, rollup.in_area(posts, filters))
    g.resolve(lambda j: None, filters)
    assert g.job.url == "https://x/job/1"
    assert [(p.location, rollup.kind(p)) for p in g.others()] == [("Surrey, BC", "workday"), ("Burnaby, BC", "indeed")]


def test_others_skips_links_to_a_listed_posting(filters):
    sf = post("successfactors:CMHC", "Vancouver, BC", "https://careers.cmhc-schl.gc.ca/job/Vancouver-Analyst/605969717/")
    gc = post("gcjobs", "Various locations", "https://emploisfp-psjobs.cfp-psc.gc.ca/psrs-srfp/applicant/page1800?poster=1")
    gc.apply_url = "https://careers.cmhc-schl.gc.ca/job/Vancouver-Analyst/605969717/?source=gc"
    g = rollup.Group("k", [sf, gc], rollup.in_area([sf, gc], filters))
    g.resolve(lambda j: None, filters)
    assert g.job is sf and g.others() == []


def test_query_ids_are_not_tracking():
    a = post("ukg:MNP", "Vancouver, BC", "https://recruiting.ultipro.ca/T/JobBoard/B/OpportunityDetail?opportunityId=1")
    b = post("ukg:MNP", "Vancouver, BC", "https://recruiting.ultipro.ca/T/JobBoard/B/OpportunityDetail?opportunityId=2")
    assert not rollup._urls(a) & rollup._urls(b)
    assert rollup._clean("https://boards.greenhouse.io/a/jobs/1?gh_src=x&utm_source=indeed") == "https://boards.greenhouse.io/a/jobs/1"


def test_resolve_skips_postings_out_of_the_area_once_fetched(filters):
    def to(where):
        def fetch(j):
            j.location, j.description = where, "full posting"
        return fetch
    posts = [post("workday:TD", "3 Locations", "https://x/1", description="", fetch=to("Kelowna, BC; Victoria, BC")),
             post("workday:TD", "2 Locations", "https://x/2", description="", fetch=to("Burnaby, BC; Toronto, ON")),
             post("workday:TD", "4 Locations", "https://x/3", description="", fetch=to("Calgary, AB"))]
    g = rollup.Group("k", posts, rollup.in_area(posts, filters))
    g.resolve(lambda j: j.fetch(), filters)
    assert g.job.url == "https://x/2"
    assert g.others() == []  # the other two turned out to be out of the area
    mem = g.memory()
    assert set(mem["u"]) == {rollup.pid(p) for p in posts} and mem["l"] == ["burnaby", "toronto"]


def test_nothing_in_the_area_remembers_only_what_took_a_fetch(filters):
    p = post("workday:TD", "3 Locations", "https://x/1", description="", fetch=lambda j: setattr(j, "location", "Calgary, AB"))
    g = rollup.Group("k", [p], rollup.in_area([p], filters))
    assert g.resolve(lambda j: j.fetch(), filters) is None
    assert g.memory() == {"u": [rollup.pid(p)]}


def test_new_places_for_a_tracked_job(filters):
    old = [post("workday:RBC", "Vancouver, BC", "https://x/job/1")]
    prev = rollup.first_memory(old, filters)
    later = old + [post("workday:RBC", "Richmond, BC", "https://x/job/5"),     # new branch
                   post("workday:RBC", "Vancouver, BC", "https://x/job/6"),    # second opening, same city
                   post("linkedin", "Vancouver, BC", "https://www.linkedin.com/jobs/view/7"),  # board copy
                   post("indeed", "Delta, BC", "https://ca.indeed.com/viewjob?jk=z")]           # new place, board only
    fresh = rollup.unseen(later, prev)
    assert len(fresh) == 4
    changes = rollup.branch_changes(prev, fresh, filters)
    assert [(p.location, new) for p, new in changes] == [("Richmond, BC", True), ("Vancouver, BC", False), ("Delta, BC", True)]
    link = rollup.link(changes[0][0], "2026-10-22", True)
    assert link == {"u": "https://x/job/5", "l": "Richmond, BC", "s": "workday", "d": "2026-10-22", "new": True}


def test_first_memory_flags_nothing_later(filters):
    posts = [post("workday:RBC", "Vancouver, BC", "https://x/job/1"), post("indeed", "Surrey, BC", "https://ca.indeed.com/viewjob?jk=1")]
    prev = rollup.first_memory(posts, filters)
    assert rollup.unseen(posts, prev) == []


def test_merge_ids_caps_and_keeps_newest():
    ids = rollup.merge_ids([str(i) for i in range(rollup.MAX_IDS)], ["new"])
    assert len(ids) == rollup.MAX_IDS and ids[-1] == "new" and "0" not in ids


def test_linkedin_remote_results_must_say_remote(filters):
    from radar.sources import boards

    def remote_result(where, url, text):
        j = post("linkedin", where + " (Remote)", url, description="")
        j.remote, j.remote_unverified = True, True
        j.fetch = lambda j=j: (setattr(j, "description", text), boards.confirm_remote(j))
        return j
    on_site = remote_result("Brantford, Ontario, Canada", "https://www.linkedin.com/jobs/view/1", "Join our branch team in Brantford.")
    remote = remote_result("Toronto, Ontario, Canada", "https://www.linkedin.com/jobs/view/2", "This role is fully remote across Canada.")
    local = remote_result("Burnaby, British Columbia, Canada", "https://www.linkedin.com/jobs/view/3", "In our Burnaby office.")
    cands = rollup.in_area([on_site, remote, local], filters)
    assert [c.location_deferred for c in cands] == [False, True, True]  # Burnaby is in the area either way
    for c in cands:
        g = rollup.Group("k", [c], [c])
        g.resolve(lambda j: j.fetch(), filters)
        c.kept = g.job is not None
    assert (local.kept, on_site.kept, remote.kept) == (True, False, True)
    assert on_site.location == "Brantford, Ontario, Canada" and not on_site.remote


def test_a_remote_result_whose_posting_cannot_be_fetched_is_kept(filters):
    j = post("linkedin", "Toronto, Ontario, Canada (Remote)", "https://www.linkedin.com/jobs/view/5", description="")
    j.remote, j.remote_unverified = True, True
    j.fetch = lambda: None  # LinkedIn refused the page
    g = rollup.Group("k", [j], rollup.in_area([j], filters))
    assert g.resolve(lambda x: x.fetch(), filters) is j  # benefit of the doubt (its text is retried next run)


def remote_copy(where, url):
    j = post("linkedin", where + " (Remote)", url)
    j.remote = True
    return j


def test_a_remote_job_reposted_under_other_cities_is_not_a_new_place(filters):
    first = [remote_copy("Toronto, Ontario, Canada", "https://www.linkedin.com/jobs/view/1")]
    prev = rollup.first_memory(first, filters)
    later = [remote_copy("Alberta, Canada", "https://www.linkedin.com/jobs/view/2"),
             remote_copy("Les Escoumins, Quebec, Canada", "https://www.linkedin.com/jobs/view/3")]
    assert rollup.branch_changes(prev, later, filters) == []


def test_only_in_area_places_or_first_remote_count_as_new(filters):
    prev = rollup.first_memory([post("workday:BDO", "Vancouver, BC", "https://bdo/1")], filters)
    national = post("workday:BDO", "Toronto, ON; Halifax, NS", "https://bdo/2")       # elsewhere: quiet
    remote = remote_copy("Calgary, Alberta, Canada", "https://www.linkedin.com/jobs/view/4")  # first remote
    surrey = post("indeed", "Surrey, BC", "https://ca.indeed.com/viewjob?jk=5")          # new in-area place
    changes = rollup.branch_changes(prev, [national, remote, surrey], filters)
    assert [(p.url, new) for p, new in changes] == [("https://bdo/2", False), ("https://www.linkedin.com/jobs/view/4", True),
                                                   ("https://ca.indeed.com/viewjob?jk=5", True)]


def test_remote_copies_are_listed_once_with_the_row(filters):
    posts = [post("workday:RBC", "Vancouver, BC", "https://x/job/1"),
             remote_copy("Alberta, Canada", "https://www.linkedin.com/jobs/view/2"),
             remote_copy("Quebec, Canada", "https://www.linkedin.com/jobs/view/3")]
    g = rollup.Group("k", posts, rollup.in_area(posts, filters))
    g.resolve(lambda j: None, filters)
    assert [p.url for p in g.others()] == ["https://www.linkedin.com/jobs/view/2"]
    assert "remote" in g.memory()["l"]


def test_jobs_remembered_before_remote_was_a_place_are_not_flagged_again(filters):
    old_memory = {"u": ["x"], "l": ["toronto", "vancouver"]}  # Toronto could only have come from a remote copy
    later = [remote_copy("Alberta, Canada", "https://www.linkedin.com/jobs/view/9")]
    assert rollup.branch_changes(old_memory, later, filters) == []
    in_area_only = {"u": ["x"], "l": ["burnaby", "vancouver"]}
    assert [new for _, new in rollup.branch_changes(in_area_only, later, filters)] == [True]
