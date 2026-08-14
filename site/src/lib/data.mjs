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

/** Frühester Anstoß einer Spieltags-Datei. */
function firstKickoff(md) {
  return md.matches.reduce((min, m) => (m.kickoff_utc < min ? m.kickoff_utc : min), '9999');
}

/** Alle Spieltags-Dateien, chronologisch (älteste zuerst).
 *
 * Sortiert nach Anstoß, nicht nach (season, matchday): Wettbewerbe teilen sich
 * die Rundennummern, WM-Runde 8 stünde sonst hinter Bundesliga-Spieltag 1 und
 * bliebe für immer "der aktuelle Spieltag". */
export function loadMatchdays() {
  return readDirJson('matchdays').sort((a, b) =>
    firstKickoff(a).localeCompare(firstKickoff(b))
  );
}

/** Wettbewerb der zuletzt getippten Runde (bl1, wm26, ...) oder undefined. */
export function activeCompetition(matchdays) {
  return matchdays.at(-1)?.competition;
}

const COMPETITION_LABELS = { wm26: 'WM 2026', wm2026: 'WM 2026', bl1: 'Bundesliga' };

/** Anzeigename eines Wettbewerbs; unbekannte Schlüssel bleiben unverändert. */
export function competitionLabel(competition) {
  return COMPETITION_LABELS[competition] ?? competition ?? '';
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

/** Saison-Bonusfragen (data/bonus/) des aktiven Wettbewerbs oder null. */
export function loadBonus(competition) {
  return readDirJson('bonus').find((b) => b.competition === competition) ?? null;
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
