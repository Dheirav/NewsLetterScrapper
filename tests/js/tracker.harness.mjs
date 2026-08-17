/**
 * Test harness for the reading tracker embedded in templates/newsletter_web.html.
 *
 * The tracker ships inside a Jinja template, so there is nothing importable.
 * This harness extracts the <script> block, stubs the handful of browser APIs
 * it touches (IntersectionObserver, document, navigator, timers), and drives it
 * through scenarios with a controllable clock.
 *
 * Run: node tests/js/tracker.test.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const HERE = dirname(fileURLToPath(import.meta.url));
const TEMPLATE = join(HERE, '..', '..', 'templates', 'newsletter_web.html');

/**
 * Pull the tracker source out of the template by slicing between the script
 * tags. Deliberately not a regex: the tracker body contains brace-paren
 * sequences that make a lazy pattern backtrack into the wrong match.
 */
export function extractTracker() {
  const html = readFileSync(TEMPLATE, 'utf8');
  const open = html.indexOf('<script>');
  const close = html.indexOf('</script>', open);
  if (open === -1 || close === -1) {
    throw new Error('reading tracker <script> block not found in template');
  }
  const source = html.slice(open + '<script>'.length, close).trim();
  if (!source.startsWith('(function')) {
    throw new Error(`expected the tracker IIFE, got: ${source.slice(0, 60)}`);
  }
  return source;
}

/**
 * Build a fake DOM for `slugs` stories, each containing `sections` section
 * elements, and run the tracker against it.
 */
export function createHarness({ slugs, sections = ['executive_summary', 'context'] }) {
  let now = 1_000_000;
  const posted = [];
  const observers = [];
  const listeners = new Map();
  const intervals = [];

  const makeSection = (name, card) => ({
    dataset: { section: name },
    closest: (sel) => (sel.includes('story') ? card : null),
  });

  const cards = slugs.map((slug) => {
    const card = { dataset: { topicSlug: slug } };
    card._sections = sections.map((s) => makeSection(s, card));
    return card;
  });
  const allSections = cards.flatMap((c) => c._sections);

  let scrollHeight = 5000;
  let clientHeight = 1000;
  let scrollTop = 0;

  const sandbox = {
    Date: { now: () => now },
    Math,
    JSON,
    Map,
    Set,
    Array,
    Blob: class Blob {
      constructor(parts) { this.text = parts.join(''); }
    },
    Error,
    setInterval: (fn) => { intervals.push(fn); return intervals.length; },
    navigator: {
      sendBeacon: (url, blob) => {
        posted.push({ via: 'beacon', url, body: JSON.parse(blob.text) });
        return true;
      },
    },
    fetch: (url, opts) => {
      posted.push({ via: 'fetch', url, body: JSON.parse(opts.body) });
      return Promise.resolve({ ok: true });
    },
    document: {
      body: { dataset: { newsletterDate: '2026-08-17' } },
      documentElement: {
        get scrollHeight() { return scrollHeight; },
        get clientHeight() { return clientHeight; },
        get scrollTop() { return scrollTop; },
      },
      visibilityState: 'visible',
      querySelectorAll: (sel) =>
        sel.includes('data-section') && !sel.includes('story') ? allSections : cards,
      addEventListener: (evt, fn) => {
        if (!listeners.has(evt)) listeners.set(evt, []);
        listeners.get(evt).push(fn);
      },
    },
    window: {
      addEventListener: (evt, fn) => {
        if (!listeners.has(evt)) listeners.set(evt, []);
        listeners.get(evt).push(fn);
      },
    },
    IntersectionObserver: class {
      constructor(cb, opts) {
        this.cb = cb;
        this.threshold = opts && opts.threshold;
        observers.push(this);
      }
      observe() {}
    },
  };

  vm.createContext(sandbox);
  vm.runInContext(extractTracker(), sandbox);

  // observers[0] watches sections (threshold .5), observers[1] watches cards (.3)
  const [sectionObserver, storyObserver] = observers;

  return {
    posted,
    advance(ms) { now += ms; },
    setScroll({ height, viewport, top }) {
      if (height !== undefined) scrollHeight = height;
      if (viewport !== undefined) clientHeight = viewport;
      if (top !== undefined) scrollTop = top;
    },
    /** Bring a story card into or out of view. */
    viewStory(slug, isIntersecting) {
      const target = cards.find((c) => c.dataset.topicSlug === slug);
      storyObserver.cb([{ target, isIntersecting }]);
    },
    /** Bring one section of one story into view. */
    viewSection(slug, name) {
      const card = cards.find((c) => c.dataset.topicSlug === slug);
      const target = card._sections.find((s) => s.dataset.section === name);
      sectionObserver.cb([{ target, isIntersecting: true }]);
    },
    /** Fire the 60s interval flush. */
    tick() { intervals.forEach((fn) => fn()); },
    /** Simulate the tab being hidden / the page unloading. */
    hide() {
      sandbox.document.visibilityState = 'hidden';
      (listeners.get('visibilitychange') || []).forEach((fn) => fn());
    },
    pagehide() { (listeners.get('pagehide') || []).forEach((fn) => fn()); },
    eventsFor(slug) { return posted.filter((p) => p.body.topic_slug === slug); },
    totalSecondsFor(slug) {
      return this.eventsFor(slug).reduce((n, p) => n + p.body.time_spent_seconds, 0);
    },
  };
}
