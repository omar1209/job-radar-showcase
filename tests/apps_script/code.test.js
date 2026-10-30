// Tests for apps_script/Code.gs, run with: node --test tests/apps_script
const test = require('node:test');
const assert = require('node:assert/strict');
const {load} = require('./gas_mock');

const job = (key, score, sheet, extra) => Object.assign({
  key, company: 'RBC', title: 'Research Analyst', location: 'Vancouver, BC', url: 'https://jobs.example/' + key,
  posted: '2026-10-20', source: 'workday', score, verdict: score >= 50 ? 'Apply' : 'Avoid', family: 'AN',
  reason: 'good fit', blockers: '', years: '', keywords: 'SQL', missing: '', sheet, alert: score >= 70, postings: []
}, extra || {});

const run = extra => Object.assign({
  stats: {listed: 100, scored: 3, screened: 2, matches: 2, alerts: 1, new_places: 0, est_cost_usd: 0.12, seconds: 300, board_window_h: 3},
  health: {'workday:RBC': 40, linkedin: 300}, sources: [], source_status: {}, stale: [], discovery: {},
  linkedin: {throttled_queries: 0, retried: 0, still_throttled: 0}, alert_min_score: 70, vol_alert_min_score: 75
}, extra || {});

function fresh(opts) {
  const g = load(opts);
  g.ctx.setup();
  return g;
}

test('setup builds the tabs, and keeps an old-layout Runs tab under another name', () => {
  const g = load({now: '2026-10-22T18:00:00Z'});
  const old = g.ss.insertSheet('Runs');
  old.appendRow(['Time', 'Listed', 'New scored', 'Drafts', 'Est. cost (USD)', 'Seconds', 'Sources with problems']);
  old.appendRow([new Date(), 5, 3, 1, 0.1, 200, '']);
  g.ctx.setup();
  assert.ok(g.ss.getSheetByName('Runs (before Oct 22)'));
  assert.deepEqual(g.ss.getSheetByName('Runs').rows[0], g.ctx.RUNS_COLS);
  assert.ok(g.ss.getSheetByName('SourceLog').hidden);
  assert.ok(g.ss.getSheetByName('Sources'));
  const jobs = g.ss.getSheetByName('Jobs');
  assert.deepEqual(jobs.rows[0], g.ctx.JOB_COLS);
  assert.ok(jobs.hiddenCols.has(g.col('Postings JSON') + 1));
  assert.ok(!jobs.hiddenCols.has(g.col('Other postings') + 1));
});

test('doGet reports the script version Python expects', () => {
  const g = fresh();
  assert.deepEqual(g.ctx.doGet(), {ok: true, version: 2});
});

test('new rows land on their tabs in one write, with links to their other postings', () => {
  const g = fresh();
  const postings = [{u: 'https://jobs.example/k1-surrey', l: 'Surrey, BC', s: 'workday', d: '2026-10-22'},
    {u: 'https://www.linkedin.com/jobs/view/9', l: 'Burnaby, BC', s: 'linkedin', d: '2026-10-22'}];
  const r = g.post({run: run(), jobs: [job('k1', 82, 'Jobs', {postings}), job('k2', 30, 'Screened out', {company: 'TD'}),
    job('k3', 66, 'Jobs', {company: 'BMO', title: 'Banking Advisor'})]});
  assert.deepEqual(r, {ok: true, added: 3, alerts: 1, places: 0});
  const jobs = g.ss.getSheetByName('Jobs');
  assert.deepEqual(g.rows('Jobs').map(x => x[g.col('Score')]), [82, 66]);  // highest first
  const cell = jobs.rich['2,' + (g.col('Other postings') + 1)];
  assert.equal(cell.text, 'Surrey, BC  ·  Burnaby, BC (LinkedIn)');
  assert.deepEqual(cell.links.map(l => l[2]), postings.map(p => p.u));
  assert.equal(cell.text.slice(cell.links[1][0], cell.links[1][1]), 'Burnaby, BC (LinkedIn)');
  assert.deepEqual(JSON.parse(g.rows('Jobs')[0][g.col('Postings JSON')]), postings);
  assert.equal(g.rows('Screened out').length, 1);
  assert.equal(g.mails.length, 1);
  assert.match(g.mails[0].htmlBody, /Also posted: <a href="https:\/\/jobs.example\/k1-surrey">Surrey, BC<\/a>/);
});

test('appending past the end of the sheet adds rows first', () => {
  const g = fresh();
  g.ss.getSheetByName('Jobs').maxRows = 2;
  const r = g.post({jobs: [1, 2, 3, 4].map(i => job('k' + i, 60, 'Jobs', {title: 'Analyst ' + i}))});
  assert.equal(r.added, 4);
  assert.equal(g.rows('Jobs').length, 4);
});

test('the same job is never added twice: same key, or same employer and title', () => {
  const g = fresh();
  g.post({jobs: [job('k1', 82, 'Jobs')]});
  const r = g.post({jobs: [job('k1', 82, 'Jobs'), job('other-key', 80, 'Jobs', {company: 'RBC Inc.'})]});
  assert.equal(r.added, 0);
  assert.equal(g.rows('Jobs').length, 1);
  assert.equal(g.post({secret: 'wrong', jobs: []}).ok, false);
});

test('a tracked job posted in a new place: star, link, and an alert when it matters', () => {
  const g = fresh();
  g.post({jobs: [job('strong', 82, 'Jobs', {alert: false}), job('weak', 40, 'Screened out', {company: 'TD'}),
    job('applied', 75, 'Jobs', {company: 'BMO', alert: false})]});
  const jobs = g.ss.getSheetByName('Jobs');
  const bmo = g.rows('Jobs').findIndex(x => x[g.col('Company')] === 'BMO') + 2;
  jobs.getRange(bmo, g.col('Status') + 1).setValue('Applied');
  g.ctx.onEdit({source: g.ss, range: {getSheet: () => jobs, getColumn: () => g.col('Status') + 1,
    getLastColumn: () => g.col('Status') + 1, getRow: () => bmo}});
  assert.equal(g.rows('Applied').length, 1);
  g.mails.length = 0;
  const place = (u, l, isNew) => ({u, l, s: 'workday', d: '2026-10-23', new: isNew});
  const r = g.post({run: run(), jobs: [], updates: [
    {key: 'strong', postings: [place('https://jobs.example/strong-richmond', 'Richmond, BC', true)]},
    {key: 'weak', postings: [place('https://jobs.example/weak-surrey', 'Surrey, BC', true)]},
    {key: 'applied', postings: [place('https://jobs.example/applied-burnaby', 'Burnaby, BC', true),
      place('https://jobs.example/applied-repost', 'Vancouver, BC', false)]},
    {key: 'not-in-sheet', postings: [place('https://x/1', 'Delta, BC', true)]}]});
  assert.equal(r.places, 2);  // the strong match and the application; not the screened-out one
  const strongCell = jobs.rich['2,' + (g.col('Other postings') + 1)];
  assert.equal(strongCell.text, '★ Richmond, BC');
  const applied = g.ss.getSheetByName('Applied');
  assert.equal(applied.rich['2,' + (g.col('Other postings') + 1)].text, '★ Burnaby, BC  ·  Vancouver, BC');
  assert.equal(JSON.parse(g.rows('Screened out')[0][g.col('Postings JSON')]).length, 1);  // recorded, no alert
  assert.equal(g.mails.length, 1);
  assert.match(g.mails[0].subject, /2 tracked jobs posted in new locations/);
  assert.match(g.mails[0].htmlBody, /you applied: Applied/);
  // Sent again (e.g. a retried upload): nothing changes, no second email.
  g.mails.length = 0;
  assert.equal(g.post({jobs: [], updates: [{key: 'strong', postings: [place('https://jobs.example/strong-richmond', 'Richmond, BC', true)]}]}).places, 0);
  assert.equal(g.mails.length, 0);
});

test('moving a row to Applied keeps its other-postings links', () => {
  const g = fresh();
  g.post({jobs: [job('k1', 82, 'Jobs', {postings: [{u: 'https://x/2', l: 'Surrey, BC', s: 'workday'}]})]});
  g.ctx.applyEmail_(g.ss, {company: 'RBC', job_title: 'Research Analyst', status: 'applied', summary: 'Received', email_date: '2026-10-22'},
    {date: new Date(), id: 'm1'});
  const applied = g.ss.getSheetByName('Applied');
  assert.equal(g.rows('Applied')[0][g.col('Status')], 'Applied');
  assert.deepEqual(applied.rich['2,' + (g.col('Other postings') + 1)].links.map(l => l[2]), ['https://x/2']);
});

test('Runs tab: one line per run, problems spelled out', () => {
  const g = fresh();
  g.post({run: run({health: {'workday:RBC': 'ERROR: 503 Service Unavailable', linkedin: 250},
    linkedin: {throttled_queries: 3, retried: 3, still_throttled: 1}, stale: [{task: 'icims:X', why: 'no postings for 3 days'}],
    stats: {listed: 900, scored: 12, screened: 5, matches: 3, alerts: 2, new_places: 1, est_cost_usd: 0.31, seconds: 340, board_window_h: 3, claude_failed: 2}})});
  const r = g.ss.getSheetByName('Runs').rows[1];
  assert.deepEqual(r.slice(1, 10), [900, 12, 5, 3, 2, 1, 0.31, 340, 3]);
  assert.match(r[10], /workday:RBC: 503 Service Unavailable/);
  assert.match(r[10], /2 Claude calls failed/);
  assert.match(r[10], /LinkedIn rate-limited 1 searches/);
  assert.match(r[10], /1 source\(s\) need attention/);
  assert.ok(Number(g.props.last_run_ok) > 0);
});

test('Sources: per-source numbers add up over the day, best sources first', () => {
  const g = fresh({now: '2026-10-22T18:00:00Z'});
  const src = (label, task, n) => Object.assign({label, task, listed: 0, new: 0, screened: 0, matches: 0, only: 0, alerts: 0, branches: 0, cost: 0}, n);
  const status = {'workday:RBC': {ok: true, note: 'OK'}, linkedin: {ok: true, note: 'OK'}, 'icims:VCH': {ok: false, note: 'failing for 6 runs in a row: 500'}};
  g.post({run: run({source_status: status, sources: [
    src('workday:RBC', 'workday:RBC', {listed: 40, new: 3, screened: 2, matches: 1, only: 1, cost: 0.03}),
    src('linkedin: research', 'linkedin', {listed: 60, new: 5, screened: 4, matches: 2, only: 0, cost: 0.05})]})});
  g.setNow('2026-10-22T19:00:00Z');
  g.post({run: run({source_status: status, discovery: {
    uncovered: [{name: 'Leith Wheeler', matches: 2, last: '2026-10-22', system: 'BambooHR', title: 'Investment Associate'}],
    feeds: [{name: 'Acme', system: 'greenhouse', added: '2026-10-22', why: '1 good match on LinkedIn/Indeed'}]},
  sources: [src('workday:RBC', 'workday:RBC', {listed: 38, new: 1, matches: 1, only: 1, cost: 0.01}),
    src('linkedin: research', 'linkedin', {listed: 70, new: 2, matches: 2, cost: 0.02})]})});
  const log = g.rows('SourceLog');
  assert.equal(log.length, 2);  // one row per source per day
  const rbc = log.find(r => r[1] === 'workday:RBC');
  assert.deepEqual(rbc.slice(3, 9), [40, 4, 2, 2, 2, 0]);  // listed = day's max; the rest are totals
  const sources = g.rows('Sources');
  const table = sources.slice(2);
  assert.equal(table[0][0], 'linkedin: research');  // 4 good matches beats 2
  assert.equal(table[0][1], 'LinkedIn search');
  assert.equal(table[0][6], 4);
  assert.equal(table[1][0], 'workday:RBC');
  assert.equal(table[1][7], 2);  // only found here
  const vch = table.find(r => r[0] === 'icims:VCH');
  assert.equal(vch[11], 'failing for 6 runs in a row: 500');
  const text = sources.map(r => r.join('|')).join('\n');
  assert.match(text, /Leith Wheeler\|2\|2026-10-22\|BambooHR\|Investment Associate/);
  assert.match(text, /Acme\|greenhouse\|2026-10-22/);
});

test('stale sources: emailed when they first need attention, then weekly', () => {
  const g = fresh({now: '2026-10-22T18:00:00Z'});
  const stale = [{task: 'icims:VCH', why: 'no postings for 3 days (it listed 100 before)'}];
  g.post({run: run({stale})});
  assert.equal(g.mails.length, 1);
  assert.match(g.mails[0].subject, /1 source need attention/);
  g.post({run: run({stale})});
  assert.equal(g.mails.length, 1);
  g.setNow('2026-10-30T18:00:00Z');
  g.post({run: run({stale})});
  assert.equal(g.mails.length, 2);
  g.post({run: run({stale: []})});  // recovered: forgotten, so a new failure is reported at once
  assert.deepEqual(JSON.parse(g.props.stale_told), {});
});

// ---------------------------------------------------------------- run scheduler + watchdog
function kick(nowIso, opts) {
  opts = opts || {};
  const calls = [];
  const g = load({now: nowIso, props: Object.assign({GITHUB_TOKEN: 't', ALERT_EMAIL: 'me@example.com'}, opts.props || {}),
    fetch: (u, o) => {
      calls.push(o.method + ' ' + u.split('job-radar')[1]);
      if (opts.status) return {code: opts.status, body: '{"message":"Bad credentials"}'};
      const headers = opts.expiry ? {'github-authentication-token-expiration': opts.expiry} : {};
      if (o.method === 'get' && u.includes('/actions/runs?')) {  // minutes check: one 10-minute run per entry
        const all = Array.from({length: opts.pastRuns || 0}, () => ({run_started_at: '2026-10-20T10:00:00Z', updated_at: '2026-10-20T10:09:30Z'}));
        const page = Number((u.match(/[?&]page=(\d+)/) || [0, 1])[1]);
        return {code: 200, body: JSON.stringify({workflow_runs: all.slice((page - 1) * 100, page * 100)}), headers};
      }
      return o.method === 'get' ? {code: 200, body: JSON.stringify({workflow_runs: opts.last ? [opts.last] : []}), headers}
        : {code: 204, body: '', headers};
    }});
  g.ctx.kickRadar();
  return {calls, mails: g.mails, g};
}
const oldRun = {status: 'completed', created_at: '2026-10-21T07:38:18Z'};

test('kickRadar starts runs in search hours only, never on top of another', () => {
  const dispatch = '/actions/workflows/radar.yml/dispatches';
  assert.ok(kick('2026-10-21T15:15:00Z', {last: oldRun}).calls.includes('post ' + dispatch));  // Wed 8:15am
  assert.ok(!kick('2026-10-21T15:15:00Z', {last: {status: 'completed', created_at: '2026-10-21T14:55:00Z'}}).calls.includes('post ' + dispatch));
  assert.ok(!kick('2026-10-21T15:15:00Z', {last: {status: 'in_progress', created_at: '2026-10-21T13:00:00Z'}}).calls.includes('post ' + dispatch));
  assert.equal(kick('2026-10-21T09:00:00Z', {last: oldRun}).calls.length, 0);  // Wed 2am
  assert.ok(kick('2026-10-24T20:10:00Z', {last: oldRun}).calls.includes('post ' + dispatch));  // Sat 1pm
  assert.equal(kick('2026-10-24T21:10:00Z', {last: oldRun}).calls.length, 0);  // Sat 2pm
  assert.equal(kick('2026-10-22T01:15:00Z', {last: oldRun}).calls.length, 0);  // Wed 6:15pm: not a search hour now
  assert.ok(kick('2026-10-22T02:15:00Z', {last: oldRun}).calls.includes('post ' + dispatch));  // Wed 7:15pm
});

test('kickRadar keeps runs within the 2,000 free GitHub minutes', () => {
  const dispatch = 'post /actions/workflows/radar.yml/dispatches';
  let k = kick('2026-10-21T17:15:00Z', {last: oldRun, pastRuns: 150});  // 1,500 minutes used, Wed 10am
  assert.ok(k.calls.includes(dispatch));
  assert.equal(k.g.props.minutes_used, '1500');
  k = kick('2026-10-21T17:15:00Z', {last: oldRun, pastRuns: 170});  // 1,700 used: 10am isn't a core hour
  assert.ok(!k.calls.includes(dispatch) && k.mails.length === 0);
  k = kick('2026-10-21T16:15:00Z', {last: oldRun, pastRuns: 170});  // 9am is
  assert.ok(k.calls.includes(dispatch));
  k = kick('2026-10-21T16:15:00Z', {last: oldRun, pastRuns: 186});  // 1,860: paused, with one email
  assert.ok(!k.calls.includes(dispatch));
  assert.match(k.mails[0].subject, /paused: free GitHub minutes/);
  assert.ok(k.calls.filter(c => c.includes('/actions/runs?')).length === 2);  // 186 runs: two pages
});

test('watchdog: no successful run for hours -> one email, repeated at most every 6 hours', () => {
  const lastOk = String(new Date('2026-10-21T16:05:00Z').getTime());  // Wed 9:05am
  let k = kick('2026-10-21T17:30:00Z', {last: oldRun, props: {last_run_ok: lastOk}});  // 10:30am: too soon
  assert.equal(k.mails.length, 0);
  k = kick('2026-10-21T19:50:00Z', {last: oldRun, props: {last_run_ok: lastOk}});  // 12:50pm: 3 runs missed
  assert.equal(k.mails.length, 1);
  assert.match(k.mails[0].subject, /no successful run since Wed 9:05 AM/);
  k = kick('2026-10-21T20:50:00Z', {last: oldRun, props: {last_run_ok: lastOk, warned_noRun: String(new Date('2026-10-21T19:50:00Z').getTime())}});
  assert.equal(k.mails.length, 0);
  // Overnight gaps are expected: Wed 8:30pm -> Thu 6:50am is no problem.
  k = kick('2026-10-22T13:50:00Z', {props: {last_run_ok: String(new Date('2026-10-22T03:30:00Z').getTime())}});
  assert.equal(k.mails.length, 0);
});

test('watchdog: rejected token and soon-to-expire token', () => {
  let k = kick('2026-10-21T15:15:00Z', {status: 401});
  assert.equal(k.mails.length, 1);
  assert.match(k.mails[0].subject, /GitHub rejected the token/);
  assert.match(k.mails[0].htmlBody, /Fine-grained tokens/);
  k = kick('2026-10-21T15:15:00Z', {last: oldRun, expiry: '2026-10-30 08:00:00 UTC'});
  assert.equal(k.mails.length, 1);
  assert.match(k.mails[0].subject, /token expires in 8 days/);
  k = kick('2026-10-21T15:15:00Z', {last: oldRun, expiry: '2027-01-30 08:00:00 UTC'});
  assert.equal(k.mails.length, 0);
});

// ---------------------------------------------------------------- inbox + weekly report (regression)
test('application emails update the matching row, or start a new Applied row', () => {
  const g = fresh();
  g.post({jobs: [job('k1', 82, 'Jobs'), job('k2', 66, 'Jobs', {company: 'TD Bank', title: 'Personal Banking Associate'})]});
  const msg = {date: new Date(), id: 'm1'};
  g.ctx.applyEmail_(g.ss, {company: 'TD', job_title: 'Personal Banking Associate', status: 'applied', summary: 'Received', email_date: '2026-10-20'}, msg);
  g.ctx.applyEmail_(g.ss, {company: 'Royal Bank of Canada (RBC)', job_title: 'Research Analyst', status: 'interview', summary: 'Invite', email_date: ''}, msg);
  g.ctx.applyEmail_(g.ss, {company: 'Vancity', job_title: 'Member Service Representative', status: 'applied', summary: 'Thanks', email_date: '2026-10-21'}, msg);
  const applied = g.rows('Applied');
  assert.equal(g.rows('Jobs').length, 0);
  assert.deepEqual(applied.map(r => [r[g.col('Company')], r[g.col('Status')], r[g.col('Source')]]).sort(),
    [['RBC', 'Interview', 'workday'], ['TD Bank', 'Applied', 'workday'], ['Vancity', 'Applied', 'email']]);
  // The radar later finds the Vancity job: no duplicate row.
  assert.equal(g.post({jobs: [job('k9', 75, 'Jobs', {company: 'Vancity', title: 'Member Service Representative'})]}).added, 0);
});

test('weekly skill report groups missing keywords with Claude', () => {
  const g = fresh({props: {ANTHROPIC_API_KEY: 'k'}, fetch: () => ({code: 200, body: JSON.stringify({content: [{type: 'text',
    text: JSON.stringify({summary: 'Power BI is the gap.', gaps: [{skill: 'Power BI', count: 1, share_pct: 100, variants: ['Power BI'], suggestion: 'PL-300'}],
      blockers: [], strengths: ['SQL']})}]})})});
  g.post({jobs: [job('k1', 82, 'Jobs', {keywords: 'SQL, Power BI', missing: 'Power BI'})]});
  g.mails.length = 0;
  g.ctx.weeklySkillReport();
  assert.equal(g.mails.length, 1);
  assert.match(g.mails[0].subject, /Power BI 100%/);
  assert.equal(g.rows('Skill gaps')[4][0], 'Power BI');  // below the title, summary and header
});

test('upgrading: an old 25-column sheet takes uploads before setup is run again, and setup finishes the job', () => {
  const g = load();
  const oldCols = g.ctx.JOB_COLS.slice(0, 25);
  ['Jobs', 'Volunteer', 'Applied', 'Screened out'].forEach(n => g.ss.insertSheet(n).appendRow(oldCols));
  g.ss.getSheetByName('Jobs').appendRow(oldCols.map(c => (c === 'Key' ? 'old1' : c === 'Score' ? 77 : c === 'Company' ? 'TD' : '')));
  ['Jobs', 'Volunteer', 'Applied', 'Screened out'].forEach(n => { g.ss.getSheetByName(n).maxCols = 26; });
  const runs = g.ss.insertSheet('Runs');
  runs.appendRow(['Time', 'Listed', 'New scored', 'Drafts', 'Est. cost (USD)', 'Seconds', 'Sources with problems']);
  const r = g.post({run: run({sources: [{label: 'workday:TD', task: 'workday:TD', listed: 9, new: 1, screened: 1, matches: 1, only: 1, alerts: 0, branches: 0, cost: 0.02}]}),
    jobs: [job('new1', 81, 'Jobs', {postings: [{u: 'https://x/2', l: 'Surrey, BC', s: 'workday'}]})],
    updates: [{key: 'old1', postings: [{u: 'https://x/9', l: 'Delta, BC', s: 'workday', new: true}]}]});
  assert.equal(r.ok, true, r.error);
  assert.equal(r.places, 1);
  assert.ok(g.ss.getSheetByName('SourceLog').hidden && g.rows('SourceLog').length === 1);
  g.ctx.setup();
  assert.deepEqual(g.ss.getSheetByName('Jobs').rows[0], g.ctx.JOB_COLS);
  assert.ok(g.ss.getSheetByName('Runs (before ' + g.ctx.Utilities.formatDate(new Date(), 'America/Vancouver', 'MMM d') + ')'));
  assert.deepEqual(g.ss.getSheetByName('Runs').rows[0], g.ctx.RUNS_COLS);
});

// ---------------------------------------------------------------- progress + screening check
test('applications remember when they were sent and how far they got', () => {
  const g = fresh({now: '2026-10-20T18:00:00Z'});
  g.post({jobs: [job('k1', 82, 'Jobs')]});
  const msg = {date: new Date(), id: 'm1'};
  g.ctx.applyEmail_(g.ss, {company: 'RBC', job_title: 'Research Analyst', status: 'applied', summary: 'Received', email_date: '2026-10-20'}, msg);
  g.ctx.applyEmail_(g.ss, {company: 'RBC', job_title: 'Research Analyst', status: 'interview', summary: 'Invite', email_date: '2026-10-24'}, msg);
  g.ctx.applyEmail_(g.ss, {company: 'RBC', job_title: 'Research Analyst', status: 'rejected', summary: 'No', email_date: '2026-10-30'}, msg);
  const r = g.rows('Applied')[0];
  assert.equal(r[g.col('Status')], 'Rejected');
  assert.equal(r[g.col('Furthest stage')], 'Interview');  // a rejection after an interview still counts
  assert.equal(g.ctx.ymd_(r[g.col('Applied on')]), '2026-10-20');
  // You set Offer yourself in the Applied tab.
  const ap = g.ss.getSheetByName('Applied');
  ap.getRange(2, g.col('Status') + 1).setValue('Offer');
  g.ctx.onEdit({source: g.ss, range: {getSheet: () => ap, getColumn: () => g.col('Status') + 1, getLastColumn: () => g.col('Status') + 1, getRow: () => 2, getLastRow: () => 2}});
  assert.equal(g.rows('Applied')[0][g.col('Furthest stage')], 'Offer');
});

test('setup fills in dates and stages for applications made before these columns existed', () => {
  const g = fresh();
  const row = g.ctx.JOB_COLS.map(c => ({Status: 'Rejected', Company: 'TD', Title: 'Analyst', Key: 'old', Source: 'linkedin',
    'Email evidence': '2026-10-15: Interview invite\n2026-10-09: Application received'}[c] || ''));
  g.ss.getSheetByName('Applied').appendRow(row);
  g.ctx.setup();
  const r = g.rows('Applied')[0];
  assert.equal(g.ctx.ymd_(r[g.col('Applied on')]), '2026-10-09');
  assert.equal(r[g.col('Furthest stage')], 'Interview');
});

test('weekly email: your week, interview rates by family, waiting jobs, and the screening check', () => {
  const g = fresh({now: '2026-10-26T15:00:00Z'});
  const day = d => new Date('2026-10-' + d + 'T18:00:00Z');
  const row = o => g.ctx.JOB_COLS.map(c => (c in o ? o[c] : ''));
  const ap = g.ss.getSheetByName('Applied'), so = g.ss.getSheetByName('Screened out'), jb = g.ss.getSheetByName('Jobs');
  for (let i = 0; i < 10; i++) {  // 10 banking applications, 3 with responses (1 interview)
    ap.appendRow(row({Added: day(10 + i), 'Applied on': day(11 + i), Status: i < 3 ? 'Assessment' : 'Applied',
      'Furthest stage': i === 0 ? 'Interview' : i < 3 ? 'Assessment' : 'Applied', Family: 'CL', Source: 'workday', Score: 72, Company: 'Bank' + i, Title: 'Teller'}));
  }
  for (let i = 0; i < 10; i++) {  // 10 analytical applications, none answered; two scored under 60
    ap.appendRow(row({Added: day(12 + i), 'Applied on': day(13 + i), Status: 'Applied', 'Furthest stage': 'Applied',
      Family: 'AN', Source: 'linkedin', Score: i < 2 ? 55 : 78, Company: 'Co' + i, Title: 'Analyst'}));
  }
  ap.appendRow(row({Added: day(5), 'Applied on': day(5), Status: 'Applied', Source: 'email', Company: 'Elsewhere', Title: 'Clerk'}));
  for (let i = 0; i < 3; i++) so.appendRow(row({Added: day(15), Status: 'Not applying', Score: 80 + i, Family: 'CL', Source: 'workday',
    'Why not': 'Hours / schedule', Company: 'Skip' + i, Title: 'Part-time Teller'}));
  jb.appendRow(row({Added: day(10), Status: 'New', Score: 85, Company: 'Waiting Co', Title: 'Credit Analyst', Source: 'workday'}));
  g.mails.length = 0;
  g.ctx.weeklySkillReport();
  const m = g.mails[0];
  assert.match(m.subject, /^Job Radar week: \d+ applied, 1 interview so far/);
  assert.match(m.htmlBody, /<b>21<\/b> in total: 3 responses, 1 interview\./);
  assert.match(m.htmlBody, /Banking \/ client<\/td><td>10<\/td><td>3<\/td><td>30%/);
  assert.match(m.htmlBody, /3 jobs scored 75\+ were skipped, most often for &quot;Hours \/ schedule&quot;/);
  assert.match(m.htmlBody, /Banking \/ client roles get the most responses \(30%\); Analytical the fewest \(0%\)/);
  assert.match(m.htmlBody, /Still waiting:<\/b> Credit Analyst at Waiting Co \(85\)/);
  const tab = g.rows('Progress').map(r => r.join('|')).join('\n');
  assert.match(tab, /Average score of jobs you applied to\|7\d\|of jobs you skipped\|81/);
  assert.match(tab, /under 60\|2\|0\|0%/);
  assert.match(tab, /Part-time Teller\|Skip2\|82\|CL\|Hours \/ schedule/);
  assert.match(tab, /Applied outside the radar\|1\|0\|0%/);
});

test('picking a Why not reason marks the job Not applying and moves it', () => {
  const g = fresh();
  g.post({jobs: [job('k1', 82, 'Jobs')]});
  const jobs = g.ss.getSheetByName('Jobs'), whyCol = g.col('Why not') + 1;
  jobs.getRange(2, whyCol).setValue('Hours / schedule');
  g.ctx.onEdit({source: g.ss, range: {getSheet: () => jobs, getColumn: () => whyCol, getLastColumn: () => whyCol, getRow: () => 2, getLastRow: () => 2}});
  assert.equal(g.rows('Jobs').length, 0);
  const r = g.rows('Screened out')[0];
  assert.deepEqual([r[g.col('Status')], r[g.col('Why not')]], ['Not applying', 'Hours / schedule']);
});

test('pay and closing date land in their columns and in the alert email', () => {
  const g = fresh();
  g.post({run: run(), jobs: [job('k1', 82, 'Jobs', {pay: '$60,000-$70,000 a year', closes: '2026-11-04'}), job('k2', 75, 'Jobs', {title: 'Credit Analyst'})]});
  const rows = g.rows('Jobs');
  assert.deepEqual([rows[0][g.col('Pay')], rows[0][g.col('Closes')]], ['$60,000-$70,000 a year', '2026-11-04']);
  assert.deepEqual([rows[1][g.col('Pay')], rows[1][g.col('Closes')]], ['', '']);
  assert.match(g.mails[0].htmlBody, /posted 2026-10-20 &middot; <b>closes 2026-11-04<\/b> &middot; \$60,000-\$70,000 a year/);
});

test('Runs tab: postings waiting for their full text are spelled out', () => {
  const g = fresh();
  g.post({run: run({linkedin: {throttled_queries: 0, retried: 0, still_throttled: 0, pages_refused: 3, pages_deferred: 12},
    stats: {listed: 900, scored: 12, screened: 5, matches: 3, alerts: 2, new_places: 0, est_cost_usd: 0.1, seconds: 300, board_window_h: 3, pending: 15}})});
  const problems = g.ss.getSheetByName('Runs').rows[1][10];
  assert.match(problems, /LinkedIn refused postings, so 12 wait for the next run/);
  assert.match(problems, /15 jobs waiting for their full posting/);
});
