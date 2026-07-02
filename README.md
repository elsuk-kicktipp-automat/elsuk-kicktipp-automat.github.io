# Der Automat

Ein selbstlernender KI-Tipper für die Kicktipp-Runde. Konzept: siehe [concept.md](concept.md).

**Stand: Phase 1** – Statistik-Engine mit OpenLigaDB-Anbindung, ELO-Ratings,
Dixon-Coles-Poisson-Modell, Kicktipp-Punkteoptimierung und Backtesting.
Die Pipeline läuft im Test-/Härtungsbetrieb mit der **WM 2026** (bis 19.07.2026),
danach wird per `config.yaml` auf die Bundesliga 2026/27 umgestellt.

## Wie es funktioniert

1. **Spieldaten:** [OpenLigaDB](https://api.openligadb.de) (kostenlos, kein API-Key).
   Die WM 2026 ist dort auf zwei Ligen verteilt: `wm2026` (Gruppenphase) und `mb`
   (K.o.-Runde) – die Engine führt sie zusammen. Teams werden über normalisierte
   Namen identifiziert, weil die Community-Ligen keine stabilen Team-IDs haben.
2. **ELO-Ratings:** gemeinsame Schnittstelle mit zwei Adaptern (`team_type` in
   config.yaml): [clubelo.com](http://clubelo.com/API) für Vereine (mit historischen
   Ständen pro Stichtag) und [eloratings.net](https://eloratings.net) für
   Nationalteams (nur aktueller Stand). Namens-Zuordnung: `data/mappings/`.
3. **Modell:** Dixon-Coles-Poisson. Erwartete Tore pro Team aus Angriffs-/
   Abwehrstärke (exponentiell abklingend gewichtete Form), Heimvorteil (bei der
   WM per `neutral_venue: true` abgeschaltet) und ELO-Differenz. Der ELO-Koeffizient
   wird mitgeschätzt und zum Prior regularisiert – so trägt ELO die Prognose,
   solange wenig Spieldaten da sind (WM-Gruppenphase), und die gefitteten
   Teamstärken übernehmen mit wachsender Datenmenge. Output pro Spiel: die
   vollständige Wahrscheinlichkeitsmatrix aller Ergebnisse von 0:0 bis 6:6.
4. **Punkteoptimierung:** Getippt wird nicht das wahrscheinlichste Ergebnis,
   sondern der Tipp mit dem höchsten **Punkte-Erwartungswert** unter dem
   Kicktipp-Schema der Runde (config.yaml, Default 4/3/2). Kicktipp-Standard:
   bei Unentschieden gibt es keine Tordifferenz-Punkte, nur exakt oder Tendenz.
5. **K.o.-Spiele:** Gewertet wird das Ergebnis nach 90 Minuten (OpenLigaDB
   resultTypeID 2, bei der WM „Endergebniss (o.E.)" = ohne Elfmeterschießen) –
   ein Unentschieden ist ein gültiger und tippbarer Ausgang.

## Setup

Voraussetzung: Python ≥ 3.11

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Benutzung

```bash
# Tests (laufen ohne Netzwerk)
python -m pytest tests/

# Tipps für die nächste anstehende Runde -> data/predictions/
python -m engine.cli predict

# Abrechnung der Tipps gegen die realen Ergebnisse -> data/results/
python -m engine.cli evaluate

# Backtests -> data/backtests/  (--mode club | national | all)
python -m engine.cli backtest
```

Alle erzeugten Daten sind menschenlesbares JSON im Repo (`data/`) – das Repo
ist die Datenbank. API-Antworten werden unter `data/cache/` (gitignored) gecacht.

## Backtesting

- **club:** Rollierend über die letzten 3 Bundesliga-Saisons. Vor jedem Spieltag
  wird nur auf bis dahin gespielten Partien gefittet (plus 2 Vorsaisons als
  Warmup); die ELO-Stände kommen historisch korrekt vom jeweiligen Stichtag.
- **national:** WM 2026 out-of-sample – Gruppenphase + bisherige K.o.-Spiele,
  Runde für Runde nur mit den davor gespielten Partien. Einschränkung:
  eloratings.net bietet keine historischen Stände, die Retro-Zahlen tragen
  dadurch einen leichten Lookahead-Bias (für *künftige* Spiele irrelevant).
- Verglichen wird gegen zwei Baselines: **(a)** immer 2:1 für den ELO-Favoriten,
  **(b)** immer 1:1. Reports inkl. Punkten pro Spieltag/Runde und Trefferquoten
  (exakt/Differenz/Tendenz): `data/backtests/club.json` bzw. `national.json`.

Aktuelle Ergebnisse: siehe Konsolen-Output von `python -m engine.cli backtest`
bzw. die JSON-Reports.

## Konfiguration (`config.yaml`)

- `competition` / `leagues` / `season` – aktiver Wettbewerb (WM 2026: zwei Ligen)
- `team_type` – `club` (clubelo.com) oder `national` (eloratings.net)
- `neutral_venue` – Heimvorteil abschalten (WM)
- `kicktipp.points` – Punkteschema der Runde
- `model.*` – Zeitgewichtung, Regularisierung, Tor-Raster, ELO-Prior
- `backtest.*` – Parameter der beiden Backtest-Modi

Umstieg auf Bundesliga 2026/27: Kommentarblock am Kopf der config.yaml.

## Projektstruktur

```text
engine/                Python-Engine
  cli.py               Einstiegspunkt: predict / evaluate / backtest
  predict.py           Prognose der nächsten Runde -> data/predictions/
  evaluate.py          Punkteabrechnung -> data/results/
  backtest.py          Backtests (club + national) -> data/backtests/
  model.py             Dixon-Coles-Poisson mit ELO-Term
  optimizer.py         Kicktipp-Punktelogik + EV-Optimierung + Baselines
  teams.py             Team-Identität über normalisierte Namen
  sources/
    openligadb.py      Spielplan/Ergebnisse mit Cache
    elo.py             ELO-Adapter (clubelo.com | eloratings.net)
tests/                 pytest-Suite (ohne Netzwerkzugriff lauffähig)
data/                  JSON-„Datenbank" (cache/ ist gitignored)
  mappings/            Namens-Zuordnung OpenLigaDB -> ELO-Quellen
site/                  Astro-Website (Phase 2, noch leer)
.github/workflows/     GitHub Actions (Phase 2+, noch leer)
config.yaml            Wettbewerb, Punkteschema, Modell- und Backtest-Parameter
.env.example           Vorlage für Secrets späterer Phasen (Phase 1 braucht keine)
```

## Roadmap

- [x] **Phase 1:** Engine (OpenLigaDB + ELO), Modell, Punkteoptimierung,
      Backtesting, WM-2026-Testbetrieb
- [ ] **Phase 2:** Astro-Site, Hash-Versiegelung, Deployment
- [ ] **Phase 3:** LLM-Schicht (Dossier, Adjustierung, Begründungen)
- [ ] **Phase 4:** Kicktipp-Bot (Playwright-Abgabe)
- [ ] **Phase 5:** Selbstlernen, Schattentipper, Dashboard
