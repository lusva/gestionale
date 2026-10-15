// Run with: node --test tests/app.test.cjs
// Exercise the shipped event handlers in an isolated VM. The DOM doubles expose
// only the browser operations used by these handlers; no frontend dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'app.js'), 'utf8');

function section(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, `Missing application section: ${start}`);
  return source.slice(from, to);
}

class EventTargetDouble {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, callback) {
    const callbacks = this.listeners.get(type) || [];
    callbacks.push(callback);
    this.listeners.set(type, callbacks);
  }
  async dispatchEvent(event) {
    for (const callback of this.listeners.get(event.type) || []) await callback(event);
  }
}

class ElementDouble extends EventTargetDouble {
  constructor(tagName, attributes = {}) {
    super();
    this.tagName = tagName.toUpperCase();
    this.attrs = {...attributes};
    this.dataset = {};
    this.children = [];
    this.parentNode = null;
    this.disabled = false;
  }
  get id() { return this.attrs.id; }
  get attributes() { return Object.entries(this.attrs).map(([name, value]) => ({name, value})); }
  getAttribute(name) { return this.attrs[name] ?? null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; }
  matches(selector) {
    if (selector.includes(',')) return selector.split(',').some(part => this.matches(part.trim()));
    if (selector === '[data-submit-disabled]') return 'submitDisabled' in this.dataset;
    if (selector === '[aria-busy="true"]') return this.attrs['aria-busy'] === 'true';
    if (selector === 'form[data-inviato]') return this.tagName === 'FORM' && 'inviato' in this.dataset;
    if (selector === 'form') return this.tagName === 'FORM';
    if (selector === 'button:not([type])') return this.tagName === 'BUTTON' && !('type' in this.attrs);
    if (selector === 'button[type="submit"]') return this.tagName === 'BUTTON' && this.attrs.type === 'submit';
    if (selector === 'input[type="submit"]') return this.tagName === 'INPUT' && this.attrs.type === 'submit';
    if (selector.startsWith('#')) return this.id === selector.slice(1);
    throw new Error(`Unsupported DOM selector in test double: ${selector}`);
  }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [
      ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector),
    ]);
  }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  replaceWith(replacement) {
    const index = this.parentNode.children.indexOf(this);
    this.parentNode.children[index] = replacement;
    replacement.parentNode = this.parentNode;
    this.parentNode = null;
  }
}

class FormDouble extends ElementDouble {
  constructor() { super('form', {method: 'post'}); this.target = ''; }
}

function formsHarness() {
  const document = new ElementDouble('document');
  const window = new EventTargetDouble();
  const timers = [];
  const context = vm.createContext({
    document, window, HTMLFormElement: FormDouble,
    caricamento: {avvia() {}, ferma() {}},
    setTimeout(callback, delay) { timers.push({callback, delay}); },
  });
  vm.runInContext(section('function sbloccaForm(root)', '// ---- Rotellina'), context);
  const pageshow = source.split('\n').find(line => line.startsWith("window.addEventListener('pageshow'"));
  assert.ok(pageshow, 'The BFCache pageshow handler must exist');
  vm.runInContext(pageshow, context);
  const form = document.appendChild(new FormDouble());
  const buttons = [
    form.appendChild(new ElementDouble('button', {type: 'submit'})),
    form.appendChild(new ElementDouble('button')),
    form.appendChild(new ElementDouble('input', {type: 'submit'})),
  ];
  const submit = async (extra = {}) => {
    const event = {type: 'submit', target: form, submitter: buttons[0], defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; }, ...extra};
    await document.dispatchEvent(event);
    return event;
  };
  const flush = delay => {
    const due = timers.filter(timer => timer.delay === delay);
    due.forEach(timer => timers.splice(timers.indexOf(timer), 1));
    due.forEach(timer => timer.callback());
  };
  return {document, window, form, buttons, submit, flush};
}

test('POST blocks repeat submission, then the timeout restores every submit button', async () => {
  const h = formsHarness();
  await h.submit();
  assert.deepEqual(h.buttons.map(button => button.disabled), [false, false, false],
    'The submitter must retain its value until the browser serializes the form');
  h.flush(0);
  assert.deepEqual(h.buttons.map(button => button.disabled), [true, true, true]);
  assert.equal((await h.submit()).defaultPrevented, true);
  h.flush(15000);
  assert.deepEqual(h.buttons.map(button => button.disabled), [false, false, false]);
  assert.equal(h.form.dataset.inviato, undefined);
  assert.equal((await h.submit()).defaultPrevented, false);
});

test('restoring a page from BFCache permits an immediate new submission', async () => {
  const h = formsHarness();
  await h.submit();
  h.flush(0);
  await h.window.dispatchEvent({type: 'pageshow', persisted: true});
  assert.deepEqual(h.buttons.map(button => button.disabled), [false, false, false]);
  assert.equal(h.form.dataset.inviato, undefined);
  assert.equal((await h.submit()).defaultPrevented, false,
    'Restored pages must not wait for the old 15-second timer');
});

test('unlock preserves buttons that were disabled before submission', async () => {
  const h = formsHarness();
  h.buttons[2].disabled = true;
  await h.submit();
  h.flush(0);
  h.flush(15000);
  assert.deepEqual(h.buttons.map(button => button.disabled), [false, false, true]);
  for (const button of h.buttons.slice(0, 2)) {
    assert.equal(button.dataset.submitDisabled, undefined);
    assert.equal(button.getAttribute('aria-busy'), null);
  }
});

test('cancelled submits and HTMX forms keep their original controls available', async () => {
  for (const kind of ['cancelled', 'htmx']) {
    const h = formsHarness();
    if (kind === 'htmx') h.form.setAttribute('hx-post', '/attivita/1/completa/');
    await h.submit({defaultPrevented: kind === 'cancelled'});
    h.flush(0);
    assert.deepEqual(h.buttons.map(button => button.disabled), [false, false, false]);
    assert.equal(h.form.dataset.inviato, undefined);
  }
});

function pipelineHarness(response) {
  const document = new EventTargetDouble();
  const body = new ElementDouble('body');
  document.body = body;
  const old = body.appendChild(new ElementDouble('div', {id: 'pipeline-results'}));
  old.appendChild(new ElementDouble('div', {id: 'pipeline-board'}));
  const replacement = new ElementDouble('div', {id: 'pipeline-results'});
  replacement.appendChild(new ElementDouble('div', {id: 'pipeline-board'}));
  const card = {dataset: {oppId: '12', moveUrl: '/opportunita/12/sposta/'}};
  const column = {dataset: {stadio: 'proposta'}, classList: {remove() {}}};
  document.getElementById = id => body.querySelectorAll(`#${id}`)[0] || null;
  document.querySelector = selector => selector === '.pipe-card.dragging' ? card : null;
  const calls = [], toasts = [], processed = [], changed = [];
  body.addEventListener('crm:changed', event => changed.push(event));
  class DOMParserDouble {
    parseFromString(html, type) {
      assert.equal(type, 'text/html');
      return {getElementById: id => html === '<valid-pipeline>' && id === replacement.id ? replacement : null};
    }
  }
  const context = vm.createContext({
    document, window: {htmx: {process: node => processed.push(node)}},
    location: {origin: 'https://crm.example', search: '?owner=me&q=Alfa'},
    URL, FormData, DOMParser: DOMParserDouble,
    CustomEvent: class { constructor(type, options) { this.type = type; Object.assign(this, options); } },
    csrfToken: () => 'test-csrf-token',
    toast: (message, options) => toasts.push({message, ...options}),
    fetch: async (url, options) => {
      calls.push({url, ...options});
      if (response instanceof Error) throw response;
      return response;
    },
  });
  vm.runInContext(section('// ---- Pipeline drag & drop', '// ---- Filter chip dropdown'), context);
  const drop = () => document.dispatchEvent({
    type: 'drop', target: {closest: selector => selector === '.pipe-col' ? column : null},
    dataTransfer: {getData: () => '12'}, preventDefault() {},
  });
  return {body, old, replacement, calls, toasts, processed, changed, drop};
}

for (const status of [403, 500]) {
  test(`a failed pipeline request (${status}) preserves the original board`, async () => {
    const h = pipelineHarness({ok: false, status, redirected: false});
    await h.drop();
    assert.equal(h.body.children[0], h.old);
    assert.equal(h.old.getAttribute('aria-busy'), null);
    assert.equal(h.toasts.at(-1).level, 'error');
    assert.equal(h.changed.length, 0);
    assert.equal(h.processed.length, 0);
    await h.drop();
    assert.equal(h.calls.length, 2, 'A failed request must permit retrying');
  });
}

test('an expired session redirect preserves the board instead of injecting the login page', async () => {
  const h = pipelineHarness({ok: true, status: 200, redirected: true});
  await h.drop();
  assert.equal(h.body.children[0], h.old);
  assert.match(h.toasts.at(-1).message, /Sessione scaduta/);
  assert.equal(h.changed.length, 0);
});

test('unexpected successful HTML leaves the board intact and shows an error', async () => {
  const h = pipelineHarness({ok: true, redirected: false, text: async () => '<unrelated-page>'});
  await h.drop();
  assert.equal(h.body.children[0], h.old);
  assert.equal(h.toasts.at(-1).level, 'error');
  assert.equal(h.changed.length, 0);
});

test('successful drag replaces the board once, preserves filters and refreshes integrations', async () => {
  const h = pipelineHarness({ok: true, redirected: false, text: async () => '<valid-pipeline>'});
  await h.drop();
  assert.equal(h.body.children[0], h.replacement);
  assert.equal(h.old.parentNode, null);
  assert.equal(h.body.querySelectorAll('#pipeline-results').length, 1);
  assert.equal(h.body.querySelectorAll('#pipeline-board').length, 1);
  assert.equal(h.calls[0].url.pathname, '/opportunita/12/sposta/');
  assert.equal(h.calls[0].url.search, '?owner=me&q=Alfa');
  assert.equal(h.calls[0].method, 'POST');
  assert.equal(h.calls[0].body.get('stadio'), 'proposta');
  assert.equal(h.calls[0].headers['X-CSRFToken'], 'test-csrf-token');
  assert.deepEqual(h.processed, [h.replacement]);
  assert.equal(h.toasts.at(-1).level, 'success');
  assert.equal(h.changed.length, 1);
});
