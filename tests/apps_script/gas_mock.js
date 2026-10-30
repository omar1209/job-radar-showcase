// A small stand-in for the Google Apps Script services Code.gs uses, so it can be tested with Node.
// Only what Code.gs calls is implemented; behaviour follows the real services where it matters
// (setValues can't write past the last row, rich text keeps its links, formulas start with "=").
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const CODE = path.join(__dirname, '..', '..', 'apps_script', 'Code.gs');

function richText(text, links) {
  return {text: text, links: links || [], getText() { return this.text; }};
}

class Sheet {
  constructor(name) {
    this.name = name; this.rows = []; this.rich = {}; this.maxRows = 1000; this.maxCols = 26; this.hidden = false; this.hiddenCols = new Set();
  }
  getName() { return this.name; }
  setName(n) { this.ss.rename(this, n); return this; }
  getLastRow() { return this.rows.length; }
  getMaxRows() { return Math.max(this.maxRows, this.rows.length); }
  insertRowsAfter(after, n) { this.maxRows = this.getMaxRows() + n; }
  getMaxColumns() { return this.maxCols; }
  insertColumnsAfter(after, n) { this.maxCols += n; }
  appendRow(r) {  // like Sheets, appendRow grows the sheet as needed
    this.rows.push(r.slice()); this.maxRows = Math.max(this.maxRows, this.rows.length); this.maxCols = Math.max(this.maxCols, r.length);
  }
  insertRowAfter(n) { this.rows.splice(n, 0, []); this._shiftRich(n + 1, 1); }
  deleteRow(n) { this.rows.splice(n - 1, 1); this._shiftRich(n, -1); }
  deleteRows(start, n) { this.rows.splice(start - 1, n); this._shiftRich(start, -n); }
  _shiftRich(from, by) {
    const out = {};
    for (const k of Object.keys(this.rich)) {
      const [r, c] = k.split(',').map(Number);
      if (by < 0 && r >= from && r < from - by) continue;
      out[(r >= from ? r + by : r) + ',' + c] = this.rich[k];
    }
    this.rich = out;
  }
  clear() { this.rows = []; this.rich = {}; }
  hideSheet() { this.hidden = true; }
  hideColumns(c) { this.hiddenCols.add(c); }
  setFrozenRows() {} setColumnWidth() {} setConditionalFormatRules() {}
  setRowHeightsForced(a, b, h) { this.forced = [a, b, h]; }
  getRange(r, c, nr, nc) {
    const range = new Range(this, r, c, nr || 1, nc || 1);
    if (c + (nc || 1) - 1 > this.maxCols) throw new Error('The coordinates of the range are outside the dimensions of the sheet.');
    return range;
  }
  cell(r, c) { return (this.rows[r - 1] || [])[c - 1]; }
}

class Range {
  constructor(sh, r, c, nr, nc) { Object.assign(this, {sh, r, c, nr, nc}); }
  _check() {
    if (this.r + this.nr - 1 > this.sh.getMaxRows() || this.c + this.nc - 1 > this.sh.getMaxColumns()) {
      throw new Error('The coordinates of the range are outside the dimensions of the sheet.');
    }
  }
  _each(fn) {
    const out = [];
    for (let i = 0; i < this.nr; i++) {
      const row = [];
      for (let j = 0; j < this.nc; j++) row.push(fn(this.r + i, this.c + j, i, j));
      out.push(row);
    }
    return out;
  }
  getValues() {
    this._check();
    return this._each((r, c) => {
      const v = this.sh.cell(r, c);
      return v === undefined ? '' : (typeof v === 'string' && v[0] === '=' ? '(formula)' : v);
    });
  }
  getValue() { return this.getValues()[0][0]; }
  getFormulas() { return this._each((r, c) => { const v = this.sh.cell(r, c); return typeof v === 'string' && v[0] === '=' ? v : ''; }); }
  getRichTextValues() { return this._each((r, c) => this.sh.rich[r + ',' + c] || richText(String(this.sh.cell(r, c) || ''))); }
  _set(r, c, v) {
    while (this.sh.rows.length < r) this.sh.rows.push([]);
    this.sh.rows[r - 1][c - 1] = v;
  }
  setValues(v) {
    this._check();
    v.forEach((row, i) => row.forEach((x, j) => { this._set(this.r + i, this.c + j, x); delete this.sh.rich[(this.r + i) + ',' + (this.c + j)]; }));
    return this;
  }
  setValue(x) { return this.setValues([[x]]); }
  setRichTextValues(v) {
    this._check();
    v.forEach((row, i) => row.forEach((x, j) => { this._set(this.r + i, this.c + j, x.text); this.sh.rich[(this.r + i) + ',' + (this.c + j)] = x; }));
    return this;
  }
  setRichTextValue(x) { return this.setRichTextValues([[x]]); }
  sort(spec) {
    const sh = this.sh, start = this.r - 1;
    const body = sh.rows.slice(start, start + this.nr).map((row, i) => ({row, rich: this._richRow(this.r + i)}));
    body.sort((a, b) => {
      for (const s of spec) {
        const A = a.row[s.column - 1], B = b.row[s.column - 1];
        const x = A === undefined || A === '' ? -Infinity : +A === +A && typeof A !== 'string' ? +A : A;
        const y = B === undefined || B === '' ? -Infinity : +B === +B && typeof B !== 'string' ? +B : B;
        if (x < y) return s.ascending ? -1 : 1;
        if (x > y) return s.ascending ? 1 : -1;
      }
      return 0;
    });
    body.forEach((b, i) => {
      sh.rows[start + i] = b.row;
      for (const k of Object.keys(sh.rich)) if (Number(k.split(',')[0]) === this.r + i) delete sh.rich[k];
      for (const c of Object.keys(b.rich)) sh.rich[(this.r + i) + ',' + c] = b.rich[c];
    });
  }
  _richRow(r) {
    const out = {};
    for (const k of Object.keys(this.sh.rich)) { const [rr, c] = k.split(','); if (Number(rr) === r) out[c] = this.sh.rich[k]; }
    return out;
  }
  setFontWeight() { return this; } setFontSize() { return this; } setBackground() { return this; }
  setDataValidation() { return this; } setWrapStrategy() { return this; }
}

class Spreadsheet {
  constructor() { this.sheets = {}; }
  getSheetByName(n) { return this.sheets[n] || null; }
  insertSheet(n) { const sh = new Sheet(n); sh.ss = this; this.sheets[n] = sh; return sh; }
  getSheets() { return Object.values(this.sheets); }
  deleteSheet(sh) { delete this.sheets[sh.name]; }
  rename(sh, n) { delete this.sheets[sh.name]; sh.name = n; this.sheets[n] = sh; }
  getUrl() { return 'https://docs.google.com/spreadsheets/d/test'; }
}

// Utilities.formatDate for the patterns Code.gs uses.
function formatDate(d, tz, pattern) {
  const parts = {};
  new Intl.DateTimeFormat('en-US', {timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit',
    minute: '2-digit', hour12: false, weekday: 'short'}).formatToParts(new Date(d)).forEach(p => { parts[p.type] = p.value; });
  const H = Number(parts.hour) % 24, months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const map = {
    yyyy: parts.year, MMM: months[Number(parts.month) - 1], MM: parts.month, dd: parts.day, d: String(Number(parts.day)),
    EEE: parts.weekday, H: String(H), h: String(H % 12 || 12), mm: parts.minute, a: H < 12 ? 'AM' : 'PM',
    u: String(days.indexOf(parts.weekday) + 1)
  };
  return pattern.replace(/yyyy|MMM|MM|dd|EEE|mm|d|H|h|a|u/g, t => map[t]);
}

function chain() {
  const p = new Proxy(function () {}, {get: (t, k) => (k === 'build' ? () => ({}) : () => p), apply: () => p});
  return p;
}

/**
 * Loads Code.gs into a fresh context. opts.now freezes the clock (ISO string), opts.fetch(url, options)
 * answers UrlFetchApp calls with {code, body, headers}.
 */
function load(opts) {
  opts = opts || {};
  const ss = new Spreadsheet(), mails = [], props = Object.assign({SHARED_SECRET: 's3cret'}, opts.props || {}), fetches = [];
  const RealDate = Date;
  let now = opts.now ? new RealDate(opts.now).getTime() : RealDate.now();
  class FakeDate extends RealDate {
    constructor(...a) { if (a.length) super(...a); else super(now); }
    static now() { return now; }
  }
  const ctx = {
    SpreadsheetApp: {
      getActive: () => ss, newDataValidation: chain, newConditionalFormatRule: chain, WrapStrategy: {CLIP: 1},
      newRichTextValue: () => {
        const v = {text: '', links: []};
        const b = {setText(t) { v.text = t; return b; }, setLinkUrl(s, e, u) { v.links.push([s, e, u]); return b; },
          build() { return richText(v.text, v.links.slice()); }};
        return b;
      }
    },
    PropertiesService: {getScriptProperties: () => ({
      getProperty: k => (k in props ? props[k] : null), setProperty: (k, v) => { props[k] = String(v); },
      deleteProperty: k => { delete props[k]; }
    })},
    ScriptApp: {getProjectTriggers: () => [], newTrigger: chain, deleteTrigger() {}, WeekDay: {MONDAY: 1}},
    LockService: {getScriptLock: () => ({waitLock() {}, releaseLock() {}})},
    ContentService: {createTextOutput: t => ({setMimeType() { return JSON.parse(t); }}), MimeType: {JSON: 1}},
    MailApp: {sendEmail: m => mails.push(m)},
    Session: {getActiveUser: () => ({getEmail: () => 'me@example.com'}), getScriptTimeZone: () => 'America/Vancouver'},
    Utilities: {
      getUuid: () => 'uuid', formatDate,
      base64Decode: t => Array.from(Buffer.from(t, 'base64')),
      newBlob: b => ({getDataAsString: () => Buffer.from(b.map(x => x & 255)).toString('utf8')})
    },
    UrlFetchApp: {fetch: (u, o) => {
      fetches.push({url: u, options: o});
      const r = (opts.fetch || (() => ({code: 200, body: '{}'})))(u, o) || {};
      return {getResponseCode: () => r.code || 200, getContentText: () => r.body || '', getHeaders: () => r.headers || {}};
    }},
    GmailApp: opts.gmail || {},
    Logger: {log() {}},
    Date: FakeDate, Math, JSON, Object, Array, String, Number, RegExp, Error, isNaN, parseInt, Buffer, console
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(CODE, 'utf8'), ctx, {filename: 'Code.gs'});
  return {
    ctx, ss, mails, props, fetches,
    setNow: iso => { now = new RealDate(iso).getTime(); },
    post: body => ctx.doPost({postData: {contents: JSON.stringify(Object.assign({secret: 's3cret'}, body))}}),
    col: n => ctx.JOB_COLS.indexOf(n),
    rows: tab => ss.getSheetByName(tab).rows.slice(1)
  };
}

module.exports = {load};
