# Konzept: „Der Automat" – Ein selbstlernender KI-Tipper für die Kicktipp-Runde

## 1. Zielbild

Ein vollständig autonomes System, das ohne laufende Kosten betrieben wird und pro Spieltag folgendes leistet:

1. **Daten sammeln** aus mehreren kostenlosen Fußball-APIs und offenen Quellen (Form, Tabelle, xG, Quoten, Verletzungen, News).
2. **Prognose berechnen** mit einem hybriden Ansatz: statistisches Modell (Poisson/ELO) plus LLM-Schicht für Kontext und Begründung.
3. **Tipps versiegeln**: Vor Anstoß wird nur ein kryptografischer Hash der Tipps veröffentlicht (Fairness-Beweis), der Klartext samt Begründung erscheint erst nach Anstoß auf der Website.
4. **Tipps bei Kicktipp eintragen** – automatisch, rechtzeitig vor der Deadline.
5. **Lernen**: Nach jedem Spieltag werden die eigenen Prognosen gegen die realen Ergebnisse ausgewertet und die Modellgewichte angepasst.

---

## 2. Architektur (alles kostenlos)

```
┌─────────────────────────── GitHub Repository ───────────────────────────┐
│                                                                          │
│  /data          → JSON-„Datenbank" (Spiele, Tipps, Ergebnisse, Gewichte) │
│  /engine        → Python: Datensammler + Prognosemodell + LLM-Aufruf     │
│  /site          → Astro-Website                                          │
│  /.github       → GitHub Actions (extern per workflow_dispatch getaktet) │
│                                                                          │
└──────────┬──────────────────────┬──────────────────────┬────────────────┘
           │                      │                      │
     GitHub Actions          GitHub Pages /         Playwright-Bot
     (Scheduler, gratis      Cloudflare Pages       → kicktipp.de
      2.000 min/Monat)       (Astro-Hosting)          (Tippabgabe)
           │
           ├─→ OpenLigaDB, football-data.org, The Odds API, ClubElo …
           └─→ LLM-API (Gemini/Groq Free Tier oder Claude API)
```

**Kernidee:** Es gibt keinen dauerhaft laufenden Server. cron-job.org löst die GitHub Actions per `workflow_dispatch` aus; GitHubs eigene Cron-Events werden wegen beobachteter Ausfälle nicht verwendet. Das Repository selbst ist die Datenbank (versionierte JSON-Dateien – dadurch ist jede Prognose und jede Modelländerung transparent nachvollziehbar, was für das „Beweis-System" ein Feature ist). Die Astro-Site wird bei jedem Datenupdate neu gebaut und deployed.

### Hosting-Optionen für die Website

| Option | Vorteil | Nachteil |
|---|---|---|
| **GitHub Pages** | Null Setup, gleiche Plattform | Nur statisch |
| **Cloudflare Pages** | Schnellere Builds, großzügige Limits, eigene Domain leichter | Zweiter Account nötig |
| Netlify/Vercel Free | Komfortabel | Build-Minuten-Limits knapper |

Empfehlung: **Astro auf Cloudflare Pages oder GitHub Pages** – statisch reicht völlig, da alle Inhalte zu Build-Zeit feststehen.

---

## 3. Datenquellen (kostenlos)

| Quelle | Was sie liefert | Limit / Hinweis |
|---|---|---|
| **OpenLigaDB** (openligadb.de) | Bundesliga-Spielplan, Live-Ergebnisse, Tabelle, historische Saisons | Komplett frei, kein API-Key, deutsche Community-API – **Rückgrat des Systems** |
| **football-data.org** | Spielpläne, Ergebnisse, Tabellen für Bundesliga, PL, CL u.a. | Free Tier: 10 Requests/min, 12 Wettbewerbe |
| **The Odds API** (the-odds-api.com) | Buchmacherquoten (1X2, Over/Under) | Free: 500 Requests/Monat – reicht für 1–2 Abrufe pro Spieltag |
| **API-Football** (api-football.com) | Aufstellungen, Verletzungen, Statistiken | Free: 100 Requests/Tag |
| **ClubElo** (clubelo.com/API) | ELO-Ratings aller europäischen Teams, historisch | Frei als CSV |
| **FBref / Understat** | xG-Daten (Expected Goals) | Kein offizielles API; sparsames Scraping oder Community-Datasets |
| **RSS/News** (Kicker, Sportschau, Vereinsseiten) | Verletzungen, Sperren, Trainerwechsel, Stimmung | Frei; wird dem LLM als Kontext gegeben |
| **Wetter-API** (open-meteo.com) | Wetter am Spielort | Frei, marginaler Faktor, aber „professionell" |

Die Quoten sind dabei die wichtigste Einzelquelle: Buchmacherquoten aggregieren bereits das Wissen des gesamten Marktes und sind einzeln kaum zu schlagen. Das Modell nutzt sie als Basis-Prior und versucht, durch die übrigen Signale Feinheiten (v.a. die exakte Torzahl) besser zu treffen.

---

## 4. Die Prognose-Engine: Hybrid aus Statistik + LLM

### Schicht 1 – Statistisches Fundament (deterministisch, kostenlos, backtestbar)

Ein **Dixon-Coles/Poisson-Modell** schätzt für jedes Spiel die erwarteten Tore beider Teams (λ_heim, λ_gast) aus:

- Angriffs-/Abwehrstärke je Team (aus den letzten ~2 Saisons, exponentiell abklingend gewichtet)
- Heimvorteil (ligaspezifisch geschätzt)
- ELO-Differenz (ClubElo)
- xG-Trend der letzten 5–10 Spiele (Form jenseits der Ergebnisse)
- Implizite Wahrscheinlichkeiten aus den Quoten (nach Abzug der Buchmacher-Marge)

Daraus entsteht eine **vollständige Wahrscheinlichkeitsmatrix aller Ergebnisse** (0:0, 1:0, 2:1, …).

### Schicht 2 – Kicktipp-Punkteoptimierung (der entscheidende Trick)

Der häufigste Fehler naiver Tipper-Bots: Sie tippen das *wahrscheinlichste* Ergebnis. Für Kicktipp ist das falsch. Gesucht ist der Tipp mit dem **höchsten Punkte-Erwartungswert** unter dem konkreten Punkteschema der Runde (z.B. 4/3/2 für Ergebnis/Tordifferenz/Tendenz).

Beispiel: 2:1 ist selten das wahrscheinlichste Ergebnis, bringt aber oft mehr erwartete Punkte als 1:0, weil es bei Heimsiegen häufiger die Tordifferenz oder Tendenz trifft. Das System rechnet für jeden möglichen Tipp:

```
E[Punkte(Tipp)] = Σ über alle Ergebnisse: P(Ergebnis) × Punkte(Tipp, Ergebnis)
```

… und wählt das Maximum. Das Punkteschema eurer Runde wird als Konfiguration hinterlegt.

### Schicht 3 – LLM als Analyst & Erzähler

Ein LLM (siehe Optionen unten) bekommt pro Spiel ein Dossier: Modell-Output, Quoten, Tabellensituation, Form, News-Schnipsel (Verletzungen, Sperren, Trainerwechsel, „Muss-Siege"), H2H-Historie. Aufgaben:

1. **Sanity-Check & Adjustierung:** Das LLM darf den Modelltipp in engen Grenzen korrigieren (z.B. ±1 Tor), wenn es harte Gründe gibt, die im statistischen Modell nicht stecken (Stammtorwart verletzt, Team schont vor CL-Spiel, Abstiegs-Endspiel). Jede Abweichung muss begründet und geloggt werden – so lässt sich später messen, ob die LLM-Eingriffe Punkte bringen oder kosten.
2. **Begründungstext** für die Website: 3–6 Sätze pro Spiel, warum so getippt wurde, inkl. der wichtigsten Faktoren und der Konfidenz.

**LLM-Optionen (kostenlos bzw. quasi-kostenlos):**

- **Google Gemini API** – Free Tier reicht locker für 9 Spiele/Spieltag
- **Groq** – Free Tier mit offenen Modellen (GPT-OSS, Qwen), sehr schnell
- **Claude API** – qualitativ top, aber kostenpflichtig (bei 9 Spielen/Woche wenige Cent pro Spieltag)
- Fallback: läuft das LLM nicht, greift das rein statistische Modell mit templatebasierter Begründung – das System bleibt immer funktionsfähig.

### Schicht 4 – Selbstlernen

Nach jedem Spieltag läuft ein Evaluations-Job:

- **Punkteabrechnung** je Tipp: Wie viele Kicktipp-Punkte hat das System geholt? Wie viele hätte das reine Statistikmodell geholt, wie viele der reine Quoten-Favorit, wie viele der LLM-adjustierte Tipp? (Vier parallel geführte „Schattentipper" als Benchmark.)
- **Kalibrierung:** Brier-Score / Log-Loss der Wahrscheinlichkeiten gegen die realen Ergebnisse.
- **Gewichts-Update:** Die Mischgewichte (wie stark zählt Quote vs. ELO vs. xG vs. Form?) werden per einfacher Optimierung (Grid Search oder Gradient auf dem Log-Loss) auf dem wachsenden Datensatz nachjustiert – zu Beginn vorsichtig (viel Regularisierung), mit wachsender Datenmenge aggressiver.
- **LLM-Vertrauensregler:** Wenn die LLM-Eingriffe über z.B. 5 Spieltage messbar Punkte kosten, wird ihr erlaubter Einfluss automatisch reduziert (und umgekehrt).
- Alle Metriken landen als JSON im Repo und werden auf der Website als „Lernkurve" visualisiert.

Vor Saisonstart wird das Modell per **Backtesting** auf 3–5 historischen Bundesliga-Saisons (OpenLigaDB liefert alles) trainiert und validiert – so startet der Automat nicht bei null.

---

## 5. Fairness-Mechanismus: Tipps erst nach Anstoß sichtbar

Damit niemand behaupten kann, die Tipps seien nachträglich „geschönt" worden:

1. **T–24h bis T–2h vor Anstoß:** Engine läuft, Tipps stehen fest. Auf der Website erscheint pro Spiel nur: „Tipp abgegeben ✓" plus **SHA-256-Hash** von `(Tipp + Begründung + geheimer Salt)`. Der Commit-Zeitstempel im öffentlichen Repo beweist zusätzlich den Zeitpunkt.
2. **Gleichzeitig:** Tipp wird bei Kicktipp eingetragen (dort ist er ohnehin bis zur Deadline für Mitspieler unsichtbar bzw. nach eurer Rundeneinstellung).
3. **T+5min nach Anstoß:** Ein zweiter Cron-Job veröffentlicht Klartext-Tipp, Begründung und Salt. Jeder kann den Hash nachrechnen → Beweis, dass der Tipp vor Anstoß feststand.

Technisch: Die Klartext-Tipps liegen bis zum Anstoß **verschlüsselt** im Repo (Secret als GitHub Actions Secret) oder in einem privaten Gist – nicht im Klartext im öffentlichen Repo.

---

## 6. Kicktipp-Integration

Kicktipp bietet **keine offizielle API**. Der etablierte Weg (mehrere aktive Open-Source-Projekte existieren, z.B. `antonengelhardt/kicktipp-bot`, `tbrodbeck/kicktipp-bot-serverless`, `schwalle/kicktipp-betbot`):

- Login per Formular-POST bzw. Playwright (Headless-Browser), Login-Cookie wird wiederverwendet
- Navigation zur „Tippabgabe"-Seite der Runde, Felder ausfüllen, absenden
- Läuft problemlos in GitHub Actions (Playwright ist dort vorinstallierbar)
- Zugangsdaten ausschließlich als **GitHub Actions Secrets**, nie im Code

**Wichtige Hinweise:**
- Das ist Web-Automatisierung, keine offizielle Schnittstelle – Kicktipp kann Layout ändern (Bot bricht dann, Fix meist trivial) und automatisierte Nutzung ist von den Nutzungsbedingungen vermutlich nicht ausdrücklich gedeckt. Für eine private Freundesrunde mit eigenem Bot-Account ist das Risiko praktisch gering, aber ihr solltet es bewusst entscheiden. Prüft die aktuellen AGB selbst.
- Empfehlung: **eigener Kicktipp-Account nur für den Bot** („Der Automat"), nicht euer persönlicher.
- Fallback, falls die Abgabe scheitert: Benachrichtigung per E-Mail/ntfy.sh, damit ein Mensch manuell einträgt.

---

## 7. Die Website (Astro)

Statische Astro-Site, Rebuild bei jedem Daten-Commit. Seitenstruktur:

- **Startseite:** Aktueller Spieltag – vor Anstoß Hashes/„versiegelt", danach Tipps + Begründungen + Konfidenz
- **Spieltags-Archiv:** Alle bisherigen Tipps, Ergebnisse, geholte Punkte
- **Leistungs-Dashboard:** Punkteverlauf, Vergleich der Schattentipper (reine Statistik vs. LLM-adjustiert vs. Quoten-Favorit), Trefferquoten (exakt / Differenz / Tendenz), Kalibrierungs-Plot, Lernkurve der Modellgewichte
- **„Wie ich denke":** Transparenzseite, die das Modell erklärt
- Optional: Vergleich mit eurer Kicktipp-Rangliste (manuell gepflegt oder mitgescraped)

---

## 8. Ablauf eines Spieltags (automatisiert)

| Zeitpunkt | Job (GitHub Action) |
|---|---|
| Mo 06:00 | Ergebnisse des Wochenendes einlesen, Punkte abrechnen, Modell-Update, Dashboard aktualisieren |
| Do 08:00 | Daten sammeln (Quoten, Form, ELO, xG, News), Roh-Dossier bauen |
| Fr 10:00 | Prognose-Lauf: Statistikmodell → Punkteoptimierung → LLM-Analyse → finale Tipps |
| Fr 12:00 | Tipps verschlüsselt committen (Hash öffentlich), Tipps bei Kicktipp eintragen, Site-Rebuild |
| Fr 20:35 / Sa 15:35 / … | Nach jedem Anstoß: betroffene Tipps entsiegeln, Site-Rebuild |
| Bei englischen Wochen / Verlegungen | Spielplan-Check täglich; Jobs orientieren sich an OpenLigaDB-Anstoßzeiten, nicht an festen Wochentagen |

GitHub Actions Free (2.000 Minuten/Monat) reicht dafür um ein Vielfaches – ein kompletter Spieltagslauf braucht wenige Minuten.

---

## 9. Umsetzungs-Roadmap

**Phase 1 – Fundament (1–2 Wochenenden):**
Repo aufsetzen, OpenLigaDB + football-data.org anbinden, Poisson-Modell auf historischen Daten trainieren, Backtesting-Harness bauen, Punkteoptimierung nach eurem Kicktipp-Schema implementieren.

**Phase 2 – Website & Versiegelung:**
Astro-Site mit Spieltagsansicht, Hash-Versiegelung + Entsiegelungs-Job, Deployment auf Pages.

**Phase 3 – LLM-Schicht:**
Gemini/Groq anbinden, Dossier-Prompt bauen, Begründungstexte generieren, LLM-Eingriffe loggen.

**Phase 4 – Kicktipp-Bot:**
Playwright-Abgabe (auf Basis der existierenden Open-Source-Bots), Test-Runde anlegen, Dry-Run-Modus, dann scharf schalten. Testen lässt sich das sofort mit laufenden Wettbewerben (z.B. internationale Ligen, Turniere) – nicht erst zum Bundesliga-Start.

**Phase 5 – Selbstlernen & Feinschliff:**
Schattentipper, Gewichts-Updates, Dashboard, Benachrichtigungen bei Fehlern.

---

## 10. Kosten & Grenzen (ehrlich)

- **Laufende Kosten: 0 €** (GitHub Free + Pages/Cloudflare + Free-Tier-APIs). Einzig ein Premium-LLM (Claude) würde Cents kosten; Gemini/Groq Free reichen.
- **Realistische Erwartung:** Kein Modell der Welt tippt „perfekt". Ein gutes System landet langfristig etwa auf dem Niveau der besten menschlichen Tipper eurer Runde – die Buchmacherquoten sind eine extrem starke Baseline, die es intelligent nutzt. Der Reiz liegt im Vergleich, in der Transparenz und im sichtbaren Lernen, nicht in garantierten Siegen.
- **Fragilster Punkt:** die Kicktipp-Abgabe (Scraping). Deshalb Fallback-Benachrichtigung.
- **Free-Tier-Limits** (v.a. Odds API mit 500 Calls/Monat) erfordern diszipliniertes Caching – im Konzept eingeplant (max. 2 Quoten-Abrufe pro Spieltag).
