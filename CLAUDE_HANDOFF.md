# Claude Handoff

## Aktueller Stand 2026-07-03: Site-Redesign ist auf main deployed

Wichtig fuer Claude / naechsten Agenten:

- Aktueller produktiver Stand ist `main` bei Commit `9f6cfcb` (`Document manual result source`).
- GitHub Actions nach diesem Commit:
  - `Tests`: success
  - `Site deployen`: success
- GitHub Pages ist aktualisiert:
  - <https://elsuk-kicktipp-automat.github.io/der-automat/>
- Lokaler Arbeitsbaum war nach dem Deploy sauber bis auf untracked `.claude/`.
  Diese lokale Claude-Konfiguration nicht committen.

### Was am 2026-07-03 gemacht wurde

Manueller Ergebnis-Fallback:

- Problem: OpenLigaDB-API meldete Schweiz - Algerien (`matchID 82070`) weiterhin offen:
  - `matchIsFinished: false`
  - `matchResults: []`
- Die OpenLigaDB-Webseite zeigte dagegen bereits das Ergebnis `2:0`.
- Die Webseite ist eine Blazor-Server-App; das sichtbare Ergebnis steht nicht im initialen HTML und laesst sich nicht sauber per statischem HTML-Scraping lesen.
- Deshalb wurde ein kostenloser, nachvollziehbarer Repo-Fallback eingebaut:
  - `data/manual_results/*.json`
  - `engine.evaluate.load_manual_results()`
  - `engine.evaluate.main()` merged diese Overrides nach den API-Ergebnissen.
- Konkreter Override:
  - `data/manual_results/wm2026_2026_md04.json`
  - Schweiz - Algerien, OpenLigaDB-Match-ID `82070`, Ergebnis `[2, 0]`
- Danach wurde `data/results/wm2026_2026_md04.json` neu bewertet:
  - `points_total: 2`
  - `matches_scored: 1`
  - Schweiz - Algerien: Tipp `2:1`, Ergebnis `2:0`, `2` Punkte
- Test ergaenzt:
  - `tests/test_evaluate.py::TestEvaluateMatchday::test_loads_manual_results`

Wichtig:

- Manuelle Overrides sind bewusst committed, nicht gitignored.
- Sie sollen nur fuer Faelle genutzt werden, in denen die API haengt oder nachweislich unvollstaendig ist.
- Der Override ist transparent durch `openligadb_match_id` und `source_url` dokumentiert.

Site-Redesign / Dashboard-Polish:

- `site/src/layouts/Layout.astro`
  - dunkles Dashboard-Theme
  - globale KPI-Kacheln (`.stat-grid`, `.stat-tile`)
  - Balkendiagramm-Styles (`.bar-chart`, `.bar-row`, `.bar-track`, `.bar-fill`)
  - Wahrscheinlichkeitsbalken und Punkt-Badges
- `site/src/pages/index.astro`
  - aktueller Spieltag zeigt KPI-Kacheln fuer bereits gewertete Spiele
- `site/src/components/MatchCard.astro`
  - Wahrscheinlichkeitsbalken Heim/Remis/Auswaerts
  - Punkt-Badges fuer gewertete Tipps
- `site/src/pages/bilanz.astro`
  - Live-Bilanz mit KPI-Kacheln
  - Balkenvergleich Automat vs. Schattentipper
  - Backtest-KPIs und Backtest-Balkenvergleich

### Build-Falle in `bilanz.astro`

Der Astro-Build brach mehrfach mit einer irrefuehrenden Meldung ab:

```text
Unterminated regular expression
Location: site/src/pages/bilanz.astro:<line>:56
```

Tatsaechlicher Kontext:

- Python-Tests waren auf GitHub gruen.
- Nur `npm run build` / Astro-Site-Build war rot.
- Die gemeldete Zeile zeigte auf normales HTML in `bilanz.astro`, war aber nur ein Symptom.
- Der Compiler erzeugte kaputten JS-Code, solange in `bilanz.astro` folgende Dinge kombiniert waren:
  - TypeScript-Typannotationen im Frontmatter
  - Fragment-Kurzform `<>...</>`
  - verschachtelte Tabellen-`<strong>`-Tags in dynamischen Rows
  - JSX/`class:list`/Inline-Style-Ausdruecke in den Balken-Maps

Der lokal gruen gebaute Fix:

- `bilanz.astro` ist im Frontmatter wieder plain JavaScript, keine TS-Typen.
- Balken werden per `barHtml(...)` vorbereitet und mit `set:html` eingesetzt.
- Die Fragment-Kurzform wurde durch normale `<div>`-Wrapper ersetzt.
- Die problematischen Tabellen-`<strong>`-Tags wurden entfernt.
- Lokaler Check:

```powershell
cd site
npm run build
```

Ergebnis:

```text
4 page(s) built
Complete!
```

### Lokales Setup fuer Site-Checks

Python-Tests laufen im Repo-Root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest tests/ -v
```

Astro-Site im `site/`-Ordner:

```powershell
cd site
npm ci
npm run build
```

Hinweise:

- `npm ci` kann Warnungen zu `tsconfck`, `audit` und `allow-scripts` ausgeben; der erfolgreiche Build ist entscheidend.
- Lokal wurde Node `v24.18.0` / npm `11.16.0` genutzt.
- GitHub Actions nutzt Node 22.

---

Stand: 2026-07-02, nach Codex-Zwischenarbeit im bestehenden Claude-Umbau.

## Ausgangslage bei Übernahme

Claude hatte den Phase-1-Umbau bereits weitgehend angelegt:

- `config.yaml` ist auf WM-2026-Testbetrieb gestellt (`competition: wm2026`, `leagues: [wm2026, mb]`, `team_type: national`, `neutral_venue: true`).
- OpenLigaDB wurde nach `engine/sources/openligadb.py` verschoben.
- Kicktipp-Logik wurde von `engine/kicktipp.py` nach `engine/optimizer.py` umbenannt.
- Neu angelegt waren unter anderem `engine/sources/elo.py`, `engine/teams.py`, `engine/cli.py`, `engine/predict.py`, `engine/evaluate.py`, `tests/test_elo.py`, `data/mappings/`.
- `data/backtests/latest.json` war staged geloescht; `data/backtests/club.json` und `data/backtests/national.json` waren untracked vorhanden.
- Der Git-Index war gemischt: Renames/Deletion staged, Code- und Datenänderungen unstaged/untracked.

## Was Codex danach gemacht hat

### 1. Zustand geprueft

Ausgefuehrte Checks:

```bash
git status --short --branch
git log --oneline -5
git diff --stat
git diff --cached --stat
git diff --name-status
git diff --cached --name-status
rg --files
```

Ergebnis:

- Letzter Commit: `d84edeb Phase 1: Engine mit OpenLigaDB, Dixon-Coles-Modell, Kicktipp-Punkteoptimierung und Backtesting`.
- Aktueller Arbeitsbaum enthaelt einen groesseren Umbau, aber keinen neuen Commit.
- Tests waren zunaechst nicht mit `python`/`python3` ausfuehrbar, weil `python` fehlte und globales `python3` kein `pytest` hatte.
- Projektlokale `.venv` existiert und funktioniert.

### 2. Tests ausgefuehrt

Erfolgreich ausgefuehrt:

```bash
.venv/bin/python -m pytest tests/
```

Vor Codex-Codeaenderungen:

```text
45 passed in 0.80s
```

Nach Codex-Codeaenderungen und neuem Test:

```text
46 passed in 0.59s
```

Ausserdem:

```bash
git diff --check
```

Ergebnis: keine Whitespace-/Diff-Check-Fehler.

### 3. Kleine Codekorrekturen gemacht

Geaenderte Dateien durch Codex:

- `engine/predict.py`
- `engine/model.py`
- `tests/test_model.py`
- diese Datei `CLAUDE_HANDOFF.md`

Details:

#### `engine/predict.py`

Entfernt:

```python
fetch_season
```

aus dem Import, weil es im neuen `predict`-Pfad ungenutzt war.

Geaendert:

```python
lookback = config.get("lookback_seasons", 2)
```

zu:

```python
lookback = config.get("backtest", {}).get("club", {}).get("lookback_seasons", 2)
```

Grund: `lookback_seasons` liegt im neuen Config-Schema unter `backtest.club.lookback_seasons`, nicht mehr top-level.

#### `engine/model.py`

Warmstart beim Re-Fit erweitert:

- vorher wurden beim vorhandenen `self.params` zwar Attack/Defense, Intercept und Rho teilweise uebernommen.
- Codex hat ergaenzt, dass auch `home_adv` und `elo_beta` als Startwerte wiederverwendet werden.
- Bei `neutral_venue=True` bleibt `home_adv` weiterhin hart auf `0.0`.
- Bei deaktiviertem ELO wird `elo_beta` weiterhin auf `0.0` gesetzt.

Wichtig: Beim ersten Patch war kurz eine Einrueckung in der Defense-Warmstart-Zeile falsch. Das wurde sofort korrigiert und mit einem neuen Test abgesichert.

#### `tests/test_model.py`

Neu hinzugefuegt:

```python
test_refit_warmstarts_all_shared_parameters
```

Der Test stubbt `engine.model.minimize`, liest den Startvektor `x0` aus und prueft:

- Attack-Warmstart fuer alle Teams
- Defense-Warmstart fuer alle Teams
- Intercept
- Home Advantage
- Rho
- ELO Beta

Grund: Genau die Warmstart-Logik war durch normale numerische Tests nicht ausreichend abgedeckt.

### 4. Club-Backtest reproduziert

Ausgefuehrt:

```bash
.venv/bin/python -m engine.cli backtest --mode club
```

Ergebnis:

```text
Saison 2023/24: 406 Punkte (Ø 1.327/Spiel) | Baselines: ELO-Favorit 2:1 = 395, immer 1:1 = 244
Saison 2024/25: 383 Punkte (Ø 1.252/Spiel) | Baselines: ELO-Favorit 2:1 = 379, immer 1:1 = 206
Saison 2025/26: 418 Punkte (Ø 1.366/Spiel) | Baselines: ELO-Favorit 2:1 = 437, immer 1:1 = 218
Gesamt (club): 1207 Punkte in 918 Spielen (Ø 1.315/Spiel) | Baselines: ELO-Favorit 1211, immer 1:1 668
```

Wichtig:

- `data/backtests/club.json` wurde dadurch neu geschrieben.
- Der aktuelle Modell-Default liegt im Club-Backtest knapp unter der ELO-Favorit-2:1-Baseline (`1207` vs. `1211`).
- Das ist kein Testfehler, aber ein fachlicher Befund: der Default sollte vor einem finalen Commit wahrscheinlich noch bewertet oder dokumentiert werden.

### 5. Kleine Variantenrunde ausgefuehrt

Ausgefuehrt wurde ein lokaler Variantenvergleich ueber den Club-Backtest aus vorhandenem Cache.

Ergebnis:

```text
('current', 1207, 1.315, 1211, 668)
('no_elo', 1205, 1.313, 989, 668)
('max_goals_8', 1209, 1.317, 1211, 668)
('elo_weaker_penalty', 1205, 1.313, 1211, 668)
('elo_stronger_prior', 1209, 1.317, 1211, 668)
('l2_0_2', 1209, 1.317, 1211, 668)
('xi_0_001', 1181, 1.286, 1211, 668)
```

Interpretation:

- `max_goals: 8`, `elo.beta_prior: 0.20` oder `l2_penalty: 0.2` verbessern einzeln minimal auf `1209`.
- Keine dieser Einzelvarianten schlaegt die ELO-Favorit-2:1-Baseline von `1211`.
- `no_elo` senkt die Modellpunkte leicht, aber auch die ELO-Baseline wird dann unbrauchbar, weil keine Ratings geladen werden.
- `xi_0_001` ist deutlich schlechter.

### 6. Groessere Variantenrunde abgebrochen

Codex startete danach eine groessere Grid-Search ueber:

- `max_goals in [6, 8]`
- `beta_prior in [0.15, 0.2, 0.25]`
- `l2_penalty in [0.05, 0.1, 0.2, 0.3]`

Diese lief zu lange und wurde wegen der Nutzeranfrage nach Dokumentation mit `Ctrl-C` abgebrochen.

Status:

- Keine Ergebnisse aus dieser Grid-Search wurden verwendet.
- Es laeuft kein offener Prozess mehr.

## Aktueller Git-Zustand nach Codex

Ungefaehr erwarteter Status:

```text
 M .gitignore
 M README.md
 M config.yaml
D  data/backtests/latest.json
 M engine/backtest.py
 M engine/model.py
RM engine/kicktipp.py -> engine/optimizer.py
RM engine/openligadb.py -> engine/sources/openligadb.py
 M tests/test_model.py
 M tests/test_openligadb.py
RM tests/test_kicktipp.py -> tests/test_optimizer.py
?? CLAUDE_HANDOFF.md
?? data/backtests/club.json
?? data/backtests/national.json
?? data/mappings/
?? engine/cli.py
?? engine/evaluate.py
?? engine/predict.py
?? engine/sources/__init__.py
?? engine/sources/elo.py
?? engine/teams.py
?? tests/test_elo.py
```

Kein Commit wurde gemacht.

## Naechste sinnvolle Schritte fuer Claude

1. `git status --short` erneut pruefen.
2. Tests laufen lassen:

   ```bash
   .venv/bin/python -m pytest tests/
   ```

3. Entscheiden, ob die Defaults fachlich akzeptabel sind, obwohl der Club-Backtest knapp unter der ELO-Favorit-Baseline liegt.
4. Falls Tuning gewuenscht ist, kleine gezielte Varianten testen; die grosse Grid-Search war langsam.
5. National-Backtest nur mit Vorsicht neu ausfuehren: `backtest_national` nutzt aktuell `force_refresh=True` und kann Netzwerk benoetigen.
6. Vor einem Commit den Index sauber machen, weil derzeit Renames/Deletion staged sind, aber neue Dateien und Codeaenderungen teilweise untracked/unstaged sind.

