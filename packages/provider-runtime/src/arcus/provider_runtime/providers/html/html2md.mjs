#!/usr/bin/env node
// html2md.mjs — Convert HTML to readable markdown. Zero dependencies.
//
// Usage:
//   echo "<html>..." | node html2md.mjs
//   node html2md.mjs < file.html
//   node html2md.mjs --url https://example.com
//   node html2md.mjs --url https://example.com --title-only
//   node html2md.mjs --base-url https://example.com < page.html   (stdin HTML,
//     links resolved against the base URL; never fetches)

import { stdin, stdout, argv } from 'node:process';

const args = argv.slice(2);
const urlFlag = args.indexOf('--url');
const titleOnly = args.includes('--title-only');
const url = urlFlag !== -1 ? args[urlFlag + 1] : null;
const baseFlag = args.indexOf('--base-url');
const baseUrl = baseFlag !== -1 ? args[baseFlag + 1] : url;

async function getHtml() {
  if (url) {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; KBCapture/1.0)' },
      redirect: 'follow',
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
    return await res.text();
  }
  // Read from stdin
  const chunks = [];
  for await (const chunk of stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf-8');
}

// The page title, as markdown text (#23): decoded once and escaped. One
// forward look for the first <title> and its close (#22): a regex retrying
// from every unclosed "<title" would be quadratic.
function extractTitle(html) {
  const open = /<title(?![\w-])/i.exec(html);
  if (!open) return '';
  const gt = html.indexOf('>', open.index);
  if (gt === -1) return '';
  const closeRe = /<\/title\s*>/gi;
  closeRe.lastIndex = gt + 1;
  const c = closeRe.exec(html);
  if (!c) return '';
  return escapeText(decodeEntities(html.slice(gt + 1, c.index)).replace(/\s+/g, ' ').trim());
}

// Resolve a possibly-relative URL against the page's base URL. Falls back to
// returning the src unchanged if baseUrl is absent or resolution fails (e.g.
// data: URIs, fragment-only links). Absolute URLs pass through untouched.
function resolveUrl(src, baseUrl) {
  if (!src) return src;
  if (/^(https?:|data:|mailto:|tel:|#)/i.test(src)) return src;
  if (src.startsWith('//')) return 'https:' + src;
  if (!baseUrl) return src;
  try {
    return new URL(src, baseUrl).href;
  } catch {
    return src;
  }
}

// Content-root choice (#10). The first <article>/<main> is the usual content
// root, but on component-CMS pages (Adobe AEM "insights") it is a small hero/
// teaser with the body in sibling containers after it, and on index pages it
// is one post of many. When the chosen root holds under THIN_ROOT_RATIO of the
// page's visible text on a page with at least THIN_ROOT_MIN_BODY characters,
// convert the whole body instead (chrome is stripped below either way).
// Mirrored by _pick_main_text in _athena_fetch_page.py.
const THIN_ROOT_RATIO = 0.3;
const THIN_ROOT_MIN_BODY = 1000;
// Page chrome, removed with its whole subtree: these elements, and any element
// whose ARIA role marks it as navigation, banner, footer (contentinfo) or a
// dialog (consent panels, "you're leaving" interstitials). Nesting is counted,
// so a <nav> holding another <nav> is removed whole, not up to the inner close.
const CHROME_TAGS = ['script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript', 'svg', 'iframe'];
const RAW_TEXT_TAGS = new Set(['script', 'style', 'noscript']);
const CHROME_OPEN_RE = new RegExp(
  `<(${CHROME_TAGS.join('|')})(?![\\w-])[^>]*>` +
  `|<([a-z][a-z0-9-]*)(?![\\w-])[^>]*(?<![\\w-])role\\s*=\\s*["']?(?:navigation|banner|contentinfo|dialog)\\b[^>]*>`,
  'gi');

// Index just past the element's matching close tag; `from` (drop only the
// opening tag) when it is self-closed or never closed.
function elementEnd(html, tag, openTag, from) {
  if (openTag.endsWith('/>')) return from;
  if (RAW_TEXT_TAGS.has(tag)) {
    const closeRe = new RegExp(`</${tag}(?![\\w-])`, 'gi');
    closeRe.lastIndex = from;
    const c = closeRe.exec(html);
    if (!c) return from;
    const gt = html.indexOf('>', c.index);
    return gt === -1 ? html.length : gt + 1;
  }
  const re = new RegExp(`<(/?)${tag}(?![\\w-])[^>]*>`, 'gi');
  re.lastIndex = from;
  let depth = 1, m;
  while ((m = re.exec(html))) {
    if (!m[1] && m[0].endsWith('/>')) continue;
    depth += m[1] ? -1 : 1;
    if (depth === 0) return m.index + m[0].length;
  }
  return from;
}

function stripChrome(html) {
  const re = new RegExp(CHROME_OPEN_RE.source, 'gi');
  let out = '', pos = 0, m;
  while ((m = re.exec(html))) {
    const tag = (m[1] || m[2]).toLowerCase();
    out += html.slice(pos, m.index);
    pos = elementEnd(html, tag, m[0], m.index + m[0].length);
    re.lastIndex = pos;
  }
  return out + html.slice(pos);
}

// Site menus not marked up as navigation (#19). A mega-menu built from plain
// <div>/<section>/<ul> blocks (link cards, "Key Links" columns) escapes the
// tag and role checks above. Such a block is many real links with little text
// between them: at least MENU_MIN_LINKS links (same-page "#" and javascript:
// links don't count) and at most MENU_MAX_CHARS_PER_LINK visible characters
// per link. Only blocks that END BEFORE the page's title (its first <h1> with
// text of its own, so a logo link in an <h1> is not the title) are dropped:
// a block that holds the title, or a link list inside or after the article,
// is never touched, and a page with no title heading is left as it is.
// Mirrored by _DROP_LINK_MENUS_JS in _athena_fetch_page.py.
const MENU_MIN_LINKS = 10;
const MENU_MAX_CHARS_PER_LINK = 60;
const VOID_TAGS = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
  'link', 'meta', 'param', 'source', 'track', 'wbr']);
const MENU_RAW_TAGS = new Set(['script', 'style', 'noscript', 'textarea', 'title']);

// Index of the title heading's opening tag, or -1.
function titleIndex(html) {
  const openRe = /<h1(?![\w-])[^>]*>/gi;
  const closeRe = /<\/h1\s*>/gi;
  let m;
  while ((m = openRe.exec(html))) {
    closeRe.lastIndex = openRe.lastIndex;
    const c = closeRe.exec(html);
    if (!c) return -1;
    const own = html.slice(openRe.lastIndex, c.index).replace(/<a(?![\w-])[^>]*>[\s\S]*?<\/a\s*>/gi, '');
    if (decodeEntities(stripTags(own)).trim()) return m.index;
    openRe.lastIndex = c.index + c[0].length;
  }
  return -1;
}

function stripLinkMenus(html) {
  const title = titleIndex(html);
  if (title <= 0) return html;
  const tagRe = /<(\/?)([a-zA-Z][\w-]*)([^>]*)>/g;
  const top = { text: 0, links: 0 };
  const stack = [];
  const drop = [];  // [start, end) of the outermost menu blocks, in order
  let last = 0, m;
  // Only elements that close before the title can be menus: stop there.
  while ((m = tagRe.exec(html)) && m.index < title) {
    const n = html.slice(last, m.index).replace(/\s+/g, ' ').trim().length;
    (stack.length ? stack[stack.length - 1] : top).text += n;
    last = tagRe.lastIndex;
    const tag = m[2].toLowerCase();
    if (!m[1]) {
      if (MENU_RAW_TAGS.has(tag)) {
        const closeRe = new RegExp(`</${tag}(?![\\w-])[^>]*>`, 'gi');
        closeRe.lastIndex = last;
        const c = closeRe.exec(html);
        if (!c) break;
        tagRe.lastIndex = last = c.index + c[0].length;
        continue;
      }
      if (VOID_TAGS.has(tag) || m[3].endsWith('/')) continue;
      let links = 0;
      if (tag === 'a') {
        const href = /(?:^|\s)href\s*=\s*["']?([^"'\s>]*)/i.exec(m[3]);
        if (href && href[1] && !/^(#|javascript:)/i.test(href[1])) links = 1;
      }
      stack.push({ tag, start: m.index, text: 0, links });
      continue;
    }
    let k = stack.length - 1;
    while (k >= 0 && stack[k].tag !== tag) k--;
    if (k < 0) continue;  // stray close tag
    const end = m.index + m[0].length;
    while (stack.length > k) {  // elements left open close here too
      const el = stack.pop();
      const parent = stack.length ? stack[stack.length - 1] : top;
      parent.text += el.text;
      parent.links += el.links;
      if (el.links >= MENU_MIN_LINKS && el.text <= MENU_MAX_CHARS_PER_LINK * el.links) {
        while (drop.length && drop[drop.length - 1][0] >= el.start) drop.pop();
        drop.push([el.start, end]);
      }
    }
  }
  if (!drop.length) return html;
  let out = '', pos = 0;
  for (const [s, e] of drop) { out += html.slice(pos, s); pos = e; }
  return out + html.slice(pos);
}

function visibleLength(html) {
  const t = decodeEntities(stripTags(stripChrome(stripLinkMenus(html.replace(/<!--[\s\S]*?-->/g, '')))));
  return t.replace(/\s+/g, ' ').trim().length;
}

// Inner HTML of the first <tag> element, matched to its own closing tag
// (nested elements of the same name are counted, not taken as its end).
function elementInner(html, tag) {
  const open = new RegExp(`<${tag}(?![\\w-])[^>]*>`, 'i');
  const first = open.exec(html);
  if (!first) return null;
  const re = new RegExp(`<(/?)${tag}(?![\\w-])[^>]*>`, 'gi');
  re.lastIndex = first.index + first[0].length;
  let depth = 1, m;
  while ((m = re.exec(html))) {
    depth += m[1] ? -1 : 1;
    if (depth === 0) return { index: first.index, inner: html.slice(first.index + first[0].length, m.index) };
  }
  return { index: first.index, inner: html.slice(first.index + first[0].length) };
}

function pickContentRoot(html) {
  const bodyTag = html.match(/<body(?![\w-])[^>]*>([\s\S]*)<\/body\s*>/i);
  const body = bodyTag ? bodyTag[1] : html;
  const candidates = ['article', 'main'].map(t => elementInner(html, t)).filter(Boolean);
  if (!candidates.length) return body;
  const root = candidates.sort((a, b) => a.index - b.index)[0].inner;
  const bodyLen = visibleLength(body);
  if (bodyLen >= THIN_ROOT_MIN_BODY && visibleLength(root) < THIN_ROOT_RATIO * bodyLen) return body;
  return root;
}

function htmlToMarkdown(html, baseUrl) {
  // Linear time (#22): every rule below sees balanced, canonical markup.
  let text = pickContentRoot(balanceMarkup(html));

  // Remove HTML comments, then unmarked site menus (see stripLinkMenus; before
  // stripChrome, so a title heading inside chrome still marks where the
  // content starts), then page chrome (see stripChrome).
  text = text.replace(/<!--[\s\S]*?-->/g, '');
  text = stripLinkMenus(text);
  text = stripChrome(text);

  // Page text stays text (#23): escapeMarkup decodes each text node once and
  // escapes it for markdown, rewrites every tag with clean double-quoted
  // attributes, and turns <pre>/<code> into finished markdown held aside
  // until the end. Nothing below ever decodes entities again.
  const code = [];
  text = escapeMarkup(text, code);

  // Every converter matches only its own element (#20): each open tag ends at a
  // tag-name boundary, (?![\w-]), so <b> isn't <button>/<br>/<blockquote>,
  // <i> isn't <input>/<ins>, <em> isn't <embed>, <li> isn't <link> and <a>
  // isn't <area>/<abbr>; attributes are read by exact name (attrValue), so
  // data-href/data-src/data-alt aren't href/src/alt. A wrong match used to pair
  // with a later real close tag and flatten everything between the two.

  // Convert headings
  text = text.replace(/<h([1-6])(?![\w-])[^>]*>([\s\S]*?)<\/h\1\s*>/gi,
    (_, n, c) => `\n${'#'.repeat(Number(n))} ${stripTags(c).trim()}\n`);

  // Convert links. Only http(s) targets (after resolving a relative href
  // against the page) become links; any other scheme (javascript:, file:,
  // obsidian:, data:, mailto:, ...) and same-page "#" links keep their text.
  text = text.replace(/<a(?![\w-])([^>]*)>([\s\S]*?)<\/a\s*>/gi, (_, attrs, c) => {
    const href = attrValue(attrs, 'href');
    if (href === null) return c;  // not a link: keep its content
    const label = stripTags(c).trim();
    if (!label) return '';
    if (href.startsWith('#')) return label;
    const target = markdownUrl(resolveUrl(href, baseUrl));
    return target ? `[${label}](${target})` : label;
  });

  // Images. Many sites lazy-load: the real URL sits in data-src /
  // data-lazy-src / data-original (or the first data-srcset entry) and src is
  // missing or a data: placeholder (an LQIP; the wiz.io article hit this). A
  // real src always wins; a lazy URL is used only when there is none.
  // Relative URLs are resolved against the page, as for links, and only
  // http(s) images are embedded. The alt text is page text: escaped.
  text = text.replace(/<img(?![\w-])[^>]*>/gi, (tag) => {
    let src = attrValue(tag, 'src');
    if (src === null || /^data:/i.test(src)) {
      let lazy = attrValue(tag, 'data-src') || attrValue(tag, 'data-lazy-src')
        || attrValue(tag, 'data-original');
      if (!lazy) {
        const srcset = attrValue(tag, 'data-srcset');
        lazy = srcset ? srcset.split(',')[0].trim().split(/\s+/)[0] : null;
      }
      if (lazy && !/^data:/i.test(lazy)) src = lazy;
    }
    const target = src ? markdownUrl(resolveUrl(src, baseUrl)) : null;
    if (!target) return '';
    const alt = escapeText(attrValue(tag, 'alt') ?? '').replace(/\s+/g, ' ').trim();
    return `![${alt}](${target})`;
  });

  // Convert bold and italic. Empty emphasis (<b></b>) leaves no markers.
  const emphasis = (marker) => (_, _t, c) => {
    const inner = stripTags(c);
    return inner.trim() ? `${marker}${inner.trim()}${marker}` : inner;
  };
  text = text.replace(/<(strong|b)(?![\w-])[^>]*>([\s\S]*?)<\/\1\s*>/gi, emphasis('**'));
  text = text.replace(/<(em|i)(?![\w-])[^>]*>([\s\S]*?)<\/\1\s*>/gi, emphasis('*'));

  // Convert blockquotes
  text = text.replace(/<blockquote(?![\w-])[^>]*>([\s\S]*?)<\/blockquote\s*>/gi, (_, c) => {
    return stripTags(c).trim().split('\n').map(l => `> ${l.trim()}`).join('\n') + '\n';
  });

  // Convert list items
  text = text.replace(/<li(?![\w-])[^>]*>([\s\S]*?)<\/li\s*>/gi, (_, c) => `- ${stripTags(c).trim()}\n`);

  // Convert paragraphs and divs to double newlines
  text = text.replace(/<\/p\s*>/gi, '\n\n');
  text = text.replace(/<br(?![\w-])[^>]*>/gi, '\n');
  text = text.replace(/<\/div\s*>/gi, '\n');

  // Convert horizontal rules
  text = text.replace(/<hr(?![\w-])[^>]*>/gi, '\n---\n');

  // Strip all remaining HTML tags (text holds no "<" of its own: escaped)
  text = stripTags(text);

  // No line of page text may open a fence (Dataview runs ```dataviewjs and
  // friends); generated code is still held aside here.
  text = neutralizeFences(text);

  // Put the code back (see escapeMarkup).
  text = restoreCode(text, code);

  // Clean up whitespace
  text = text.replace(/\n{3,}/g, '\n\n');          // max 2 consecutive newlines
  text = text.replace(/[ \t]+$/gm, '');             // trailing whitespace per line
  text = text.replace(/^[ \t]+/gm, (m) => m.replace(/\t/g, '  ')); // tabs to spaces
  text = text.trim();

  return text;
}

// ── Linear time (#22) ───────────────────────────────────────────────────────
//
// The rules above are regexes that pair an open tag with its close. On markup
// with unclosed tags each attempt rescanned to the end of the page (quadratic;
// a 300 KB page of unclosed <nav> took 47 s), and a tag missing its ">" made
// [^>]* scans run on too. balanceMarkup rewrites the page once, in linear
// time, so every tag is rebuilt whole and every element is closed: a close tag
// with no open element is dropped, and elements left open are closed where
// their parent closes or at the end. Browser-rendered pages are balanced
// already, so their output doesn't change.

const BALANCE_RAW_TAGS = new Set(['script', 'style', 'noscript', 'textarea', 'title', 'xmp',
  'iframe', 'noembed', 'noframes']);
const TAG_NAME_OK_RE = /^[a-z][a-z0-9:-]*$/;

function balanceMarkup(html) {
  const n = html.length;
  const stack = [];
  const openCount = new Map();
  let out = '', i = 0;
  const pop = () => {
    const t = stack.pop();
    openCount.set(t, openCount.get(t) - 1);
    out += '</' + t + '>';
  };
  while (i < n) {
    const lt = html.indexOf('<', i);
    if (lt === -1) { out += html.slice(i); break; }
    out += html.slice(i, lt);
    if (html.startsWith('<!--', lt)) { const e = html.indexOf('-->', lt + 4); i = e === -1 ? n : e + 3; continue; }
    if (html[lt + 1] === '!' || html[lt + 1] === '?') { const e = html.indexOf('>', lt); i = e === -1 ? n : e + 1; continue; }
    if (!isTagStart(html, lt)) { out += '&lt;'; i = lt + 1; continue; }
    const t = readTag(html, lt);
    if (!TAG_NAME_OK_RE.test(t.name)) { out += '&lt;'; i = lt + 1; continue; }  // not a tag: text
    i = t.end;
    if (t.close) {
      if (openCount.get(t.name) > 0) {
        while (stack[stack.length - 1] !== t.name) pop();  // close what was left open inside
        pop();
      }
      continue;  // a close tag with no open element is dropped
    }
    let attrs = '';
    for (const [k, v] of t.attrs) {
      if (CLEAN_ATTR_NAME_RE.test(k)) attrs += ' ' + k + '="' + escapeAttr(decodeEntities(v)) + '"';
    }
    if (BALANCE_RAW_TAGS.has(t.name) && !t.selfClose) {
      // Raw text up to its own close; only then look for that close's ">".
      const closeRe = new RegExp('</' + t.name + '(?![\\w-])', 'gi');
      closeRe.lastIndex = i;
      const c = closeRe.exec(html);
      const stop = c ? c.index : n;
      out += '<' + t.name + attrs + '>' + html.slice(i, stop) + '</' + t.name + '>';
      if (!c) { i = n; continue; }
      const gt = html.indexOf('>', c.index);
      i = gt === -1 ? n : gt + 1;
      continue;
    }
    out += '<' + t.name + attrs + (t.selfClose ? ' /' : '') + '>';
    if (!t.selfClose && !VOID_TAGS.has(t.name)) {
      stack.push(t.name);
      openCount.set(t.name, (openCount.get(t.name) || 0) + 1);
    }
  }
  while (stack.length) pop();
  return out;
}

// ── Page text → markdown text (#23) ─────────────────────────────────────────
//
// Page text must never become markup in the note: a page that shows
// "&lt;img src=...&gt;" or "![x](url)" as text, or hides a tag in an attribute,
// must not produce a live tag, link, embed or wikilink that Obsidian renders
// (and fetches) when the note is opened.

// Placeholders for code held aside until the end; any in the page are dropped.
const CODE_OPEN = String.fromCharCode(0xE000);
const CODE_CLOSE = String.fromCharCode(0xE001);
const CODE_MARK_RE = new RegExp(`[${CODE_OPEN}${CODE_CLOSE}]`, 'g');
const CODE_REF_RE = new RegExp(`${CODE_OPEN}(\\d+)${CODE_CLOSE}`, 'g');
const ENTITY_START_RE = /&(?=(?:#\d+|#x[0-9a-f]+|amp|lt|gt|quot|apos|nbsp);)/gi;
// RCDATA elements hold text, never markup; RAWTEXT ones are not page text.
const RCDATA_TAGS = new Set(['textarea', 'title']);
const RAWTEXT_TAGS = new Set(['script', 'style', 'noscript', 'xmp', 'iframe', 'noembed', 'noframes']);

// Decoded page text → markdown that shows exactly that text. Escapes "\" first
// (so no text backslash can escape the escapes), "`" (no code spans, so no
// Dataview inline queries, and no backtick fences), "&" where it would start an
// entity, "<" (no tags, autolinks, HTML blocks or Templater "<% %>"), and "[" /
// "]" (no links, embeds, reference links or [[wikilinks]]). "~" too: no tilde
// fence can form, even from pieces that only meet later (an empty <code>
// between them). ">" stays: without "<" it starts no markup (at most a quote
// at the start of a line). neutralizeFences is a second line of defence.
function escapeText(s) {
  return s.replace(CODE_MARK_RE, '')
    .replace(/\\/g, '\\\\')
    .replace(/`/g, '\\`').replace(/~/g, '\\~')
    .replace(ENTITY_START_RE, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/\[/g, '\\[').replace(/\]/g, '\\]');
}

function escapeAttr(s) {
  return s.replace(CODE_MARK_RE, '').replace(/&/g, '&amp;').replace(/"/g, '&quot;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// An http(s) URL, made safe inside a markdown link target (no whitespace or
// parentheses or angle brackets to end it early), or null for any other
// scheme or a URL that stayed relative.
function markdownUrl(u) {
  if (!u || !/^https?:\/\/[^\s/?#]/i.test(u)) return null;
  return u.replace(/[\s()<>]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase().padStart(2, '0'));
}

const TAG_NAME_RE = /[a-zA-Z][^\s\/>]*/y;
const ATTR_NAME_RE = /[^\s\/>][^\s\/>=]*/y;
const UNQUOTED_RE = /[^\s>]*/y;
const CLEAN_ATTR_NAME_RE = /^[a-z_:][a-z0-9_:.-]*$/;

// One tag at html[i] === '<' (the caller checked a name follows): its name,
// whether it closes, its attributes [[name, raw value]], and the index just
// past it. Quoted values may hold ">" and "<"; an unterminated tag or quote
// runs to the end of the input. Linear in the tag's length.
function readTag(html, i) {
  const n = html.length;
  let j = i + 1, close = false;
  if (html[j] === '/') { close = true; j++; }
  TAG_NAME_RE.lastIndex = j;
  const name = TAG_NAME_RE.exec(html)[0].toLowerCase();
  j = TAG_NAME_RE.lastIndex;
  const attrs = [];
  let selfClose = false;
  while (j < n) {
    while (j < n && (html[j] === '/' || /\s/.test(html[j]))) {
      if (html[j] === '/' && html[j + 1] === '>') selfClose = true;
      j++;
    }
    if (j >= n) break;
    if (html[j] === '>') { j++; return { name, close, attrs, selfClose, end: j }; }
    ATTR_NAME_RE.lastIndex = j;
    const an = ATTR_NAME_RE.exec(html)[0].toLowerCase();
    j = ATTR_NAME_RE.lastIndex;
    while (j < n && /\s/.test(html[j])) j++;
    let value = '';
    if (html[j] === '=') {
      j++;
      while (j < n && /\s/.test(html[j])) j++;
      const q = html[j];
      if (q === '"' || q === "'") {
        const e = html.indexOf(q, j + 1);
        value = html.slice(j + 1, e === -1 ? n : e);
        j = e === -1 ? n : e + 1;
      } else {
        UNQUOTED_RE.lastIndex = j;
        value = UNQUOTED_RE.exec(html)[0];
        j = UNQUOTED_RE.lastIndex;
      }
    }
    attrs.push([an, value]);
  }
  return { name, close, attrs, selfClose, end: n };
}

function isTagStart(html, i) {
  const c = html[i + 1] || '';
  return /[a-zA-Z]/.test(c) || (c === '/' && /[a-zA-Z]/.test(html[i + 2] || ''));
}

// The text of a <pre>/<code> element whose open tag ends at i, up to its
// matching close tag (same-name nesting counted): entities decoded once,
// <br> as a newline, other tags dropped. Returns [text, index past the close].
function readCodeText(html, i, tag) {
  const n = html.length;
  let out = '', depth = 1;
  while (i < n) {
    const lt = html.indexOf('<', i);
    if (lt === -1) { out += decodeEntities(html.slice(i)); i = n; break; }
    out += decodeEntities(html.slice(i, lt));
    if (html.startsWith('<!--', lt)) { const e = html.indexOf('-->', lt + 4); i = e === -1 ? n : e + 3; continue; }
    if (!isTagStart(html, lt)) { out += '<'; i = lt + 1; continue; }
    const t = readTag(html, lt);
    i = t.end;
    if (t.name === tag) {
      depth += t.close ? -1 : (t.selfClose ? 0 : 1);
      if (depth === 0) break;
    } else if (t.name === 'br' && !t.close) {
      out += '\n';
    }
  }
  // Templater runs "<% ... %>" anywhere in the raw file, code included, and
  // ignores markdown: the one sequence code can't keep literally.
  return [out.replace(CODE_MARK_RE, '').replace(/<%/g, '&lt;%'), i];
}

// A line that would open a fenced block: optional container prefixes (quote
// markers, list markers), then 3+ backticks or tildes. The run's first
// character is escaped so the line is text. Text backticks are escaped
// already; this catches tilde fences, which need the line start.
const FENCE_LINE_RE = /^((?:[ \t>]|[-*+][ \t]|\d{1,9}[.)][ \t])*)(`{3,}|~{3,})/gm;
function neutralizeFences(text) {
  return text.replace(FENCE_LINE_RE, (_, pre, run) => pre + '\\' + run);
}

// Inline code whose content starts with "=" or "$=" is a Dataview inline
// query (DQL or JavaScript) in Obsidian: a zero-width space in front keeps it
// plain code.
const INLINE_QUERY_RE = /^\s*\$?=/;
const ZERO_WIDTH_SPACE = String.fromCharCode(0x200B);

function longestBacktickRun(s) {
  let best = 0;
  for (const m of s.matchAll(/`+/g)) best = Math.max(best, m[0].length);
  return best;
}

// Inline code: one line (a line break inside could start a markdown block),
// fenced with more backticks than any run in the code.
function inlineCode(s) {
  let t = s.replace(/\s*\n\s*/g, ' ');
  if (!t.trim()) return '';
  if (INLINE_QUERY_RE.test(t)) t = ZERO_WIDTH_SPACE + t;
  const fence = '`'.repeat(longestBacktickRun(t) + 1);
  const pad = (t.startsWith('`') || t.endsWith('`')) ? ' ' : '';
  return fence + pad + t + pad + fence;
}

// A fenced block, its fence longer than any backtick run in the code. It is
// put back as a top-level block, with blank lines around it, so no container
// it lands in (list item, heading, quote, link text) can end early and leave
// code lines to be read as markdown.
function codeBlock(s) {
  const t = s.replace(/^\n+|\s+$/g, '');
  if (!t.trim()) return '';
  const fence = '`'.repeat(Math.max(3, longestBacktickRun(t) + 1));
  return `\n\n${fence}\n${t}\n${fence}\n\n`;
}

function restoreCode(text, code) {
  return text.replace(CODE_REF_RE, (_, k) => code[Number(k)] ?? '');
}

// Rewrite html so the converters see only markup they can trust: each text
// node decoded once and escaped (escapeText); each tag rebuilt with clean,
// double-quoted, escaped attributes (a "<img ...>" inside a quoted attribute
// can no longer look like a tag); <pre>/<code> replaced by a placeholder for
// their finished markdown (pushed to code); RCDATA content (<textarea>,
// <title>) escaped as text; RAWTEXT content (<script>, <style>, ...) dropped.
// Linear: every scan moves forward.
function escapeMarkup(html, code) {
  const n = html.length;
  let out = '', i = 0;
  while (i < n) {
    const lt = html.indexOf('<', i);
    const end = lt === -1 ? n : lt;
    if (end > i) out += escapeText(decodeEntities(html.slice(i, end)));
    if (lt === -1) break;
    if (html.startsWith('<!--', lt)) { const e = html.indexOf('-->', lt + 4); i = e === -1 ? n : e + 3; continue; }
    if (html[lt + 1] === '!' || html[lt + 1] === '?') { const e = html.indexOf('>', lt); i = e === -1 ? n : e + 1; continue; }
    if (!isTagStart(html, lt)) { out += '&lt;'; i = lt + 1; continue; }
    const t = readTag(html, lt);
    i = t.end;
    if (t.close) { out += `</${t.name}>`; continue; }
    if (t.name === 'pre' || t.name === 'code') {
      if (t.selfClose) continue;
      const [body, next] = readCodeText(html, i, t.name);
      i = next;
      code.push(t.name === 'pre' ? codeBlock(body) : inlineCode(body));
      out += CODE_OPEN + (code.length - 1) + CODE_CLOSE;
      continue;
    }
    if (RCDATA_TAGS.has(t.name) || RAWTEXT_TAGS.has(t.name)) {
      const closeRe = new RegExp(`</${t.name}(?![\\w-])`, 'gi');
      closeRe.lastIndex = i;
      const c = t.selfClose ? null : closeRe.exec(html);
      const stop = t.selfClose ? i : (c ? c.index : n);
      if (RCDATA_TAGS.has(t.name)) out += `<${t.name}>${escapeText(decodeEntities(html.slice(i, stop)))}`;
      i = stop;  // the close tag is read on the next turn
      continue;
    }
    let attrs = '';
    for (const [k, v] of t.attrs) {
      if (CLEAN_ATTR_NAME_RE.test(k)) attrs += ` ${k}="${escapeAttr(decodeEntities(v))}"`;
    }
    out += `<${t.name}${attrs}${t.selfClose ? ' /' : ''}>`;
  }
  return out;
}

// Value of the attribute with exactly this name in a tag escapeMarkup rebuilt
// (or that tag's attribute text), decoded, or null: `src` is not read out of
// `data-src="…"`.
function attrValue(tag, name) {
  const m = new RegExp(`(?<![\\w-])${name}="([^"]*)"`, 'i').exec(tag);
  return m ? decodeEntities(m[1]) : null;
}

function stripTags(html) {
  return html.replace(/<[^>]+>/g, '');
}

// One pass, so "&amp;lt;" is "&lt;" (text), never "<". Named entities other
// than these are left as written.
const NAMED_ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };
function decodeEntities(text) {
  return text.replace(/&(?:#(\d+)|#x([0-9a-f]+)|(amp|lt|gt|quot|apos|nbsp));/gi, (_m, dec, hex, name) => {
    if (name) return NAMED_ENTITIES[name.toLowerCase()];
    const cp = dec !== undefined ? parseInt(dec, 10) : parseInt(hex, 16);
    if (!(cp > 0 && cp <= 0x10ffff) || (cp >= 0xd800 && cp <= 0xdfff)) return String.fromCharCode(0xfffd);
    return String.fromCodePoint(cp);
  });
}

try {
  const html = await getHtml();
  if (titleOnly) {
    stdout.write(extractTitle(html) + '\n');
  } else {
    const title = extractTitle(html);
    const md = htmlToMarkdown(html, baseUrl);
    if (title) stdout.write(`# ${title}\n\n`);
    stdout.write(md + '\n');
  }
} catch (err) {
  process.stderr.write(`error: ${err.message}\n`);
  process.exit(1);
}
