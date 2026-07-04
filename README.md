# Der Automat

Ein selbstlernender KI-Tipper für die Kicktipp-Runde. Konzept: siehe [concept.md](concept.md).
Website: **<https://elsuk-kicktipp-automat.github.io/>**

**Stand: Phase 2-4 im Test-/Härtungsbetrieb** – Statistik-Engine (OpenLigaDB +
ELO + Quoten, Dixon-Coles-Modell, Kicktipp-Punkteoptimierung, Backtesting),
LLM-Begründungsschicht (Groq), Paper-Betting, Astro-Website, Hash-Versiegelung,
GitHub-Actions-Automatisierung und verifizierte Kicktipp-Abgabe per Playwright.
Die Pipeline läuft mit der **WM 2026** (bis 19.07.2026), danach wird per
`config.yaml` auf die Bundesliga 2026/27 umgestellt.

## Wie es funktioniert

1. **Spieldaten:** [OpenLigaDB](https://api.openligadb.de) (kostenlos, kein API-Key).
   Für die WM 2026 die Liga `wm26` (durchgehend gepflegt, Gruppenphase bis
   Finale, stabile Team-IDs). Teams werden trotzdem über normalisierte Namen
   identifiziert – bei anderen Community-Ligen (z.B. Vereinswettbewerbe) sind
   Namensvarianten wie „Bosnien-Herzegowina" vs. „Bosnien und Herzegowina" die Norm.
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
   resultTypeID 2 = „Ende der offiziellen Spielzeit", in `wm26` sauber getrennt
   von „nach Verlängerung" und „nach Elfmeterschießen") – ein Unentschieden ist
   ein gültiger und tippbarer Ausgang.
6. **Quoten-Prior:** [The Odds API](https://the-odds-api.com) (Free Tier, 500
   Requests/Monat). Anders als ELO gibt es hier keinen historischen Endpunkt –
   Quoten fließen deshalb nicht ins Fitting, sondern verschieben zur
   Vorhersagezeit die fertige Wahrscheinlichkeitsmatrix Richtung Markt
   (`engine/market.py`, Gewicht konfigurierbar über `odds.market_weight`).
   Ergebnis wird auf einen Blend aus Markt- und Modellmeinung optimiert, die
   relative Form (Dixon-Coles-rho) bleibt vom Modell bestimmt. Cache pro
   Kalendertag hält den Verbrauch weit unter dem Freikontingent.
7. **Paper-Betting:** rein theoretische 1X2-Wetten ohne echte Wettabgabe.
   Referenz ist `tipico_de`, falls verfügbar; sonst wird der Durchschnitt der
   gelieferten Bookmaker-Quoten genutzt. Der Einsatz wird per konservativem
   Fractional-Kelly aus der rohen Modellwahrscheinlichkeit vor Markt-Blend
   berechnet und bei 100 EUR gedeckelt (`paper_betting` in `config.yaml`).
8. **LLM-Begründung:** [Groq](https://console.groq.com) (Free Tier,
   `llama-3.3-70b-versatile`) formuliert den Begründungstext aus denselben
   Modellzahlen in natürlicher Sprache; ohne Key/Netzwerk springt automatisch
   die Template-Begründung ein. Zusätzlich prüft der News-Check Kicker,
   Sportschau und die BILD-News-Sitemap auf aktuelle Schlagzeilen zu den Teams.
   Eine daraus abgeleitete Tipp-Anpassung läuft nur als Schattentipp mit und
   ändert den offiziellen Tipp nicht.
9. **Kicktipp-Abgabe:** Der Playwright-Bot trägt versiegelte Tipps bei
   kicktipp.de ein und liest die gespeicherten Werte danach serverseitig zurück.
   Abweichungen, fehlende Spiele oder verworfene Eingaben machen den Workflow rot.

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

# Tipps für die nächste anstehende Runde -> data/predictions/ (gitignored!)
python -m engine.cli predict

# Tipps versiegeln: Hash öffentlich, Klartext verschlüsselt (braucht SEAL_SECRET)
python -m engine.cli seal

# Tipps nach Anstoß enthüllen
python -m engine.cli unseal

# Abrechnung der enthüllten Tipps gegen die realen Ergebnisse -> data/results/
python -m engine.cli evaluate

# Backtests -> data/backtests/  (--mode club | national | all)
python -m engine.cli backtest
```

Alle erzeugten Daten sind menschenlesbares JSON im Repo (`data/`) – das Repo
ist die Datenbank. API-Antworten werden unter `data/cache/` (gitignored) gecacht.

## Fairness-Mechanismus & Automatisierung

Klartext-Tipps liegen **nie** vor Anstoß im öffentlichen Repo
(`data/predictions/` ist gitignored). Stattdessen (concept.md §5):

1. **Versiegeln:** Pro Spiel wird nur der SHA-256-Hash von
   `(Teams, Anstoß, Tipp, Begründung, Paper-Bet, Salt)` veröffentlicht
   (`data/matchdays/`); der Klartext liegt Fernet-verschlüsselt in
   `data/sealed/*.enc`. Schlüssel: `SEAL_SECRET` (GitHub Actions Secret /
   lokale `.env`). Der Commit-Zeitstempel beweist den Zeitpunkt.
2. **Entsiegeln:** Ab Anstoß wird der Klartext samt Salt in die
   Spieltags-Datei geschrieben – der 5-Minuten-Worker hält die normale
   Veröffentlichungsverzögerung klein. Jeder kann den Hash nachrechnen
   (Anleitung auf der Website unter „Wie ich denke").

GitHub Actions übernimmt den Betrieb (`.github/workflows/`):

| Workflow | Zeitplan | Aufgabe |
| --- | --- | --- |
| `spieltag.yml` | stündlich | predict (Spiele im 4h-Fenster vor Anstoß) → seal → evaluate → learn → Commit → Kicktipp-Abgabe (verifiziert, aus den versiegelten .enc) |
| `unseal.yml` | alle 5 min | fällige Tipps ab Anstoß enthüllen + abrechnen (früher Abbruch ohne fällige Spiele) |
| `deploy-site.yml` | bei Daten-/Site-Änderungen | Astro-Build → GitHub Pages |

Deploys laufen über `main`. Manuelle Spieltags-/Unseal-Läufe auf `main`
triggern bei geänderten Daten anschließend den Site-Deploy.

K.o.-Pläne mit Platzhaltern („Sieger SF 12") werden unterstützt: Sobald
Nachzügler-Paarungen feststehen, versiegelt der nächste Lauf sie als weiteren
Batch derselben Runde.

## Website

Astro-Site unter `site/`, deployed auf
<https://elsuk-kicktipp-automat.github.io/>:
**Spieltag** (versiegelte/enthüllte Tipps), **Archiv**, **Bilanz**
(Live-Punkte + Backtests), **Wie ich denke** (Modell & Hash-Verifikation).
Die Site wird statisch gebaut; ein kleiner Versions-Check (`site-version.json`)
lädt nach einem neuen Pages-Deploy einmal hart neu, damit Navigation zwischen
Spieltag, Archiv und Bilanz nicht auf einer alten HTML-Version hängen bleibt.

```bash
cd site && npm install && npm run dev   # lokale Vorschau
```

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

### Ergebnisse (Lauf vom 03.07.2026, Schema 4/3/2)

| Backtest | Spiele | Punkte | Ø/Spiel | ELO-Favorit 2:1 | immer 1:1 |
| --- | --- | --- | --- | --- | --- |
| Bundesliga 2023/24 | 306 | 408 | 1,333 | 395 | 244 |
| Bundesliga 2024/25 | 306 | 383 | 1,252 | 379 | 206 |
| Bundesliga 2025/26 | 306 | 418 | 1,366 | 437 | 218 |
| **Bundesliga gesamt** | **918** | **1209** | **1,317** | **1211** | **668** |
| **WM 2026 (out-of-sample)** | **82** | **138** | **1,683** | **123** | **72** |

Befunde:

- Bei der WM schlägt das Modell beide Baselines klar. Voraussetzung war die
  starke L2-Regularisierung für Nationalteams: mit dem Club-Wert (0.2) über-
  erklären die Team-Parameter die 3–7 Turnierspiele pro Team und übertönen den
  ELO-Term (107 statt 138 Punkte).
- In der Bundesliga liegt das Modell gleichauf mit der ELO-Favorit-2:1-Baseline
  (1209 vs. 1211 – bei 918 Spielen Rauschen). Diese Baseline ist unter dem
  4/3/2-Schema sehr stark; Mehrwert gegenüber ihr soll v.a. die Quoten-Schicht
  (Phase 3+, siehe concept.md) bringen.

## Konfiguration (`config.yaml`)

- `competition` / `leagues` / `season` – aktiver Wettbewerb (WM 2026: zwei Ligen)
- `team_type` – `club` (clubelo.com) oder `national` (eloratings.net)
- `neutral_venue` – Heimvorteil abschalten (WM)
- `kicktipp.points` – Punkteschema der Runde; `advance` = Zusatzfrage
  „Wer kommt weiter?" bei K.o.-Spielen (separat ausgewiesen, 0 = deaktiviert)
- `model.*` – Zeitgewichtung, Regularisierung, Tor-Raster, ELO-Prior
- `backtest.*` – Parameter der beiden Backtest-Modi

Umstieg auf Bundesliga 2026/27: Kommentarblock am Kopf der config.yaml.

## Projektstruktur

```text
engine/                Python-Engine
  cli.py               Einstiegspunkt: predict / seal / unseal / evaluate / backtest
  predict.py           Prognose der nächsten Runde -> data/predictions/ (gitignored)
  seal.py              Hash-Versiegelung + Entsiegelung nach Anstoß
  evaluate.py          Punkteabrechnung -> data/results/
  backtest.py          Backtests (club + national) -> data/backtests/
  model.py             Dixon-Coles-Poisson mit ELO-Term
  market.py            Quoten-Blending der Wahrscheinlichkeitsmatrix (Vorhersagezeit)
  paper_betting.py     theoretische Wetten, Einsatzlogik, Abrechnung
  llm.py               LLM-Begründungstexte (Groq) mit Template-Fallback
  kicktipp_bot.py      Playwright-Abgabe bei kicktipp.de mit Verifikation
  optimizer.py         Kicktipp-Punktelogik + EV-Optimierung + Baselines
  teams.py             Team-Identität über normalisierte Namen
  sources/
    openligadb.py      Spielplan/Ergebnisse mit Cache
    elo.py             ELO-Adapter (clubelo.com | eloratings.net)
    odds.py            Quoten-Adapter (The Odds API) mit Cache + Entviggen
tests/                 pytest-Suite (ohne Netzwerkzugriff lauffähig)
data/                  JSON-„Datenbank" (cache/ ist gitignored)
  matchdays/           öffentliche Spieltags-Dateien (Hashes bzw. Enthülltes)
  sealed/              verschlüsselte Klartext-Tipps bis zum Anstoß
  mappings/            Namens-Zuordnung OpenLigaDB -> ELO-/Quoten-Quellen
site/                  Astro-Website (GitHub Pages)
.github/workflows/     GitHub Actions (Spieltag, Entsiegeln, Site-Deploy)
config.yaml            Wettbewerb, Punkteschema, Modell- und Backtest-Parameter
.env.example           Vorlage für Secrets späterer Phasen (Phase 1 braucht keine)
```

## Roadmap

- [x] **Phase 1:** Engine (OpenLigaDB + ELO), Modell, Punkteoptimierung,
      Backtesting, WM-2026-Testbetrieb
- [x] **Phase 2:** Astro-Site, Hash-Versiegelung, GitHub-Actions-Betrieb,
      Deployment auf Pages
- [x] **Phase 3:** Quoten-Prior (The Odds API), LLM-Begründungstexte (Groq)
      und News-Check über Kicker, Sportschau und BILD-News-Sitemap. News-gestützte
      Tipp-Adjustierung läuft bewusst nur als Schattentipp.
- [x] **Phase 4:** Kicktipp-Bot (Playwright-Abgabe mit Rücklese-Verifikation)
- [x] **Phase 5 (Kern):** Selbstlernen (engine/learn.py): LLM-Vertrauensregler
      (Schatten-Anpassungen werden erst nach nachweislich positiver Punktebilanz
      scharf geschaltet) und gelerntes Quotengewicht (Log-Loss-Grid-Search mit
      Pseudo-Count-Regularisierung); Schattentipper, Kalibrierung und
      Bilanz-Dashboard liefern die Datengrundlage. Offen: Lernkurven-Grafik
