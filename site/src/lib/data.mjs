// Liest die JSON-"Datenbank" des Repos (data/) zur Build-Zeit.
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const DATA_DIR = fileURLToPath(new URL('../../../data/', import.meta.url));

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf-8'));
}

function readDirJson(dir) {
  const abs = join(DATA_DIR, dir);
  if (!existsSync(abs)) return [];
  return readdirSync(abs)
    .filter((f) => f.endsWith('.json'))
    .map((f) => readJson(join(abs, f)));
}

/** Alle Spieltags-Dateien, chronologisch (älteste zuerst). */
export function loadMatchdays() {
  return readDirJson('matchdays').sort(
    (a, b) => a.season - b.season || a.matchday - b.matchday
  );
}

/** Punkteabrechnungen, Schlüssel: `${competition}_${season}_${matchday}`. */
export function loadResults() {
  const map = new Map();
  for (const r of readDirJson('results')) {
    map.set(`${r.competition}_${r.season}_${r.matchday}`, r);
  }
  return map;
}

export function resultsFor(matchday, results) {
  return results.get(`${matchday.competition}_${matchday.season}_${matchday.matchday}`);
}

/** Ergebnis + Punkte eines Einzelspiels aus der Abrechnung. */
export function scoredMatch(match, resultReport) {
  return resultReport?.matches.find(
    (m) => m.home === match.home && m.away === match.away && m.points !== undefined
  );
}

/** Paper-Kombiwetten (data/kombi/), älteste zuerst (die .enc-Dateien daneben
 * sind kein JSON und tauchen hier nicht auf). */
export function loadKombis() {
  return readDirJson('kombi').sort((a, b) => a.id.localeCompare(b.id));
}

/** Selbstlern-Zustand (engine/learn.py) oder null. */
export function loadLearning() {
  const path = join(DATA_DIR, 'learning', 'state.json');
  return existsSync(path) ? readJson(path) : null;
}

export function loadBacktest(mode) {
  const path = join(DATA_DIR, 'backtests', `${mode}.json`);
  return existsSync(path) ? readJson(path) : null;
}

export function formatKickoff(iso) {
  return (
    new Date(iso).toLocaleString('de-DE', {
      timeZone: 'Europe/Berlin',
      weekday: 'short',
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }) + ' Uhr'
  );
}
