// Exercise the real browser script with a small DOM and transport fixture.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('English upload, stream, filters, CSV, and historical report remain connected', async () => {
  const staticPath = path.join(__dirname, '..', 'static');
  const html = fs.readFileSync(path.join(staticPath, 'index.html'), 'utf8');
  function element() {
    return {
      hidden: false, checked: false, disabled: false, textContent: '', style: {}, dataset: {},
      children: [], listeners: {}, classList: { add() {}, toggle() {} },
      set innerHTML(value) { this.markup = value; this.children = []; },
      get innerHTML() { return this.markup || ''; },
      addEventListener(type, callback) { this.listeners[type] = callback; },
      appendChild(child) { this.children.push(child); }, scrollIntoView() {},
    };
  }
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], element()]));
  const uploads = [];
  const streams = [];
  const timers = new Map();
  const rows = [
    { Page: 1, 'Code (Reference PDF)': '113640', 'Code (OCR Slips)': '113640',
      'Amount (Reference PDF)': '76,82', 'Amount (OCR Slips)': '76,82',
      'Code Status': 'OK', 'Amount Status': 'OK', 'Overall Status': 'OK', Category: '' },
    { Page: 2, 'Code (Reference PDF)': '113640', 'Code (OCR Slips)': '113840',
      'Amount (Reference PDF)': '76,82', 'Amount (OCR Slips)': '76,82',
      'Code Status': 'MISMATCH', 'Amount Status': 'OK', 'Overall Status': 'ERROR', Category: 'OTHER REGISTRATION' },
  ];
  const context = vm.createContext({
    document: {
      getElementById(id) { assert.ok(nodes.has(id), `Unknown DOM ID: ${id}`); return nodes.get(id); },
      createElement: element,
      querySelectorAll(selector) { assert.equal(selector, '#body-table tr'); return nodes.get('body-table').children; },
    },
    FormData: class extends Map {
      constructor() { super([['payment_slips', 'slips.pdf'], ['reference', 'reference.pdf'], ['dpi', '300']]); }
    },
    EventSource: class {
      static CLOSED = 2;
      constructor(url) { this.url = url; streams.push(this); }
      close() { this.closed = true; }
    },
    setInterval(callback) { const id = timers.size + 1; timers.set(id, callback); return id; },
    clearInterval(id) { timers.delete(id); },
    async fetch(url, options) {
      if (options?.method === 'POST') {
        uploads.push({ url, fields: [...options.body.keys()] });
        return { ok: true, json: async () => ({ job_id: 'sample', message: 'Audit started.' }) };
      }
      if (url === '/api/v1/audits') return { ok: true, json: async () => [] };
      assert.equal(url, '/api/v1/audits/sample/pages');
      return { ok: true, json: async () => rows };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(staticPath, 'app.js'), 'utf8'), context);
  await nodes.get('auditForm').listeners.submit({ preventDefault() {} });
  assert.equal(uploads[0].url, '/api/v1/audits?dpi=300');
  assert.deepEqual(uploads[0].fields, ['payment_slips', 'reference']);
  assert.equal(streams[0].url, '/api/v1/audits/sample/events');
  function emit(event) { streams[0].onmessage({ data: JSON.stringify(event) }); }
  emit({ type: 'start', total_pages: 2, reference_count: 2, dpi: 300 });
  assert.equal(nodes.get('active-resolution').textContent, '300 DPI');
  for (const row of rows) emit({ type: 'page', page: row.Page, row, strategy: '1. Default' });
  assert.equal(nodes.get('ind-processed').textContent, '2/2');
  assert.equal(nodes.get('ind-match-rate').textContent, '50.0%');
  assert.match(nodes.get('explanation-category').innerHTML, /Other registration/);
  nodes.get('mismatch-filter').listeners.change({ target: { checked: true } });
  assert.deepEqual(nodes.get('body-table').children.map(row => row.hidden), [true, false]);
  emit({ type: 'end', total_pages: 2, model: 'Tesseract OCR' });
  assert.equal(nodes.get('link-csv').href, '/api/v1/audits/sample/report.csv');
  assert.equal(streams[0].closed, true);
  assert.equal(timers.size, 0);
  await context.openAudit({ job_id: 'sample', total_pages: 2, created_at: '2026-09-30T10:00:00' });
  assert.match(nodes.get('viewer').innerHTML, /Annotated OCR images are not stored/);
  assert.equal(nodes.get('body-table').children.length, 2);
  context.followAudit('sample');
  streams[1].onmessage({ data: JSON.stringify({ type: 'error', message: 'Could not read PDF.' }) });
  assert.equal(nodes.get('notice').textContent, 'Could not read PDF.');
  assert.equal(streams[1].closed, true);
  assert.equal(timers.size, 0);
});
