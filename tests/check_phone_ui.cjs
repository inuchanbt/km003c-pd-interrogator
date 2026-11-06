const assert = require('node:assert/strict');
const {File} = require('node:buffer');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');
const KPD = require('../phone/codec.js');
const KPDStore = require('../phone/store.js');
require('../phone/samples.js');

const app = readFileSync(join(__dirname, '../phone/app.js'), 'utf8');
const oldPages = readFileSync(join(__dirname, 'fixtures/stock-probe-06-pages.txt'), 'utf8').trim().split(/\r?\n/);
const oldPage = KPD.parsePage(oldPages[1]);
const measurements = JSON.parse(readFileSync(0, 'utf8'));

// Run the page's handlers without a browser dependency in CI.
class Element {
  constructor() {
    this.value = '';
    this.children = [];
    this.textContent = '';
    this.classList = {add() {}, remove() {}, toggle() {}};
  }
  get firstElementChild() { return this.children[0]; }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children = []; }
  scrollIntoView() {}
}

function importer(storage) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  }
  let download, downloadedName, shared;
  element('progressText').append(new Element());
  const document = {
    documentElement: {},
    querySelectorAll: () => [],
    getElementById: element,
    createElement(tag) {
      const node = new Element();
      if (tag === 'a') node.click = () => { downloadedName = node.download; };
      return node;
    },
  };
  const window = {localStorage: storage, location: {hash: ''}, addEventListener() {}};
  const navigator = {
    canShare: ({files}) => files.length === 1 && files[0].type === 'application/json',
    async share({files}) { shared = files[0]; },
  };
  const URL = {createObjectURL(file) { download = file; return 'blob:download'; }};
  runInNewContext(app, {document, window, navigator, URL, KPD, KPDStore, KPDSamples, File, setTimeout() {}});
  return {
    element,
    select(id, value) { element(id).value = value; element(id).onchange(); },
    import(pages) { element('pages').value = pages.join('\n'); element('add').onclick(); },
    async exported() {
      download = shared = null;
      element('save').onclick();
      await element('share').onclick();
      assert.ok(download && shared);
      assert.equal(downloadedName, download.name);
      assert.equal(shared.name, download.name);
      const savedText = await download.text();
      assert.equal(await shared.text(), savedText);
      assert.equal(element('raw').textContent, savedText.trimEnd());
      return JSON.parse(savedText);
    },
  };
}

test('demo downloads and shares keep their provenance across languages and never alter QR history', async () => {
  // The storage entry contains only the original QR pages in the v1 layout.
  const stored = new Map([[KPDStore.STORAGE_KEY, JSON.stringify({
    version: 1, active: oldPage.key, transfers: {[oldPage.key]: {pages: [oldPage.text], updatedAt: 1}},
  })]]);
  const storage = {getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value)};
  const original = storage.getItem(KPDStore.STORAGE_KEY);
  const ui = importer(storage);
  assert.match(ui.element('counter').textContent, /^1 \/ 5 pages$/);

  for (const [type, pages] of Object.entries(KPDSamples)) {
    const expected = {...KPD.assemble(pages).snapshot, demo: true, synthetic: true, sample_type: type};
    ui.select('sample', type);
    for (const [lang, notice] of [['ja', /デモ用の架空データ/], ['en', /demo uses synthetic data/]]) {
      ui.select('language', lang);
      assert.equal(ui.element('badges').children.filter(badge => badge.textContent === 'DEMO').length, 1);
      assert.match(ui.element('notice').textContent, notice);
      assert.deepEqual(await ui.exported(), expected);
      assert.equal(storage.getItem(KPDStore.STORAGE_KEY), original);
    }
  }

  // Resume the saved transfer with a mixture of URL and legacy text pages,
  // out of order and with a duplicate. A successful import leaves the sample.
  ui.select('language', 'ja');
  ui.import([oldPages[4], oldPages[2], oldPage.text, oldPages[0], oldPages[3]]);
  const expected = KPD.assemble(oldPages).snapshot;
  assert.equal(ui.element('sample').value, '');
  assert.doesNotMatch(ui.element('notice').textContent, /デモ/);
  assert.deepEqual(await ui.exported(), expected);
  const finished = storage.getItem(KPDStore.STORAGE_KEY);
  const reopened = importer(storage);
  assert.match(reopened.element('counter').textContent, /^5 \/ 5 枚$/);
  reopened.select('sample', 'pps');
  reopened.select('language', 'en');
  assert.equal(storage.getItem(KPDStore.STORAGE_KEY), finished);
  reopened.select('history', oldPage.key);
  assert.deepEqual(await reopened.exported(), expected);
});

test('an imported legacy demo is marked synthetic by its KPD1 origin', async () => {
  const ui = importer(null);
  ui.import(KPDSamples.source);
  assert.equal(ui.element('badges').children[0].textContent, 'DEMO');
  assert.deepEqual(await ui.exported(), {...KPD.assemble(KPDSamples.source).snapshot, synthetic: true});
});

test('cable and PPS measurements sharing sample origins export without demo flags', async () => {
  const ui = importer(null);
  for (const [origin, pages] of Object.entries(measurements)) {
    ui.select('sample', origin.slice('km003c_'.length));
    ui.import(pages);
    assert.equal(ui.element('sample').value, '');
    assert.equal(ui.element('badges').children.some(badge => badge.textContent === 'DEMO'), false);
    const expected = KPD.assemble(pages).snapshot;
    assert.equal(expected.origin, origin);
    assert.deepEqual(await ui.exported(), expected);
  }
});
