"""LLM-Schicht: Begründungstexte + Anpassungsvorschlag (concept.md Schicht 3).

Alle LLM-Aufrufe gehen zuerst an Claude Opus 5.5 über die Claude-Code-
Kommandozeile (`claude -p`) mit dem Claude-Abo - kein API-Schlüssel, keine
Zusatzkosten. Groq (Free Tier) springt nur ein, wenn Claude nicht erreichbar
ist (siehe ask). Jeder gelungene Aufruf wird mit Zweck, Modell und Tokens
mitgeschrieben und pro Spiel veröffentlicht (factors.llm_usage).

Begründung: ersetzt die Template-Begründung durch einen vom LLM formulierten
Analysetext, der dieselben Modellzahlen in flüssigerer Sprache einordnet.
Hier gibt es keinen Groq-Ersatz: fällt Claude aus, bleibt die Template-
Begründung. Der Text ist Teil des Hashes und nach dem Versiegeln nicht mehr
korrigierbar, deshalb wird er vorher geprüft (check_begruendung).

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
import os
import re
import shutil
import subprocess
import tempfile

import requests

GROQ_API_BASE = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"
# Claude läuft über das Abo. Lokal reicht die angemeldete `claude`-CLI, in
# GitHub Actions das Token aus `claude setup-token` (Secret
# CLAUDE_CODE_OAUTH_TOKEN, ein Jahr gültig).
CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_SYSTEM = (
    "Du schreibst kurze Texte genau nach Vorgabe. Antworte nur mit dem verlangten "
    "Text, ohne Vor- oder Nachbemerkung."
)
# Endungen, mit denen ein Mannschaftsname im Fließtext auftaucht ("Kölner")
NAME_SUFFIX = r"(?:er|ern|ers|en|es|e|s)?"


def build_prompt(match_context: dict) -> str:
    """Ausführliches Dossier für die LLM-Analyse: alle Quellen, die in die
    Entscheidung eingeflossen sind (Modell, ELO, Quoten, News-Check) - das
    LLM soll sie im Begründungstext explizit benennen, nicht nur die Zahlen
    umformulieren."""
    home, away = match_context["home"], match_context["away"]
    probs = match_context["probabilities"]
    lam, mu = match_context["expected_goals"]
    tip = match_context["tip"]
    # Bei Turnieren auf neutralem Platz (WM) keine Heim/Auswärts-Rollen nennen,
    # sonst fabuliert das LLM einen Heimvorteil herbei
    pairing = (
        f"{home} gegen {away} (neutraler Platz, kein Heimvorteil)"
        if match_context.get("neutral_venue")
        else f"{home} (Heim) gegen {away} (Auswärts)"
    )
    lines = [
        f"Fußballspiel: {pairing}, {match_context['stage']}.",
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
        "Schreibe 3-4 Sätze auf Deutsch im Ton eines pointierten Fußball-Kommentators "
        "am Stammtisch: meinungsstark, anschaulich, gern mit einem Augenzwinkern - aber "
        "ohne Floskeln wie 'Es bleibt spannend' oder 'Fußball ist unberechenbar'. "
        "Nenne den Tipp früh. Zähle NICHT alle Zahlen von oben auf: Zitiere höchstens "
        "ein, zwei der aussagekräftigsten und übersetze den Rest in Fußballsprache "
        "(klarer Favorit, enge Kiste, Duell auf Augenhöhe, Torfestival, Abnutzungskampf). "
        "Sprich über die Teams und das Spiel, nicht über 'das Modell', 'die Statistik' "
        "oder 'die Berechnung'. Dein Wissen über die beiden Mannschaften darfst du "
        "einbringen, soweit es zeitlos ist: Spitznamen, Stadion, Stadt und Region, "
        "Farben, Tradition. Erfinde nichts zur aktuellen Lage dazu: keine Spielernamen, "
        "Trainer, Verletzungen, Ergebnisse, Bilanzen, Tabellenstände oder Formkurven, "
        "die oben nicht stehen. Nenne außer den beiden Mannschaften keine andere. "
        "Ordne das Kräfteverhältnis ehrlich anhand der Zahlen ein und bleib dabei "
        "in einer Linie: einen klaren Favoriten nicht kleinreden, ein enges Duell "
        "nicht zum Selbstläufer erklären. Vermeide Fachwörter wie Erwartungswert, "
        "Matrix, Prior oder Dixon-Coles. Keine Anrede, keine Überschrift, nur Fließtext."
    )
    return "\n".join(lines)


def call_groq(
    prompt: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.4,
    max_tokens: int = 300,
    reasoning_effort: str = "low",
    usage: list | None = None,
) -> str | None:
    """Best-effort Chat-Completion; None bei jedem Fehler (Fallback greift dann).

    reasoning_effort steuert, wie lange das Modell vor der Antwort nachdenkt.
    Das Denken zählt gegen max_tokens - ist der Deckel zu niedrig, kommt eine
    leere Antwort zurück. Für JSON/Einwort-Antworten reicht "low"."""
    try:
        resp = requests.post(
            f"{GROQ_API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "reasoning_effort": reasoning_effort,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        tokens = data.get("usage") or {}
        _record(usage, model, tokens.get("prompt_tokens"), tokens.get("completion_tokens"))
        return text or None
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        print(f"Groq-LLM nicht verfügbar: {exc}")
        return None


def _record(usage: list | None, model: str, input_tokens, output_tokens) -> None:
    if usage is not None:
        usage.append(
            {"model": model, "input_tokens": int(input_tokens or 0), "output_tokens": int(output_tokens or 0)}
        )


def call_claude(prompt: str, model: str = CLAUDE_MODEL, usage: list | None = None) -> str | None:
    """Best-effort-Aufruf über `claude -p`; None bei jedem Fehler (Fallback greift dann).

    Läuft bewusst über das Abo statt über die API: ANTHROPIC_API_KEY und
    ANTHROPIC_AUTH_TOKEN hätten Vorrang vor dem Abo-Login und würden berechnet,
    deshalb bekommt der Aufruf sie nicht mit. Ohne Werkzeuge, ohne Einstellungen
    und MCP-Server des Rechners und aus einem leeren Verzeichnis, damit nur der
    Prompt zählt. Die Antwort wird nie geloggt - sie enthält den Tipp."""
    if not shutil.which("claude"):
        print("Claude-CLI nicht installiert")
        return None
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    try:
        proc = subprocess.run(
            [
                "claude", "-p", "--model", model, "--system-prompt", CLAUDE_SYSTEM,
                "--tools", "", "--strict-mcp-config", "--setting-sources", "",
                "--no-session-persistence", "--output-format", "json",
            ],
            input=prompt, capture_output=True, text=True, timeout=180,
            env=env, cwd=tempfile.gettempdir(),
        )
        data = json.loads(proc.stdout)
        # Bei Fehlern (Limit erreicht, CLI zu alt, nicht angemeldet) steht die
        # Fehlermeldung im result-Feld - die darf nie als Begründung durchgehen
        if proc.returncode != 0 or data.get("is_error"):
            raise ValueError(str(data.get("result"))[:200])
        text = data["result"].strip()
        tokens = data.get("usage") or {}
        # Eingabe = frisch gelesene plus (neu angelegte oder wiederverwendete)
        # zwischengespeicherte Tokens; die CLI weist sie getrennt aus
        _record(
            usage, model,
            sum(tokens.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
            tokens.get("output_tokens"),
        )
        return text or None
    except (OSError, subprocess.SubprocessError, KeyError, AttributeError, ValueError) as exc:
        print(f"Claude nicht verfügbar: {exc}")
        return None


def ask(
    prompt: str,
    zweck: str,
    usage: list | None = None,
    groq_api_key: str | None = None,
    groq_model: str = DEFAULT_MODEL,
    **groq_kwargs,
) -> str | None:
    """Claude zuerst, Groq nur als Ersatz (und nur mit Schlüssel).

    Jeder gelungene Aufruf landet mit Zweck, Modell und Tokens in `usage` -
    auch ein Text, der danach durch die Prüfung fällt, hat Tokens gekostet."""
    calls: list = []
    text = call_claude(prompt, usage=calls)
    if not text and groq_api_key:
        text = call_groq(prompt, groq_api_key, groq_model, usage=calls, **groq_kwargs)
    if usage is not None:
        usage.extend({"zweck": zweck, **call} for call in calls)
    return text


def summarize_usage(calls: list) -> dict | None:
    """Verbrauch eines Spiels für die Veröffentlichung; None ohne gelungenen Aufruf."""
    if not calls:
        return None
    return {
        "input_tokens": sum(c["input_tokens"] for c in calls),
        "output_tokens": sum(c["output_tokens"] for c in calls),
        "calls": calls,
    }


def _name_tokens(name: str) -> set[str]:
    """Wörter, an denen eine Mannschaft im Fließtext erkennbar ist."""
    return {word for word in re.findall(r"[^\W\d_]+", name) if len(word) >= 4}


def check_begruendung(text: str, match_context: dict, other_teams=()) -> str | None:
    """Prüft den LLM-Text vor dem Versiegeln; liefert den Ablehnungsgrund oder None.

    Geprüft wird nur, was sich aus den Daten sicher entscheiden lässt: Der Text
    nennt den Tipp, und er nennt keine dritte Mannschaft (so stand am 5. Spieltag
    2026 "Die Bayern" im Text zu Dortmund - Bremen). Was das Modell aus eigenem
    Wissen über die beiden Mannschaften schreibt, bleibt ungeprüft."""
    tip = match_context["tip"]
    if not re.search(rf"(?<!\d){tip[0]}\s*:\s*{tip[1]}(?!\d)", text):
        return "Tipp fehlt im Text"
    own = _name_tokens(match_context["home"]) | _name_tokens(match_context["away"])
    for team in other_teams:
        for token in _name_tokens(team) - own:
            if re.search(rf"\b{re.escape(token)}{NAME_SUFFIX}\b", text):
                return f"nennt dritte Mannschaft ({team})"
    return None


def generate_begruendung(
    match_context: dict, other_teams=(), usage: list | None = None
) -> tuple[str | None, str]:
    """(text, quelle) – quelle ist "llm" oder "template". text ist None, wenn der
    Aufrufer auf die Template-Begründung zurückfallen soll: Claude nicht
    erreichbar oder zwei Texte in Folge durch die Prüfung gefallen."""
    prompt = build_prompt(match_context)
    for _ in range(2):
        text = ask(prompt, "begruendung", usage)
        if not text:
            break
        problem = check_begruendung(text, match_context, other_teams)
        if problem is None:
            return text, "llm"
        # Nur der Grund ins Log, nie der Text: er enthält den Tipp vor Anstoß
        print(f"LLM-Begründung verworfen: {problem}")
    return None, "template"


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
    match_context: dict,
    news: list[dict],
    api_key: str | None,
    model: str = DEFAULT_MODEL,
    usage: list | None = None,
) -> dict | None:
    """Schattentipp-Vorschlag (siehe Modul-Docstring) oder None, wenn keine
    News vorliegen, das LLM ausfällt oder kein harter Grund gefunden wurde.
    api_key/model gelten für den Groq-Ersatz."""
    if not news:
        return None
    text = ask(
        build_adjustment_prompt(match_context, news), "news", usage,
        groq_api_key=api_key, groq_model=model, temperature=0.2, max_tokens=400,
    )
    if not text:
        return None
    return parse_adjustment_response(text)
