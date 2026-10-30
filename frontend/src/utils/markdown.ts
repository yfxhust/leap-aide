import { marked, Marked } from 'marked';
import katex from 'katex';
import Prism from 'prismjs';
import 'prismjs/themes/prism-tomorrow.css';
import 'prismjs/components/prism-javascript';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-yaml';
import 'prismjs/components/prism-markdown';
import 'prismjs/components/prism-css';
import 'prismjs/components/prism-go';
import 'prismjs/components/prism-rust';

// ── URL auto-link stash (stash → let marked handle rest → restore) ──

const STASH_PREFIX = '\x02';
const STASH_SUFFIX = '\x03';

function createStash(): { map: Map<string, string>; counter: number } {
  return { map: new Map(), counter: 0 };
}

function stashPut(stash: ReturnType<typeof createStash>, content: string): string {
  const key = `${STASH_PREFIX}${stash.counter++}${STASH_SUFFIX}`;
  stash.map.set(key, content);
  return key;
}

function restore(stash: ReturnType<typeof createStash>, html: string): string {
  if (stash.map.size === 0) return html;
  let result = html;
  stash.map.forEach((value, key) => {
    result = result.split(key).join(value);
  });
  return result;
}

// ── Math extension for marked (KaTeX) ──
// Registered as a marked inline extension so that marked's tokenizer respects
// its own priority rules — codespan (backticks) is matched before inline math,
// preventing $ inside `code` from being misinterpreted as LaTeX.

const mathExtension = {
  name: 'math' as const,
  level: 'inline' as const,
  start(src: string) {
    return src.match(/\$/)?.index;
  },
  tokenizer(src: string) {
    // Block math: $$...$$
    const blockMatch = src.match(/^\$\$([\s\S]+?)\$\$/);
    if (blockMatch) {
      return { type: 'math' as const, raw: blockMatch[0], text: blockMatch[1], display: true };
    }
    // Inline math: $...$ (skip empty or double-dollar)
    const inlineMatch = src.match(/^\$([^\s$][^$]*?[^\s$])\$/);
    if (inlineMatch) {
      return { type: 'math' as const, raw: inlineMatch[0], text: inlineMatch[1], display: false };
    }
  },
  renderer(token: { text: string; display: boolean }) {
    try {
      const html = katex.renderToString(token.text, { displayMode: token.display, strict: false });
      return token.display
        ? `<div class="katex-block">${html}</div>`
        : `<span class="katex-inline">${html}</span>`;
    } catch {
      const escaped = escapeHTML(token.text);
      const cls = token.display ? 'katex-block katex-error' : 'katex-inline katex-error';
      return token.display
        ? `<div class="${cls}">${escaped}</div>`
        : `<span class="${cls}">${escaped}</span>`;
    }
  },
};

// ── Configure marked ──

const MAX_HIGHLIGHT_LENGTH = 3000;

// Custom code block renderer with Prism syntax highlighting
// Note: skipHighlight is handled at the renderMarkdown level by swapping renderers
const renderer = new marked.Renderer();

renderer.code = function ({ text, lang }: { text: string; lang?: string }): string {
  const langAttr = lang || 'text';
  const trimmed = text.endsWith('\n') ? text.slice(0, -1) : text;
  let highlighted: string;
  if (trimmed.length > MAX_HIGHLIGHT_LENGTH) {
    highlighted = escapeHTML(trimmed);
  } else {
    try {
      const grammar = Prism.languages[langAttr];
      highlighted = grammar
        ? Prism.highlight(trimmed, grammar, langAttr)
        : escapeHTML(trimmed);
    } catch {
      highlighted = escapeHTML(trimmed);
    }
  }
  return `<pre class="language-${langAttr}"><code class="language-${langAttr}">${highlighted}</code></pre>`;
};

// Fast renderer — skips syntax highlighting (used during streaming)
const fastRenderer = new marked.Renderer();
fastRenderer.code = function ({ text, lang }: { text: string; lang?: string }): string {
  const langAttr = lang || 'text';
  const trimmed = text.endsWith('\n') ? text.slice(0, -1) : text;
  return `<pre class="language-${langAttr}"><code class="language-${langAttr}">${escapeHTML(trimmed)}</code></pre>`;
};

// ── Helpers ──

function escapeHTML(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function sanitizeHTML(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/\son\w+="[^"]*"/gi, '')
    .replace(/javascript:/gi, '');
}

// ── Main export ──

export function renderMarkdown(raw: string, skipHighlight: boolean = false): string {
  if (!raw) return '';

  const MAX_INPUT = 100_000;
  if (raw.length > MAX_INPUT) {
    raw = raw.slice(0, MAX_INPUT) + '\n\n*(content truncated for display)*';
  }

  // Strip YAML frontmatter
  const textToRender = raw.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '');

  const stash = createStash();
  let html = textToRender;

  // 1. Protect plain URLs before marked's GFM autolink swallows non-ASCII text.
  //    Stash them as inline code so marked leaves them untouched.
  html = html.replace(
    /(https?:\/\/[\w\-._~:/?#[\]@!$&'()*+,;=%]+)/g,
    (_, url) => stashPut(stash, `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`),
  );

  // 2. Parse markdown with marked (math extension handles $...$ / $$...$$)
  const mkd = new Marked();
  mkd.use({ extensions: [mathExtension] });
  html = mkd.parse(html, {
    renderer: skipHighlight ? fastRenderer : renderer,
    breaks: true,
    gfm: true,
    async: false,
  }) as string;

  // 3. Restore stashed content (auto-linked URLs)
  html = restore(stash, html);

  // 5. Sanitize
  html = sanitizeHTML(html);

  return html;
}

// ── Relative file-link interception ─────────────────────────────────────────
// Markdown like `[中文](SKILL.zh-CN.md)` renders an <a href="SKILL.zh-CN.md">.
// Inside the SPA that href resolves against the current route (invalid URL).
// Callers of rendered markdown pass a click handler; this extracts the link
// target when the click landed on a relative, in-document file link.

export function relativeFileLinkTarget(event: {
  target: EventTarget | null;
}): string | null {
  const el = event.target;
  if (!(el instanceof Element)) return null;
  const a = el.closest('a');
  if (!a) return null;
  const href = a.getAttribute('href') || '';
  // Skip in-page anchors, external/scheme links (http:, mailto:),
  // protocol-relative (//) and site-absolute (/) paths.
  if (!href || href.startsWith('#') || /^(?:[a-z][a-z0-9+.-]*:|\/\/|\/)/i.test(href)) {
    return null;
  }
  return decodeURIComponent(href.split('#')[0].split('?')[0]) || null;
}

export function highlightCode(code: string, lang: string): string {
  try {
    const grammar = Prism.languages[lang] || Prism.languages.text;
    return Prism.highlight(code, grammar, lang);
  } catch {
    return escapeHTML(code);
  }
}

// ── Language resolution (file extension → Prism language) ──

const LANG_MAP: Record<string, string> = {
  md: 'markdown',
  markdown: 'markdown',
  txt: 'plain',
  text: 'plain',
  js: 'javascript',
  ts: 'typescript',
  jsx: 'javascript',
  tsx: 'typescript',
  py: 'python',
  json: 'json',
  yml: 'yaml',
  yaml: 'yaml',
  css: 'css',
  sh: 'bash',
  bash: 'bash',
};

export function resolveLanguage(fileName?: string, lang?: string): string {
  if (lang) return lang;
  if (!fileName) return 'markdown';
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  return LANG_MAP[ext] || 'plain';
}

export { Prism };
