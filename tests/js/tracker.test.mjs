/**
 * Reading-tracker regression tests.
 *
 * Run: node tests/js/tracker.test.mjs
 *
 * Each test below corresponds to a defect that was corrupting the reading_events
 * table, and therefore the personalisation profile built from it.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { createHarness } from './tracker.harness.mjs';

const MIN = 60_000;

test('time is reported once, not re-sent cumulatively on every flush', () => {
  // Was: flush() posted the running total without clearing it, so a tab left
  // open for an hour turned 30s of reading into ~30 rows summing to minutes.
  const h = createHarness({ slugs: ['tariffs'] });
  h.viewStory('tariffs', true);

  for (let i = 0; i < 5; i++) {
    h.advance(MIN);
    h.tick();
  }

  const events = h.eventsFor('tariffs');
  const total = h.totalSecondsFor('tariffs');
  assert.equal(events.length, 5, 'one event per minute elapsed');
  assert.equal(total, 300, `5 minutes in view should report 300s, got ${total}`);
  assert.ok(events.every((e) => e.body.time_spent_seconds === 60),
    'each flush reports only the delta since the last one');
});

test('a story left on screen keeps accruing time after a flush', () => {
  // Was: flush() set start=null on visible cards. IntersectionObserver only
  // fires on change, so the card you are still reading never restarted its
  // clock — the most-read story recorded the least time.
  const h = createHarness({ slugs: ['tariffs'] });
  h.viewStory('tariffs', true);

  h.advance(MIN); h.tick();          // first flush
  h.advance(MIN); h.tick();          // second flush — must not be zero

  const seconds = h.eventsFor('tariffs').map((e) => e.body.time_spent_seconds);
  assert.deepEqual(seconds, [60, 60], `expected [60,60], got ${JSON.stringify(seconds)}`);
});

test('a finished story is reported exactly once, however many flushes follow', () => {
  // This is the scenario that isolates the double-count. Once a card is out of
  // view its total is final, so the old code — which never cleared the counter
  // — re-posted that same total on every subsequent 60s tick. Three ticks after
  // a 30s read produced three rows and 90 recorded seconds.
  const h = createHarness({ slugs: ['tariffs'] });
  h.viewStory('tariffs', true);
  h.advance(30_000);
  h.viewStory('tariffs', false);

  h.tick();
  h.tick();
  h.tick();

  assert.equal(h.eventsFor('tariffs').length, 1,
    `expected a single row, got ${h.eventsFor('tariffs').length}`);
  assert.equal(h.totalSecondsFor('tariffs'), 30,
    `expected 30s recorded, got ${h.totalSecondsFor('tariffs')}`);
});

test('time stops accruing once a story scrolls out of view', () => {
  const h = createHarness({ slugs: ['tariffs'] });
  h.viewStory('tariffs', true);
  h.advance(30_000);
  h.viewStory('tariffs', false);     // scrolled away
  h.advance(10 * MIN);               // long gone
  h.tick();

  assert.equal(h.totalSecondsFor('tariffs'), 30);
});

test('sections are attributed to their own story, not shared page-wide', () => {
  // Was: one page-global Set copied onto every event, so opening one section
  // on one story recorded it against all thirty.
  const h = createHarness({ slugs: ['tariffs', 'elections'] });
  h.viewStory('tariffs', true);
  h.viewStory('elections', true);
  h.viewSection('tariffs', 'context');       // only tariffs was expanded
  h.advance(MIN);
  h.tick();

  const tariffs = h.eventsFor('tariffs')[0].body.sections_read;
  const elections = h.eventsFor('elections')[0].body.sections_read;
  assert.ok(tariffs.includes('context'), 'tariffs should record its own section');
  assert.ok(!elections.includes('context'),
    `elections must not inherit tariffs' sections, got ${JSON.stringify(elections)}`);
});

test('scroll percent is 100, never NaN, when the page fits the viewport', () => {
  // Was: division by (scrollHeight - clientHeight) === 0 gave NaN, which
  // serialises to null and is rejected by the endpoint with a 422.
  const h = createHarness({ slugs: ['tariffs'] });
  h.setScroll({ height: 800, viewport: 800, top: 0 });   // nothing to scroll
  h.viewStory('tariffs', true);
  h.advance(MIN);
  h.tick();

  const pct = h.eventsFor('tariffs')[0].body.scroll_percent;
  assert.ok(Number.isFinite(pct), `scroll_percent must be a number, got ${pct}`);
  assert.equal(pct, 100);
});

test('scroll percent is clamped to 0..100 under overscroll', () => {
  const h = createHarness({ slugs: ['tariffs'] });
  h.setScroll({ height: 5000, viewport: 1000, top: 9999 });  // rubber-band overscroll
  h.viewStory('tariffs', true);
  h.advance(MIN);
  h.tick();

  assert.equal(h.eventsFor('tariffs')[0].body.scroll_percent, 100);
});

test('teardown uses sendBeacon, which survives an unloading document', () => {
  // Was: an awaited fetch() loop on beforeunload; browsers do not keep the
  // page alive for it, so a read-then-close session recorded nothing.
  const h = createHarness({ slugs: ['tariffs'] });
  h.viewStory('tariffs', true);
  h.advance(30_000);
  h.hide();

  const events = h.eventsFor('tariffs');
  assert.equal(events.length, 1, 'hiding the tab must flush');
  assert.equal(events[0].via, 'beacon', 'teardown must not rely on fetch');
  assert.equal(events[0].body.time_spent_seconds, 30);
});

test('a double teardown does not double-count', () => {
  // visibilitychange and pagehide can both fire on the same navigation.
  const h = createHarness({ slugs: ['tariffs'] });
  h.viewStory('tariffs', true);
  h.advance(30_000);
  h.hide();
  h.pagehide();

  assert.equal(h.totalSecondsFor('tariffs'), 30,
    'the second teardown should find the counters already cleared');
});

test('accidental scroll-past is not reported', () => {
  const h = createHarness({ slugs: ['tariffs'] });
  h.viewStory('tariffs', true);
  h.advance(500);                    // half a second
  h.viewStory('tariffs', false);
  h.tick();

  assert.equal(h.eventsFor('tariffs').length, 0);
});

test('sub-threshold time is carried forward, not discarded', () => {
  // Two 1.5s glances at the same story: neither alone clears the 2s filter,
  // but the first must survive the flush that could not report it so the pair
  // is eventually counted. Anything still under the threshold at the end stays
  // pending by design — that is the accidental-hover filter.
  const h = createHarness({ slugs: ['tariffs'] });
  for (let i = 0; i < 2; i++) {
    h.viewStory('tariffs', true);
    h.advance(1500);
    h.viewStory('tariffs', false);
    h.tick();
  }
  assert.equal(h.eventsFor('tariffs').length, 1, 'only the second flush clears the filter');
  assert.equal(h.totalSecondsFor('tariffs'), 3, 'both glances are counted, not just the second');
});

test('each story is tracked independently', () => {
  const h = createHarness({ slugs: ['tariffs', 'elections'] });
  h.viewStory('tariffs', true);
  h.advance(20_000);
  h.viewStory('tariffs', false);
  h.viewStory('elections', true);
  h.advance(40_000);
  h.tick();

  assert.equal(h.totalSecondsFor('tariffs'), 20);
  assert.equal(h.totalSecondsFor('elections'), 40);
});

test('the posted payload matches the ReadingEventIn schema', () => {
  const h = createHarness({ slugs: ['tariffs'] });
  h.viewStory('tariffs', true);
  h.advance(MIN);
  h.tick();

  const { url, body } = h.eventsFor('tariffs')[0];
  assert.equal(url, '/api/events/reading');
  assert.deepEqual(Object.keys(body).sort(), [
    'date', 'scroll_percent', 'sections_read', 'time_spent_seconds', 'topic_slug',
  ]);
  assert.equal(body.date, '2026-08-17');
  assert.ok(Number.isInteger(body.time_spent_seconds) && body.time_spent_seconds >= 0);
  assert.ok(body.scroll_percent >= 0 && body.scroll_percent <= 100);
  assert.ok(Array.isArray(body.sections_read));
});
