"""LLM-Schicht: Begründungstexte + Anpassungsvorschlag (concept.md Schicht 3,
Groq Free Tier).

Begründung: ersetzt die Template-Begründung durch einen vom LLM formulierten
Analysetext, der dieselben Modellzahlen in flüssigerer Sprache einordnet.

Anpassungsvorschlag: Mit News-Schnipseln (engine/sources/news.py) darf das LLM
einen Tipp innerhalb von ±1 Tor vorschlagen – aber nur mit konkretem Grund
(Verletzung, Sperre, Rotation), nicht auf Basis von nichts. Läuft aktuell im
Schatten-Modus: der Vorschlag wird nur geloggt und als eigener Schattentipper
bewertet (siehe evaluate.py), er ändert NICHT den echten/versiegelten Tipp.
Erst wenn Phase 5 belegt, dass er über mehrere Spieltage Punkte bringt, wird
er scharf geschaltet (LLM-Vertrauensregler, engine/learn.py).

Fällt das LLM aus (kein Key, Netzwerkfehler, Rate-Limit) oder gibt es keine
News-Schnipsel, bleibt die Template-Begründung bzw. bleibt die Anpassung aus –
das System bleibt immer funktionsfähig.
"""

import json
import re

import requests

GROQ_API_BASE = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "llama-3.3-70b-versatile"


def build_prompt(match_context: dict) -> str:
    """Ausführliches Dossier für die LLM-Analyse: alle Quellen, die in die
    Entscheidung eingeflossen sind (Modell, ELO, Quoten, News-Check) - das
    LLM soll sie im Begründungstext explizit benennen, nicht nur die Zahlen
    umformulieren."""
    home, away = match_context["home"], match_context["away"]
    probs = match_context["probabilities"]
    lam, mu = match_context["expected_goals"]
    tip = match_context["tip"]
    lines = [
        f"Fußballspiel: {home} (Heim) gegen {away} (Auswärts), {match_context['stage']}.",
        "Statistisches Modell (Dixon-Coles-Poisson, trainiert auf "
        f"{match_context.get('trained_on_matches', '?')} Spielen):",
        f"- Heimsieg {probs['home']:.0%}, Remis {probs['draw']:.0%}, Auswärtssieg {probs['away']:.0%}",
        f"- Erwartete Tore: {home} {lam:.2f} : {mu:.2f} {away}",
    ]
    elo = match_context.get("elo") or {}
    if elo.get("home") is not None and elo.get("away") is not None:
        lines.append(f"- ELO-Bewertung als Prior: {home} {elo['home']:.0f}, {away} {elo['away']:.0f}")
    if match_context.get("market_probabilities"):
        m = match_context["market_probabilities"]
        weight = match_context.get("market_weight", 0)
        lines.append(
            f"- Buchmacherquoten (entvigt, zu {weight:.0%} eingerechnet): Heimsieg {m['home']:.0%}, "
            f"Remis {m['draw']:.0%}, Auswärtssieg {m['away']:.0%}"
        )
    llm_adjustment = match_context.get("llm_adjustment")
    news_checked = match_context.get("news_checked")
    news_sources = match_context.get("news_sources") or {}
    news_source_labels = [
        s["label"] for s in news_sources.get("sources", []) if s.get("checked")
    ]
    news_source_text = f" aus {', '.join(news_source_labels)}" if news_source_labels else ""
    if llm_adjustment:
        lines.append(
            f"- News-Check: eine der {llm_adjustment['news_count']} geprüften Schlagzeilen lieferte "
            f"einen möglichen Grund ({llm_adjustment['grund']}) für eine Anpassung auf "
            f"{llm_adjustment['tip'][0]}:{llm_adjustment['tip'][1]} - läuft nur als Schattentipp mit, "
            "ändert nicht den unten genannten offiziellen Tipp"
        )
    elif news_checked is not None:
        lines.append(
            f"- News-Check: {news_checked} aktuelle Schlagzeile(n){news_source_text} geprüft, "
            "kein harter Grund für eine Anpassung gefunden"
            if news_checked > 0
            else f"- News-Check: keine einschlägigen aktuellen Schlagzeilen{news_source_text} gefunden"
        )
    lines.append(f"- Für Kicktipp ausgewählter Tipp: {tip[0]}:{tip[1]}")
    lines.append(
        "Schreibe 3-4 kurze Sätze auf Deutsch, so dass ein normaler Kicktipp-Mitspieler "
        "es sofort versteht. Vermeide Fachwörter wie Erwartungswert, Matrix, Prior oder "
        "Dixon-Coles. Beginne mit dem Tipp und erkläre dann einfach: Wer wirkt stärker, "
        "was sagen Quoten/ELO/News grob, und warum dieser Tipp für Kicktipp sinnvoll ist. "
        "Keine Anrede, keine Überschrift, nur Fließtext."
    )
    return "\n".join(lines)


def call_groq(
    prompt: str, api_key: str, model: str = DEFAULT_MODEL, temperature: float = 0.4, max_tokens: int = 300
) -> str | None:
    """Best-effort Chat-Completion; None bei jedem Fehler (Fallback greift dann)."""
    try:
        resp = requests.post(
            f"{GROQ_API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=20,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        return text or None
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        print(f"Groq-LLM nicht verfügbar: {exc}")
        return None


def generate_begruendung(
    match_context: dict, api_key: str | None, model: str = DEFAULT_MODEL
) -> tuple[str | None, str]:
    """(text, quelle) – quelle ist "llm" oder "template". text ist None, wenn der
    Aufrufer auf die Template-Begründung zurückfallen soll."""
    if not api_key:
        return None, "template"
    text = call_groq(build_prompt(match_context), api_key, model, max_tokens=450)
    return (text, "llm") if text else (None, "template")


def build_adjustment_prompt(match_context: dict, news: list[dict]) -> str:
    home, away = match_context["home"], match_context["away"]
    tip = match_context["tip"]
    lines = [
        f"Fußballspiel: {home} (Heim) gegen {away} (Auswärts).",
        f"Statistischer Tipp: {tip[0]}:{tip[1]}.",
        "Aktuelle Schlagzeilen (unsortiert, nicht alle relevant):",
    ]
    for item in news:
        lines.append(f"- [{item['source']}] {item['title']}: {item['description']}")
    lines.append(
        "Gibt es unter diesen Schlagzeilen einen KONKRETEN harten Grund (Verletzung/Sperre "
        "eines Schlüsselspielers, Trainerwechsel kurz vor dem Spiel, angekündigte Schonung "
        "vor einem wichtigeren Spiel), der im statistischen Modell nicht steckt und eine "
        "Anpassung um höchstens 1 Tor pro Team rechtfertigt? Wenn nein, oder wenn die "
        "Schlagzeilen nur allgemeine Spielberichte/Analysen ohne harten Fakt sind, antworte "
        "mit adjust=false. Antworte NUR mit einem einzeiligen JSON-Objekt, keine Erklärung "
        "davor oder danach, exakt in diesem Format: "
        '{"adjust": true oder false, "home_delta": -1/0/1, "away_delta": -1/0/1, "grund": "kurzer Satz"}'
    )
    return "\n".join(lines)


def parse_adjustment_response(text: str) -> dict | None:
    """Extrahiert und validiert das JSON-Objekt; None bei jedem Parse-/Schema-
    fehler oder wenn adjust=false (dann gibt es nichts anzuwenden)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict) or not data.get("adjust"):
        return None

    def clamp(value) -> int | None:
        try:
            return max(-1, min(1, int(value)))
        except (TypeError, ValueError):
            return None

    home_delta, away_delta = clamp(data.get("home_delta")), clamp(data.get("away_delta"))
    if home_delta is None or away_delta is None or (home_delta == 0 and away_delta == 0):
        return None

    return {
        "home_delta": home_delta,
        "away_delta": away_delta,
        "grund": str(data.get("grund", ""))[:300],
    }


def propose_adjustment(
    match_context: dict, news: list[dict], api_key: str | None, model: str = DEFAULT_MODEL
) -> dict | None:
    """Schattentipp-Vorschlag (siehe Modul-Docstring) oder None, wenn keine
    News vorliegen, das LLM ausfällt oder kein harter Grund gefunden wurde."""
    if not api_key or not news:
        return None
    text = call_groq(build_adjustment_prompt(match_context, news), api_key, model, temperature=0.2, max_tokens=200)
    if not text:
        return None
    return parse_adjustment_response(text)
