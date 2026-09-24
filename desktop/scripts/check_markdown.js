// Sanitized-Markdown check for Ridian's replies (v7.4, renderer/markdown.js).
//
// Runs in plain Node against a minimal DOM that offers ONLY the calls the
// module may make (createElement, createTextNode, createDocumentFragment,
// appendChild, removeChild, setAttribute) and throws on innerHTML. Checks
// the parse output, that a hostile reply renders inert, and the module's
// source for any HTML-parsing API.
//
// Run:  node scripts/check_markdown.js
// Exits 0 with "MARKDOWN OK", or 1 with the failure.
// Pinned by apps/api/tests/test_renderer_markdown.py; the real-DOM half is in
// check_settings_layout.js ("Reply markdown (real DOM)").

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const SOURCE_PATH = path.join(__dirname, '..', 'renderer', 'markdown.js');
const md = require(SOURCE_PATH);

function makeDocument() {
  const doc = {};
  const element = (tag) => {
    const node = {
      nodeType: tag === '#fragment' ? 11 : 1,
      tagName: tag.toUpperCase(),
      attrs: {},
      children: [],
      ownerDocument: doc,
      appendChild(child) {
        if (child.nodeType === 11) {
          child.children.forEach((c) => this.appendChild(c));
          child.children = [];
        } else {
          this.children.push(child);
        }
        return child;
      },
      removeChild(child) {
        this.children.splice(this.children.indexOf(child), 1);
        return child;
      },
      get firstChild() { return this.children[0] || null; },
      setAttribute(name, value) { this.attrs[name] = String(value); },
    };
    for (const banned of ['innerHTML', 'outerHTML']) {
      Object.defineProperty(node, banned, {
        get() { throw new Error(banned + ' read'); },
        set() { throw new Error(banned + ' written'); },
      });
    }
    node.insertAdjacentHTML = () => { throw new Error('insertAdjacentHTML used'); };
    return node;
  };
  doc.createElement = (tag) => element(String(tag).toLowerCase());
  doc.createTextNode = (text) => ({ nodeType: 3, text: String(text) });
  doc.createDocumentFragment = () => element('#fragment');
  return doc;
}

const escapeText = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
function serialize(node) {
  if (node.nodeType === 3) return escapeText(node.text);
  const inner = node.children.map(serialize).join('');
  if (node.nodeType === 11) return inner;
  const tag = node.tagName.toLowerCase();
  const attrs = Object.entries(node.attrs).map(([k, v]) => ` ${k}="${v}"`).join('');
  return tag === 'br' ? `<br${attrs}>` : `<${tag}${attrs}>${inner}</${tag}>`;
}
function walk(node, visit) {
  visit(node);
  (node.children || []).forEach((c) => walk(c, visit));
}
function renderToHost(text) {
  const doc = makeDocument();
  const host = doc.createElement('div');
  host.appendChild(doc.createTextNode('stale content'));
  md.renderInto(host, text);
  return host;
}

const t = (text) => ({ kind: 'text', text });
const b = (text) => ({ kind: 'strong', text });
const SAMPLE = [
  '# Recap', '', 'Drafted **two** files.', 'Second line.', '',
  '- one', '- **two**', '  continued', '', '3. third', '4. fourth', '',
  '## Next', 'See [the doc](https://example.com/doc) and ![chart](https://example.com/x.png).', '',
  'Plain *italic* and `code` stay literal.',
].join('\n');

// 1. The same subset, and the same parse, as the Owner Workspace.
assert.deepEqual(md.parse(SAMPLE), [
  { kind: 'heading', level: 4, inlines: [t('Recap')] },
  { kind: 'paragraph', lines: [[t('Drafted '), b('two'), t(' files.')], [t('Second line.')]] },
  { kind: 'list', ordered: false, start: 1, items: [[t('one')], [b('two'), t(' continued')]] },
  { kind: 'list', ordered: true, start: 3, items: [[t('third')], [t('fourth')]] },
  { kind: 'heading', level: 5, inlines: [t('Next')] },
  { kind: 'paragraph', lines: [[t('See the doc (https://example.com/doc) and chart.')]] },
  { kind: 'paragraph', lines: [[t('Plain *italic* and `code` stay literal.')]] },
]);
assert.deepEqual(md.parse('**unclosed bold'), [{ kind: 'paragraph', lines: [[t('**unclosed bold')]] }]);
assert.deepEqual(md.parse(''), []);
assert.deepEqual(md.parse(undefined), []);

const sample = serialize(renderToHost(SAMPLE));
assert.equal(sample,
  '<div><h4>Recap</h4><p>Drafted <strong>two</strong> files.<br>Second line.</p>'
  + '<ul><li>one</li><li><strong>two</strong> continued</li></ul>'
  + '<ol start="3"><li>third</li><li>fourth</li></ol><h5>Next</h5>'
  + '<p>See the doc (https://example.com/doc) and chart.</p>'
  + '<p>Plain *italic* and `code` stay literal.</p></div>');

// 2. The morning-brief case: bold renders as bold, never as literal **.
const brief = serialize(renderToHost('**Today:** 3 meetings\n\n**Overdue invoices**\n- Sandy Alvarez $250'));
assert.ok(!brief.includes('**'), brief);
assert.ok(brief.includes('<strong>Today:</strong> 3 meetings'), brief);

// 3. A hostile reply renders inert.
const HOSTILE = [
  'Done.', '', '<script>alert("pwned")</script>', '', '<img src=x onerror="alert(1)">', '',
  '![tracker](https://evil.example/pixel.png)', '', '**<b onmouseover="alert(2)">bold html</b>**', '',
  '- [click me](javascript:alert(3))',
].join('\n');
const host = renderToHost(HOSTILE);
const ALLOWED = new Set(['DIV', 'H4', 'H5', 'H6', 'P', 'BR', 'UL', 'OL', 'LI', 'STRONG']);
walk(host, (node) => {
  if (node.nodeType !== 1) return;
  assert.ok(ALLOWED.has(node.tagName), 'element ' + node.tagName);
  for (const name of Object.keys(node.attrs)) assert.equal(name, 'start', 'attribute ' + name);
});
const hostile = serialize(host);
assert.ok(!/<script|<img|<a[\s>]|<b[\s>]/i.test(hostile), hostile);
assert.ok(!hostile.includes('evil.example'), 'the image source is dropped');
assert.ok(hostile.includes('&lt;script&gt;alert("pwned")&lt;/script&gt;'), hostile);
assert.ok(hostile.includes('&lt;img src=x onerror="alert(1)"&gt;'), hostile);
assert.ok(hostile.includes('tracker') && hostile.includes('click me (javascript:alert(3))'), hostile);
assert.ok(!hostile.includes('stale content'), 'renderInto replaces what was there');

// 4. The module never parses a string as HTML.
const source = fs.readFileSync(SOURCE_PATH, 'utf8');
const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
for (const api of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'DOMParser',
  'createContextualFragment', 'srcdoc', 'eval(', 'Function(']) {
  assert.ok(!code.includes(api), 'markdown.js uses ' + api);
}
assert.deepEqual([...code.matchAll(/setAttribute\('([^']+)'/g)].map((m) => m[1]), ['start']);

console.log('MARKDOWN OK — parse matches the Owner Workspace subset; hostile replies render inert.');
