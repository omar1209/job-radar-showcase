"""Careers-site readers, on small samples shaped like the real pages (no network)."""
import json

from radar.sources import ats, more_ats


class Resp:
    def __init__(self, text="", data=None):
        self.text, self._data, self.status_code = text, data, 200

    def json(self):
        return self._data if self._data is not None else json.loads(self.text)

    def raise_for_status(self):
        pass


def test_icims(monkeypatch):
    card = ('<li class="iCIMS_JobCardItem"><div class="title"><a href="https://careers-vch.icims.com/jobs/{id}/{slug}/job?in_iframe=1" '
            'class="iCIMS_Anchor"><h3>{title}</h3></a></div><div><span class="sr-only field-label">City</span> '
            '<span class="">{city}</span></div></li>')
    page = "".join(card.format(id=i, slug="financial-analyst", title="Financial Analyst &amp; Planner", city="Vancouver")
                   for i in range(3))
    detail = '<div class="iCIMS_JobContent"><div><h2>Overview</h2><p>Analyze <b>budgets</b>.</p></div></div>'
    urls = []
    monkeypatch.setattr(more_ats, "get", lambda url, **kw: urls.append(url) or Resp(detail if "/jobs/0/" in url else page))
    jobs = more_ats.icims({"name": "Vancouver Coastal Health", "host": "careers-vch.icims.com", "province": "BC"})
    assert len(jobs) == 3 and len(urls) == 1  # fewer than 50 cards: no second page
    j = jobs[0]
    assert (j.company, j.title, j.location, j.source) == ("Vancouver Coastal Health", "Financial Analyst & Planner",
                                                          "Vancouver, BC", "icims:Vancouver Coastal Health")
    j.fetch()
    assert "Analyze budgets" in j.description


def test_jibe_sends_no_accept_language(monkeypatch):
    seen = {}
    data = {"jobs": [{"data": {"slug": "R123", "title": "Data Analyst", "full_location": "Surrey, BC, Canada", "state": "BC",
                               "posted_date": "2026-10-20T08:00:00+0000", "description": "<p>Do analysis</p>",
                               "qualifications": "<ul><li>SQL</li></ul>", "location_type": "HYBRID"}}]}

    def fake_get(url, headers=None, **kw):
        seen["headers"] = headers
        return Resp(data=data)
    monkeypatch.setattr(more_ats, "get", fake_get)
    (j,) = more_ats.jibe({"name": "Fraser Health", "host": "jobs.fraserhealth.ca"})
    assert seen["headers"]["Accept-Language"] is None  # Jibe filters postings by it
    assert (j.url, j.posted, j.location) == ("https://jobs.fraserhealth.ca/jobs/R123", "2026-10-20", "Surrey, BC, Canada")
    assert "Do analysis" in j.description and "- SQL" in j.description


def test_ukg(monkeypatch):
    data = {"opportunities": [{"Id": "abc-1", "Title": "Junior Analyst", "PostedDate": "2026-10-19T10:00:00Z",
                               "BriefDescription": "Short", "JobLocationType": 2,
                               "Locations": [{"Address": {"City": "Vancouver", "State": {"Name": "British Columbia"},
                                                          "Country": {"Name": "Canada"}}}]}]}
    detail = '<script>var opportunity = {"Title":"Junior Analyst","Description":"<p>Full \\u0026 complete</p>"};</script>'
    monkeypatch.setattr(more_ats, "post", lambda url, **kw: Resp(data=data))
    monkeypatch.setattr(more_ats, "get", lambda url, **kw: Resp(detail))
    (j,) = more_ats.ukg({"name": "MNP", "tenant": "MNP5000MNPL", "board": "b-1"})
    assert j.location == "Vancouver, British Columbia, Canada"
    assert j.url == "https://recruiting.ultipro.ca/MNP5000MNPL/JobBoard/b-1/OpportunityDetail?opportunityId=abc-1"
    j.fetch()
    assert j.description == "Full & complete"


def test_taleo_be_location_field_and_paging(monkeypatch):
    def block(rid, title, fields):
        return ('<h4 class="oracletaleocwsv2-head-title"><a href="https://tre.tbe.taleo.net/tre01/ats/careers/v2/'
                f'viewRequisition?org=SIMOFRAS&amp;cws=37&amp;rid={rid}" class="viewJobLink">{title}</a></h4>'
                + "".join(f'<div tabindex="0" >{f}</div>' for f in fields))
    pages = [block(1, "Research Assistant", ["Faculty of Arts", "Temporary", "Burnaby"]) +
             block(2, "Policy Analyst", ["Vancouver", "Regular", "Finance"]),
             block(3, "Data Coordinator", ["IRP", "Surrey"]), ""]

    class Session:
        def __init__(self):
            self.headers, self.n = {}, 0

        def get(self, url, timeout=None):
            self.n += 1
            return Resp(pages[min(self.n - 1, len(pages) - 1)])
    monkeypatch.setattr(more_ats.requests, "Session", Session)
    jobs = more_ats.taleo_be({"name": "SFU", "host": "tre.tbe.taleo.net", "path": "tre01", "org": "SIMOFRAS", "cws": 37, "province": "BC"})
    assert [(j.title, j.location) for j in jobs] == [("Research Assistant", "Burnaby, BC"), ("Policy Analyst", "Vancouver, BC"),
                                                     ("Data Coordinator", "Surrey, BC")]
    assert "&amp;" not in jobs[0].url


def test_gcjobs_departments_and_external_postings(monkeypatch):
    item = ('<li class="searchResult"><div><strong><a href="/psrs-srfp/applicant/page1800?poster={p}">{t}</a></strong></div>'
            '<div class="tableTable"><div class="tableRow"><div class="tableCell">Closing date: 2026-11-01 <br /> {d} <br /> {l}'
            '</div><div class="tableCell">English essential</div></div></div></li>')
    listing = ("Results 1 - 2 of 1 [ 1 ]" + item.format(p=1, t="Economist", d="National Defence - Canadian Forces Housing Agency",
                                                        l="Vancouver (British Columbia)")
               + item.format(p=2, t="Data Scientist", d="Canada Mortgage and Housing Corporation", l=""))
    elsewhere = ('<h1>You will leave the <abbr>GC</abbr> Jobs Web site </h1><p>Select the link below</p>'
                 '<p><a href="https://careers.cmhc-schl.gc.ca/job/Ottawa-Data-Scientist/605969717/">Data Scientist</a></p>')
    cmhc = '<div><span class="jobdescription"><p>Build models.</p></span></div>'

    class FakeGC:
        def __init__(self):
            self.sid = "S"

        def first_results(self):
            return listing

        def two_step(self, page, query):
            return elsewhere if "poster=2" in query else "<p>Economist duties</p>"
    monkeypatch.setattr(more_ats, "_GCSession", FakeGC)
    monkeypatch.setattr(more_ats, "get", lambda url, **kw: Resp(cmhc))
    econ, ds = more_ats.gcjobs()
    assert (econ.company, econ.location, econ.closing) == ("National Defence", "Vancouver (British Columbia)", "2026-11-01")
    assert (ds.company, ds.location) == ("Canada Mortgage and Housing Corporation", "Various locations")
    ds.fetch()
    assert ds.apply_url.startswith("https://careers.cmhc-schl.gc.ca/job/") and ds.description == "Build models."
    econ.fetch()
    assert econ.description == "Economist duties"


def test_successfactors_tile_layout(monkeypatch):
    tile = ('<li class="job-tile"><a class="jobTitle-link fontcolor" href="/job/North-Vancouver-Member-Service-Rep/{id}/">'
            'Member Service Representative</a><div class="section-field date"><span class="jobDate">Oct 20, 2026</span></div>'
            '<div class="section-field multilocation"><span class="section-label" aria-describedby="job-{id}-desktop-'
            'section-multilocation-value"> Other Locations </span><div id="job-{id}-desktop-section-multilocation-value">'
            'North Vancouver, British Columbia, CA, V7M 3H7 </div></div></li>')
    page = "".join(tile.format(id=600000000 + i) for i in range(3)) + \
        '<li class="job-tile"><a class="jobTitle-link" href="/job/Port-Moody-Teller/600000009/">Teller</a></li>'
    monkeypatch.setattr(ats, "get", lambda url, **kw: Resp(page))
    jobs = ats.successfactors({"name": "Coast Capital", "domain": "careers.coastcapitalsavings.com"}, ["BC"], pages=2)
    assert len(jobs) == 4
    # The place as listed, not the URL's first word ("North"); none listed: left for the full posting.
    assert (jobs[0].location, jobs[0].posted) == ("North Vancouver, British Columbia, CA, V7M 3H7", "2026-10-20")
    assert (jobs[3].title, jobs[3].location) == ("Teller", "")


def test_successfactors_table_rows_listed_twice(monkeypatch):
    def row(href, title, place, when):
        cell = f'<span class="jobLocation">{place}</span>'
        return (f'<tr class="data-row"><td class="colTitle"><span class="jobTitle hidden-phone"><a href="{href}" '
                f'class="jobTitle-link">{title}</a></span><div class="jobdetail-phone visible-phone"><span class="jobTitle '
                f'visible-phone"><a class="jobTitle-link" href="{href}">{title}</a></span><span class="jobLocation '
                f'visible-phone">{cell}</span><span class="jobDate visible-phone">{when}</span></div></td>'
                f'<td class="colLocation hidden-phone">{cell}</td><td class="colDate"><span class="jobDate">{when}</span></td></tr>')
    page = (row("/ey/job/Vancouver-Tax-Senior/1442046233/", "Tax Senior", "Vancouver, British Columbia, CA, V6E 4E5", "Oct 27, 2026")
            + row("/ey/job/Toronto-Analyst/1442046234/", "Analyst", 'Toronto, ON, CA <small class="nobr">+2 more&hellip;</small>',
                  "28 Oct 2026"))
    monkeypatch.setattr(ats, "get", lambda url, **kw: Resp(page))
    tax, analyst = ats.successfactors({"name": "EY", "domain": "careers.ey.com"}, ["Vancouver"], pages=1)
    assert (tax.url, tax.location, tax.posted) == ("https://careers.ey.com/ey/job/Vancouver-Tax-Senior/1442046233/",
                                                   "Vancouver, British Columbia, CA, V6E 4E5", "2026-10-27")
    assert (analyst.location, analyst.posted) == ("3 locations", "2026-10-28")  # the full posting names all three


def test_successfactors_posting_page_names_every_place(monkeypatch):
    address = ('<span itemprop="address" itemscope itemtype="http://schema.org/PostalAddress">'
               '<meta itemprop="addressLocality" content="{}"/><meta itemprop="addressRegion" content="{}"/></span>')
    page = ('<meta itemprop="datePosted" content="Tue Oct 27 00:00:00 UTC 2026"/>' + address.format("Toronto", "ON")
            + address.format("Vancouver", "Brit")
            + '<span itemprop="description" class="x"><p>Our purpose</p></span>\n</div>'
            + '<span itemprop="description" class="x"><p>What you will do</p></span>\n</div>')
    monkeypatch.setattr(ats, "get", lambda url, **kw: Resp(page))
    j = ats.Job("successfactors:EY", "EY", "Analyst", "3 locations", "https://careers.ey.com/ey/job/Toronto-Analyst/1/")
    ats._sf_detail(j)()
    assert (j.location, j.posted, j.description) == ("Toronto, ON; Vancouver, BC", "2026-10-27", "Our purpose\n\nWhat you will do")
    j = ats.Job("successfactors:EY", "EY", "Analyst", "Vancouver, BC, CA", "https://careers.ey.com/ey/job/x/2/", posted="2026-10-20")
    ats._sf_detail(j)()
    assert (j.location, j.posted) == ("Vancouver, BC, CA", "2026-10-20")  # a place and date already listed stay


def test_successfactors_newer_layout_reads_its_json_search(monkeypatch):
    sent = []
    data = {"totalJobs": 2, "jobSearchResult": [
        {"response": {"id": "133211", "unifiedStandardTitle": "Senior Manager, Tax &amp; Advisory",
                      "urlTitle": "Senior-Manager%2C-Tax-&amp;-Advisory", "jobLocationShort": ["Vancouver, BC, CAN, V7X 1P4<br/>"],
                      "unifiedStandardStart": "10/1/26", "workModel": ["Hybrid"]}},
        {"response": {"id": "1071", "unifiedStandardTitle": "Analyst", "urlTitle": "Analyst",
                      "jobLocationShort": ["Toronto, ON, CAN<br/>"], "unifiedStandardStart": "10/13/26",
                      "unifiedStandardEnd": "11/2/26", "workModel": ["Remote"]}}]}
    monkeypatch.setattr(ats, "get", lambda url, **kw: Resp("<div id='rmk-jobs-search'></div><script>currentLocale: 'en_US',</script>"))
    monkeypatch.setattr(ats, "post", lambda url, **kw: sent.append((url, kw["json"])) or Resp(data=data))
    tax, analyst = ats.successfactors({"name": "Deloitte", "domain": "careers.deloitte.ca"}, ["BC"])
    assert sent == [("https://careers.deloitte.ca/services/recruiting/v1/jobs", {
        "locale": "en_US", "pageNumber": 0, "sortBy": "date", "keywords": "", "location": "BC", "facetFilters": {},
        "brand": "", "skills": [], "categoryId": 0, "alertId": "", "rcmCandidateId": ""})]  # both listed: no second page
    assert (tax.title, tax.location, tax.posted, tax.closing, tax.remote) == (
        "Senior Manager, Tax & Advisory", "Vancouver, BC, CAN, V7X 1P4", "2026-10-01", "", False)
    assert tax.url == "https://careers.deloitte.ca/job/Senior-Manager%2C-Tax-&-Advisory/133211-en_US/"
    assert (analyst.closing, analyst.remote) == ("2026-11-02", True)


def test_taleo_be_fixed_location_and_closing_date(monkeypatch):
    def block(rid, title, when):
        return ('<h4 class="oracletaleocwsv2-head-title"><a href="https://tre.tbe.taleo.net/tre01/ats/careers/v2/'
                f'viewRequisition?org=Q8Z9AA&amp;cws=37&amp;rid={rid}" class="viewJobLink">{title}</a></h4>'
                f'<div tabindex="0" >City of New Westminster</div><div tabindex="0" >Regular Full-Time</div>'
                f'<div tabindex="0" >{when}</div>')
    pages = [block(1, "Budget Analyst", "12/31/2099") + block(2, "Clerk", "1/5/2020"), ""]

    class Session:
        def __init__(self):
            self.headers, self.n = {}, 0

        def get(self, url, timeout=None):
            self.n += 1
            return Resp(pages[min(self.n - 1, len(pages) - 1)])
    monkeypatch.setattr(more_ats.requests, "Session", Session)
    site = {"name": "City of New Westminster", "host": "tre.tbe.taleo.net", "path": "tre01", "org": "Q8Z9AA", "cws": 37,
            "location": "New Westminster, BC"}
    analyst, clerk = more_ats.taleo_be(site)
    assert (analyst.location, analyst.closing) == ("New Westminster, BC", "2099-12-31")
    assert clerk.closing == ""  # a date already past is not a closing date
    del site["location"]
    analyst, _ = more_ats.taleo_be(site)
    assert analyst.location == "Regular Full-Time"  # without one: the last field that isn't a date


def test_bc_public_service_close_dates(monkeypatch):
    row = ('<tr> <td>Finance</td><td>126000</td><td><a href="/hr/ats/Posting/view/126000">Economist</a></td><td>BCGEU</td>'
           '<td>Hybrid</td><td>Victoria, BC</td><td>10/27/2026</td><td>11/10/2026</td></tr>')
    monkeypatch.setattr(ats, "get", lambda url, **kw: Resp('<table id="jobSearchResultsGrid_table">' + row + "</table>"))
    (j,) = ats.bc_public_service(pages=1)
    assert (j.title, j.posted, j.closing, j.location) == ("Economist", "2026-10-27", "2026-11-10", "Victoria, BC (Hybrid)")


def test_translink(monkeypatch):
    listing = [{"title": "Data Analyst - FTT", "company": "001", "startDate": "2026-10-23", "endDate": "2026-11-09",
                "url": "https://careersconnect.translink.bc.ca/psc/EXT/x?Page=HRS_APP_JBPST_FL&JobOpeningId=20260725"},
               {"title": "Transit Operator", "company": "BCT", "startDate": "2026-10-01", "endDate": "N/A",
                "url": "https://careersconnect.translink.bc.ca/psc/EXT/x?Page=HRS_APP_JBPST_FL&JobOpeningId=20260111"}]
    page = ("<div class='ps_header'>Search Jobs</div><div class='ps_box-scrollarea-row' id='win0divHRS_SCH_PSTDSC_row$0'>"
            "<h2><span class='ps-text' id='HRS_SCH_WRK_DESCR100$0lbl'>Responsibilities</span></h2>"
            "<div class='ps_content-group'><span class='ps_box-value'><p>Build dashboards.</p></span></div></div>"
            "<div class='ps_box-group psc_hidden' id='win0div$ICField41'><DIV class='ps_box-button' "
            "id='win0divHRS_SCH_WRK_HRS_APPLY_PB'><a id='HRS_SCH_WRK_HRS_APPLY_PB'>Apply</a></DIV></div>")
    monkeypatch.setattr(more_ats, "get", lambda url, **kw: Resp(page) if "JobOpeningId" in url else Resp(data=listing))
    analyst, operator = more_ats.translink()
    assert (analyst.company, analyst.location, analyst.posted, analyst.closing) == (
        "TransLink", "Metro Vancouver, BC", "2026-10-23", "2026-11-09")
    assert (operator.company, operator.closing) == ("Coast Mountain Bus Company", "")
    analyst.fetch()
    assert analyst.description.split() == ["Responsibilities", "Build", "dashboards."]  # no page menus, no Apply button
