import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import * as data from '../src/lib/data.mjs';

const source = readFileSync(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
const frontmatter = source.split('---')[1].replace(/^import .*;\n/gm, '');
const browserScript = source.match(/<script is:inline>([\s\S]*?)<\/script>/)[1];
const HOUR = 3_600_000;
const friday = Date.parse('2026-09-18T18:30:00Z');
const saturday = Date.parse('2026-09-19T13:30:00Z');
const sunday = Date.parse('2026-09-20T17:30:00Z');
const restart = Date.parse('2026-10-09T18:30:00Z');
const match = (home, t) => ({ home, away: 'Gast', kickoff_utc: new Date(t).toISOString() });
const round = (matchday, matches) => ({
  competition: 'bl1', season: 2026, matchday, stage: `${matchday}. Spieltag`, matches,
});
const fullRound = round(4, [match('Freitag', friday), match('Samstag', saturday), match('Sonntag', sunday)]);
const current = round(4, [fullRound.matches[0]]);
const schedule = [fullRound, round(5, [match('Neustart', restart)])];

function buildPage(now, rounds = schedule) {
  return vm.runInNewContext(`${frontmatter}\n({ lastKick, recentUntil, kickoffs, banners });`, {
    ...data,
    Date: class extends Date { static now() { return now; } },
    loadMatchdays: () => [current],
    loadSchedule: () => rounds,
    loadResults: () => new Map(),
    loadKombis: () => [],
    loadBonus: () => null,
  });
}

test('countdown includes untipped weekend games without duplicating tipped games', () => {
  const before = buildPage(friday - HOUR);
  assert.equal(before.kickoffs.length, 4);
  assert.equal(before.kickoffs[0].label, 'Freitag – Gast');
  const after = buildPage(friday + 3 * HOUR);
  assert.equal(after.kickoffs[0].t, saturday);
});

test('pause and expiry use the final scheduled game, even when only Friday is tipped', () => {
  const page = buildPage(friday + 3 * HOUR);
  assert.equal(page.lastKick, new Date(sunday).toISOString());
  assert.equal(page.banners[0].from, sunday + 2 * HOUR);
  assert.equal(page.banners[0].until, restart);
  assert.equal(page.recentUntil, sunday + 4 * 24 * HOUR);
});

test('updated schedule times take precedence over old tipped kickoff times', () => {
  const postponed = round(4, [match('Freitag', sunday + HOUR), ...fullRound.matches.slice(1)]);
  const page = buildPage(friday - HOUR, [postponed, schedule[1]]);
  assert.equal(page.kickoffs.length, 4);
  assert.equal(page.kickoffs[0].t, saturday);
  assert.equal(page.lastKick, new Date(sunday + HOUR).toISOString());
});

test('tipped games remain available without a schedule', () => {
  const page = buildPage(friday - HOUR, []);
  assert.equal(page.kickoffs.length, 1);
  assert.equal(page.kickoffs[0].t, friday);
  assert.equal(page.lastKick, new Date(friday).toISOString());
});

function openPage(now, withCountdown = true) {
  let clock = now;
  let tick;
  const host = { textContent: '' };
  const banner = { dataset: { from: String(restart - HOUR), until: String(restart) }, hidden: true };
  const cd = {
    dataset: {
      kickoffs: JSON.stringify([
        { t: restart, label: 'Neustart – Gast', when: 'Freitag' },
        { t: restart + 19 * HOUR, label: 'Samstagsspiel', when: 'Samstag' },
      ]),
      matchMs: String(2 * HOUR),
    },
    children: [{ textContent: '' }, { textContent: '' }, { textContent: '' }],
    hidden: true,
  };
  vm.runInNewContext(browserScript, {
    Date: { now: () => clock },
    document: {
      querySelectorAll: () => [banner],
      querySelector: (selector) => selector === '[data-kickoffs]'
        ? (withCountdown ? cd : null) : (banner.hidden ? null : host),
    },
    setInterval: (fn) => { tick = fn; },
  });
  return { banner, cd, host, advance(t) { clock = t; tick(); } };
}

test('an open page switches from pause countdown to live banner at kickoff', () => {
  const page = openPage(restart - 1000);
  assert.equal(page.banner.hidden, false);
  assert.equal(page.host.textContent, 'noch 00:00:01');
  assert.equal(page.cd.hidden, true);
  page.advance(restart);
  assert.equal(page.banner.hidden, true);
  assert.equal(page.cd.hidden, false);
  assert.equal(page.cd.children[0].textContent, 'Läuft gerade');
  assert.equal(page.cd.children[1].textContent, 'Neustart – Gast');
});

test('an open page moves its countdown into a newly visible pause banner', () => {
  const page = openPage(restart - 2 * HOUR);
  assert.equal(page.banner.hidden, true);
  assert.equal(page.cd.hidden, false);
  page.advance(restart - HOUR);
  assert.equal(page.banner.hidden, false);
  assert.equal(page.cd.hidden, true);
  assert.equal(page.host.textContent, 'noch 01:00:00');
});

test('time windows still update when no countdown games are available', () => {
  const page = openPage(restart - 1000, false);
  assert.equal(page.banner.hidden, false);
  page.advance(restart);
  assert.equal(page.banner.hidden, true);
});
