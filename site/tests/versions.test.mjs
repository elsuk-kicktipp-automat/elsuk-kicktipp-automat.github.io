import assert from 'node:assert/strict';
import test from 'node:test';
import { loadMatchdays, loadVersions, versionFor } from '../src/lib/data.mjs';

const versions = [
  { version: 1, since_utc: '2026-07-02T21:49:18Z', model_version: 'a' },
  { version: 2, since_utc: '2026-07-04T05:42:49Z', model_version: 'b' },
  { version: 3, since_utc: '2026-10-09T19:26:21Z', model_version: 'b' },
];

test('a stamped tip keeps its version, whatever the kickoff says', () => {
  const match = { kickoff_utc: '2026-10-10T13:30:00Z', factors: { method_version: 2 } };
  const found = versionFor(match, versions);
  assert.equal(found.version, 2);
  assert.equal(found.derived, false);
});

test('an older tip gets the version that applied at kickoff', () => {
  const found = versionFor({ kickoff_utc: '2026-10-09T18:30:00Z', factors: {} }, versions);
  assert.equal(found.version, 2);
  assert.equal(found.derived, true);
  assert.equal(versionFor({ kickoff_utc: '2026-10-10T13:30:00Z' }, versions).version, 3);
});

test('a kickoff before the first version has no version', () => {
  assert.equal(versionFor({ kickoff_utc: '2026-06-01T12:00:00Z' }, versions), null);
});

// Gegenprobe mit den echten Daten: Die abgeleitete Version muss dieselbe
// Modellkennung tragen wie die Spieltags-Datei, in der das Spiel steht.
test('every published match maps to a version with the model of its matchday file', () => {
  const real = loadVersions();
  for (const md of loadMatchdays()) {
    for (const match of md.matches) {
      if (match.status === 'sealed') continue;
      const found = versionFor(match, real);
      assert.ok(found, `${md.competition} ${md.matchday}: ${match.home} ohne Version`);
      assert.equal(
        found.model_version,
        md.model_version,
        `${md.competition} ${md.matchday}: ${match.home} -> Version ${found.version}`
      );
    }
  }
});

test('known tips map to the version they were made with', () => {
  const real = loadVersions();
  const pick = (competition, matchday, home) =>
    loadMatchdays()
      .find((md) => md.competition === competition && md.matchday === matchday)
      .matches.find((m) => m.home === home);
  // unversiegelte erste Runde, getippt am 03.07.2026
  assert.equal(versionFor(pick('wm26', 4, 'Kolumbien'), real).version, 1);
  // Achtelfinale, versiegelt am 04.07.2026 morgens
  assert.equal(versionFor(pick('wm26', 5, 'Kanada'), real).version, 2);
  // letzter Text des alten Sprachmodells, Anstoß 09.10.2026 20:30 Uhr
  assert.equal(versionFor(pick('bl1', 5, 'Borussia Dortmund'), real).version, 7);
});
