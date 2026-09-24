/* Ridian Operator — Ridian's replies as sanitized Markdown (v7.4).
 *
 * The same deliberately small subset the Owner Workspace uses for job
 * replies (ridian-technologies-site $lib/operator-jobs/markdown.ts):
 * headings (#..######), paragraphs (single line breaks kept), bullet lists
 * (-, *, +), numbered lists (1. or 1)), and **bold** / __bold__. Nothing else:
 *   - raw HTML (<script>, <img …>, any tag) stays literal text;
 *   - images ![alt](src) become their alt text — nothing is ever loaded;
 *   - links [label](url) become the plain text "label (url)" — never clickable;
 *   - italics, code, quotes, tables and rules stay as typed.
 *
 * The DOM is built ONLY with createElement / createTextNode / appendChild and
 * fixed tag names (h4-h6, p, br, ul, ol, li, strong); no string is ever parsed
 * as HTML, so a reply cannot inject markup, scripts, images or links.
 * Pinned by apps/api/tests/test_renderer_markdown.py (desktop/scripts/
 * check_markdown.js) and the real-DOM section of check_settings_layout.js.
 */
(function (root) {
  'use strict';

  const HEADING_RE = /^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$/;
  const BULLET_RE = /^ {0,3}([-*+])[ \t]+(.*)$/;
  const ORDERED_RE = /^ {0,3}(\d{1,9})[.)][ \t]+(.*)$/;
  const CONTINUATION_RE = /^(?: {2,}|\t)(\S.*)$/;
  const IMAGE_RE = /!\[([^\]]*)\]\([^)]*\)/g;
  const LINK_RE = /\[([^\]]+)\]\(\s*([^)\s]+)[^)]*\)/g;
  const BOLD_RE = /\*\*(?=\S)([\s\S]*?\S)\*\*|__(?=\S)([\s\S]*?\S)__/g;

  /** Inline text: images to alt text, links to "label (url)", then **bold**. */
  function parseInline(text) {
    const plain = String(text)
      .replace(IMAGE_RE, (_m, alt) => alt)
      .replace(LINK_RE, (_m, label, url) => `${label} (${url})`);
    const out = [];
    let last = 0;
    for (const match of plain.matchAll(BOLD_RE)) {
      const index = match.index || 0;
      if (index > last) out.push({ kind: 'text', text: plain.slice(last, index) });
      out.push({ kind: 'strong', text: match[1] !== undefined ? match[1] : (match[2] || '') });
      last = index + match[0].length;
    }
    if (last < plain.length) out.push({ kind: 'text', text: plain.slice(last) });
    return out;
  }

  /** The reply as blocks. Never throws; unknown syntax stays literal text. */
  function parse(input) {
    const text = typeof input === 'string' ? input.replace(/\r\n?/g, '\n') : '';
    const blocks = [];
    let paragraph = null;
    let list = null;
    const flush = () => {
      if (paragraph) blocks.push({ kind: 'paragraph', lines: paragraph.map(parseInline) });
      if (list) blocks.push({ kind: 'list', ordered: list.ordered, start: list.start, items: list.items.map(parseInline) });
      paragraph = null;
      list = null;
    };
    for (const line of text.split('\n')) {
      if (!line.trim()) { flush(); continue; }
      const heading = HEADING_RE.exec(line);
      if (heading) {
        flush();
        blocks.push({ kind: 'heading', level: Math.min(6, heading[1].length + 3), inlines: parseInline(heading[2]) });
        continue;
      }
      const bullet = BULLET_RE.exec(line);
      const ordered = bullet ? null : ORDERED_RE.exec(line);
      if (bullet || ordered) {
        const isOrdered = !!ordered;
        const itemText = (bullet ? bullet[2] : ordered[2]).trim();
        if (!list || list.ordered !== isOrdered) {
          flush();
          list = { ordered: isOrdered, start: ordered ? Math.max(1, Number(ordered[1])) : 1, items: [] };
        }
        list.items.push(itemText);
        continue;
      }
      const continuation = CONTINUATION_RE.exec(line);
      if (list && continuation) {
        list.items[list.items.length - 1] += ' ' + continuation[1].trim();
        continue;
      }
      if (list) flush();
      if (!paragraph) paragraph = [];
      paragraph.push(line.trim());
    }
    flush();
    return blocks;
  }

  function appendInline(doc, parent, parts) {
    for (const part of parts) {
      if (part.kind === 'strong') {
        const strong = doc.createElement('strong');
        strong.appendChild(doc.createTextNode(part.text));
        parent.appendChild(strong);
      } else {
        parent.appendChild(doc.createTextNode(part.text));
      }
    }
  }

  /** Blocks -> a DocumentFragment of fixed elements and text nodes. */
  function render(blocks, doc) {
    const frag = doc.createDocumentFragment();
    for (const block of blocks) {
      if (block.kind === 'heading') {
        const h = doc.createElement(block.level === 4 ? 'h4' : block.level === 5 ? 'h5' : 'h6');
        appendInline(doc, h, block.inlines);
        frag.appendChild(h);
      } else if (block.kind === 'paragraph') {
        const p = doc.createElement('p');
        block.lines.forEach((line, i) => {
          if (i) p.appendChild(doc.createElement('br'));
          appendInline(doc, p, line);
        });
        frag.appendChild(p);
      } else if (block.kind === 'list') {
        const listEl = doc.createElement(block.ordered ? 'ol' : 'ul');
        if (block.ordered && block.start !== 1) listEl.setAttribute('start', String(block.start));
        for (const item of block.items) {
          const li = doc.createElement('li');
          appendInline(doc, li, item);
          listEl.appendChild(li);
        }
        frag.appendChild(listEl);
      }
    }
    return frag;
  }

  /** Replace el's contents with the rendered reply. */
  function renderInto(el, text) {
    const doc = el.ownerDocument;
    while (el.firstChild) el.removeChild(el.firstChild);
    el.appendChild(render(parse(text), doc));
  }

  const api = { parse, parseInline, render, renderInto };
  if (typeof module === 'object' && module && module.exports) module.exports = api;
  root.RidianMarkdown = api;
})(typeof window !== 'undefined' ? window : globalThis);
