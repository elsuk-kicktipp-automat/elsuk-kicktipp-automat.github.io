"""Bonusfragen der Kicktipp-Runde beantworten (Saisonwetten, 4 Punkte je Antwort).

Kicktipp-Runden stellen neben den Spieltipps Saisonfragen, die alle vor dem
ersten Anstoß beantwortet sein müssen und deren Antwort immer ein Verein ist:
Meister, Herbstmeister, Absteiger (Plätze 16-18), Verein des Torschützenkönigs,
Ort des ersten Trainerwechsels.

Zwei Quellen, bewusst getrennt ausgewiesen (Feld `sources`):

- `simulation`: Meister, Herbstmeister und Abstieg fallen aus der
  Monte-Carlo-Saisonsimulation (engine/season.py) - echte Modellaussagen.
- `llm`: Torschützenkönig-Verein und erster Trainerwechsel. Dafür hat das
  Projekt keine Datenquelle - es gibt weder Spieler- noch Trainerdaten. Hier
  entscheidet Groq mit seinem Weltwissen, bekommt die Modellprognose aber als
  Kontext mit. Das ist die einzige Stelle, an der die LLM-Schicht eine echte
  Abgabe bestimmt statt nur im Schatten mitzulaufen (vgl. llm.adjustment) -
  eine bewusste Ausnahme, weil die Alternative "gar nicht antworten" wäre.
  Fällt Groq aus, greift eine dokumentierte Heuristik (`heuristic`).

Fairness wie bei Tipps und Kombi (concept.md §5): Die Antworten werden
versiegelt - öffentlich sind bis zur Frist nur der Hash und die Fragen ohne
Antworten (data/bonus/<id>.json), der Klartext liegt Fernet-verschlüsselt
daneben (<id>.enc). Enthüllt wird nach dem ersten Anstoß der Saison, denn das
ist die Tippfrist der Bonusfragen.
"""

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .config import BONUS_DIR, MAPPINGS_DIR, load_dotenv
from .llm import call_groq
from .seal import _fernet, require_secret
from .teams import normalize

QUESTION_FILE = MAPPINGS_DIR / "bonus_questions.json"

# Fragetyp -> Anzahl der zu wählenden Vereine.
ANSWER_COUNTS = {
    "champion": 1,
    "autumn_champion": 1,
    "relegation": 3,
    "top_scorer_club": 1,
    "first_coach_change": 1,
}

# Diese Felder (plus Salt) gehen in den Hash - dieselbe Rezeptur wie bei
# Tipps und Kombi, nach der Enthüllung nachrechenbar.
HASHED_FIELDS = ("answers", "created_utc")

REVEALED_FIELDS = ("answers", "sources", "begruendung", "forecast", "created_utc")


def load_question_kinds() -> dict[str, str]:
    """Fragetext auf der Kicktipp-Seite -> Fragetyp, Schlüssel normalisiert.

    Die Feldnamen auf der Tippabgabe (fragetippForms[...]) tragen runden-
    spezifische IDs und taugen nicht als Schlüssel; der Fragetext ist das
    einzig Stabile.
    """
    raw = json.loads(QUESTION_FILE.read_text(encoding="utf-8"))
    return {normalize(k): v for k, v in raw.items() if not k.startswith("_")}


def question_kind(text: str) -> str | None:
    """Fragetyp zu einem Fragetext von der Kicktipp-Seite, None wenn unbekannt.

    Enthaltensein statt Gleichheit: Der Text einer Bonuszeile kommt so von der
    Seite, wie er dort steht - mit Datumspräfix davor ("28.08.26 20:30") und
    allen Dropdown-Optionen dahinter ("-- Nicht getippt -- 1. FC Köln ..."). Die
    eigentliche Frage steckt mittendrin. Bei mehreren Treffern gewinnt die
    längste Frage, damit eine kürzere Frage keine längere überstimmt.
    """
    haystack = normalize(text)
    hits = [(q, kind) for q, kind in load_question_kinds().items() if q in haystack]
    if not hits:
        return None
    return max(hits, key=lambda hit: len(hit[0]))[1]


def canonical_payload(bonus: dict, salt: str) -> str:
    core = {field: bonus[field] for field in HASHED_FIELDS}
    core["salt"] = salt
    return json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(bonus: dict, salt: str) -> str:
    return hashlib.sha256(canonical_payload(bonus, salt).encode("utf-8")).hexdigest()


def build_llm_prompt(kind: str, forecast: list[dict], teams: list[str]) -> str:
    """Prompt für die zwei Fragen, die das Modell nicht beantworten kann."""
    frage = {
        "top_scorer_club": (
            "Von welchem Bundesliga-Verein kommt am Saisonende der Torschützenkönig "
            "(der Spieler mit den meisten Toren)?"
        ),
        "first_coach_change": (
            "Bei welchem Bundesliga-Verein gibt es in dieser Saison den ersten "
            "Trainerwechsel?"
        ),
    }[kind]

    tabelle = "\n".join(
        f"  {i:2d}. {row['team']:<28} "
        f"{row['expected_points']:5.1f} Punkte erwartet, "
        f"Meister {row['champion']:.0%}, Abstieg {row['bottom_three']:.0%}"
        for i, row in enumerate(forecast, 1)
    )

    return (
        f"{frage}\n\n"
        "Statistische Saisonprognose eines Dixon-Coles-Modells (nur als Kontext, "
        "sie beantwortet die Frage nicht direkt):\n"
        f"{tabelle}\n\n"
        "Erlaubte Antworten (exakt eine davon, wörtlich):\n"
        + "\n".join(f"  - {t}" for t in teams)
        + "\n\nAntworte NUR mit dem Vereinsnamen, ohne Begründung, ohne Satzzeichen."
    )


def parse_team(text: str | None, teams: list[str]) -> str | None:
    """Vereinsnamen aus einer LLM-Antwort herauslesen; None wenn nicht eindeutig.

    Der längste passende Name gewinnt, damit "Bayer 04 Leverkusen" nicht an
    "Bayern München" verloren geht.
    """
    if not text:
        return None
    haystack = normalize(text)
    hits = [t for t in teams if normalize(t) in haystack]
    if not hits:
        return None
    return max(hits, key=lambda t: len(normalize(t)))


def build_forecast(probabilities) -> list[dict]:
    """Modellprognose als Tabelle, bester Verein zuerst - Kontext fürs LLM und
    Begründungsmaterial in der enthüllten Datei."""
    return [
        {
            "team": team,
            "expected_points": round(probabilities.expected_points[team], 1),
            "expected_goals_for": round(probabilities.expected_goals_for[team], 1),
            "champion": round(probabilities.champion[team], 4),
            "autumn_champion": round(probabilities.autumn_champion[team], 4),
            "bottom_three": round(probabilities.bottom_three[team], 4),
        }
        for team in sorted(
            probabilities.teams,
            key=lambda t: -probabilities.expected_points[t],
        )
    ]


def _heuristic_answer(kind: str, probabilities, promoted: set[str]) -> str:
    """Fallback, wenn Groq nicht erreichbar ist - bewusst simpel und begründbar."""
    if kind == "top_scorer_club":
        # Der Torschützenkönig kommt fast immer aus einer Top-Offensive.
        return max(probabilities.expected_goals_for, key=probabilities.expected_goals_for.get)
    # Erster Trainerwechsel: etablierte Vereine im Abstiegskampf stehen am
    # schnellsten unter Druck - Aufsteiger bekommen erfahrungsgemäß Geduld.
    etabliert = {t: p for t, p in probabilities.bottom_three.items() if t not in promoted}
    pool = etabliert or probabilities.bottom_three
    return max(pool, key=pool.get)


def answer_questions(
    probabilities,
    promoted: set[str],
    api_key: str | None,
    llm_model: str,
    kinds=tuple(ANSWER_COUNTS),
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """(Antworten je Fragetyp, Quelle je Fragetyp)."""
    forecast = build_forecast(probabilities)
    answers: dict[str, list[str]] = {}
    sources: dict[str, str] = {}

    for kind in kinds:
        if kind == "champion":
            answers[kind] = probabilities.most_likely(probabilities.champion, 1)
            sources[kind] = "simulation"
        elif kind == "autumn_champion":
            answers[kind] = probabilities.most_likely(probabilities.autumn_champion, 1)
            sources[kind] = "simulation"
        elif kind == "relegation":
            answers[kind] = probabilities.most_likely(probabilities.bottom_three, 3)
            sources[kind] = "simulation"
        else:
            team = None
            if api_key:
                text = call_groq(
                    build_llm_prompt(kind, forecast, probabilities.teams),
                    api_key,
                    llm_model,
                    temperature=0.3,
                    max_tokens=150,
                )
                team = parse_team(text, probabilities.teams)
            if team is None:
                team = _heuristic_answer(kind, probabilities, promoted)
                sources[kind] = "heuristic"
            else:
                sources[kind] = "llm"
            answers[kind] = [team]

    return answers, sources


def answer_window_open(cfg: dict, now: datetime) -> bool:
    """Darf jetzt schon geantwortet werden?

    `answer_after` verschiebt die Beantwortung nach hinten. Grund: Der
    Spielleiter kann die Bonusfragen bis kurz vor Saisonstart noch ändern, und
    einmal abgegebene Antworten überschreibt der Bot nicht - sie müssten in
    Kicktipp von Hand zurückgesetzt werden. Ohne den Wert wird beim nächsten
    Lauf geantwortet.
    """
    answer_after = cfg.get("answer_after")
    if not answer_after:
        return True
    not_before = datetime.strptime(str(answer_after), "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    return now >= not_before


def bonus_id(competition: str, season: int) -> str:
    return f"{competition}_{season}_bonus"


def exists(competition: str, season: int, bonus_dir: Path = BONUS_DIR) -> bool:
    """Wurde für diese Saison schon versiegelt? Bonusfragen gibt es nur einmal."""
    return (bonus_dir / f"{bonus_id(competition, season)}.json").exists()


def seal_bonus(bonus: dict, secret: str, bonus_dir: Path = BONUS_DIR) -> Path:
    """Öffentlich nur Hash, Fragen und Frist; Antworten verschlüsselt daneben."""
    salt = secrets.token_hex(16)
    h = payload_hash(bonus, salt)
    public = {
        "id": bonus["id"],
        "competition": bonus["competition"],
        "season": bonus["season"],
        "type": "bonus",
        "status": "sealed",
        "hash": h,
        "sealed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reveal_after_utc": bonus["reveal_after_utc"],
        # Fragen ja, Antworten nein: zeigt nachprüfbar, worauf sich der Hash bezieht
        "questions": [
            {"kind": kind, "answer_count": len(teams)}
            for kind, teams in sorted(bonus["answers"].items())
        ],
    }
    bonus_dir.mkdir(parents=True, exist_ok=True)
    public_path = bonus_dir / f"{bonus['id']}.json"
    public_path.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")

    private = {**bonus, "salt": salt, "hash": h}
    encrypted = _fernet(secret).encrypt(json.dumps(private, ensure_ascii=False).encode("utf-8"))
    (bonus_dir / f"{bonus['id']}.enc").write_bytes(encrypted)
    return public_path


def unseal_due(secret: str, bonus_dir: Path = BONUS_DIR, now: datetime | None = None) -> list[Path]:
    """Enthüllt Bonusantworten, deren Tippfrist abgelaufen ist."""
    now = now or datetime.now(timezone.utc)
    fernet = _fernet(secret)
    changed = []
    for enc_path in sorted(bonus_dir.glob("*.enc")):
        public_path = enc_path.with_suffix(".json")
        public = json.loads(public_path.read_text(encoding="utf-8"))
        reveal_after = datetime.strptime(
            public["reveal_after_utc"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        if public["status"] != "sealed" or now < reveal_after:
            continue
        private = json.loads(fernet.decrypt(enc_path.read_bytes()).decode("utf-8"))
        public.update({field: private[field] for field in REVEALED_FIELDS if field in private})
        public["salt"] = private["salt"]
        public["status"] = "revealed"
        public_path.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
        enc_path.unlink()
        changed.append(public_path)
    return changed


def load_pending_answers(secret: str, bonus_dir: Path = BONUS_DIR) -> dict[str, list[str]]:
    """Antworten aus der versiegelten Datei - Quelle für die Kicktipp-Abgabe.

    Bewusst aus der .enc-Datei (wie die Spieltipps): eingetragen wird nur, was
    schon öffentlich per Hash beweisbar ist.
    """
    for enc_path in sorted(bonus_dir.glob("*.enc")):
        private = json.loads(_fernet(secret).decrypt(enc_path.read_bytes()).decode("utf-8"))
        return private["answers"]
    return {}


def answer_probabilities(
    probabilities, answers: dict[str, list[str]], top_scorer: dict[str, float] | None = None
) -> dict[str, dict[str, float]]:
    """Aktuelle Trefferwahrscheinlichkeit je gegebener Antwort.

    Für den ersten Trainerwechsel gibt es weiterhin keine - dafür existiert
    keine Datenquelle. Der Torschützenkönig-Verein bekommt eine, sobald in der
    laufenden Saison Tore gefallen sind (engine/season.simulate_top_scorer_club).
    """
    quellen = {
        "champion": probabilities.champion,
        "autumn_champion": probabilities.autumn_champion,
        "relegation": probabilities.bottom_three,
    }
    if top_scorer:
        quellen["top_scorer_club"] = top_scorer
    return {
        kind: {team: round(quellen[kind].get(team, 0.0), 4) for team in teams}
        for kind, teams in answers.items()
        if kind in quellen
    }


def _top_scorer_standing(goals_by_scorer: dict[tuple[str, str], int], top: int = 3) -> list[dict]:
    """Aktuelle Torjägerliste - Kontext zur Wahrscheinlichkeit auf der Website."""
    beste = sorted(goals_by_scorer.items(), key=lambda kv: (-kv[1], kv[0][0]))[:top]
    return [{"player": name, "club": club, "goals": tore} for (name, club), tore in beste]


def update_live_probabilities(
    config: dict, all_matches: list, bonus_dir: Path = BONUS_DIR, now: datetime | None = None
) -> list[Path]:
    """Rechnet die Trefferwahrscheinlichkeiten enthüllter Bonusantworten neu.

    Nur nach einem neuen Ergebnis: Die Simulation selbst ist deterministisch
    (fester Seed), der ELO-Stand ändert sich aber täglich. Ohne diese Bremse
    würde die Datei auch an spielfreien Tagen neu geschrieben - und der
    Spieltags-Workflow committet jede Änderung.

    Läuft nur auf enthüllten Antworten. Vorher wäre eine Wahrscheinlichkeit je
    Antwort ein Leck: Sie würde verraten, worauf der Automat getippt hat.
    """
    from .predict import build_model, load_elo
    from .season import simulate, simulate_top_scorer_club
    from .sources.openligadb import fetch_competition, fetch_goalscorers

    cfg = config.get("bonus") or {}
    if not cfg.get("enabled"):
        return []

    playable = [m for m in all_matches if not m.has_placeholder]
    gespielt = sum(1 for m in playable if m.has_result)
    faellig = []
    for path in sorted(bonus_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") != "revealed" or data.get("competition") != config["competition"]:
            continue
        if (data.get("live") or {}).get("after_matches") == gespielt:
            continue  # kein neues Ergebnis seit der letzten Berechnung
        faellig.append((path, data))
    if not faellig:
        return []

    season = config["season"]
    train = [m for m in playable if m.has_result]
    if config["team_type"] == "club":
        lookback = config.get("backtest", {}).get("club", {}).get("lookback_seasons", 2)
        for s in range(season - lookback, season):
            train += [m for m in fetch_competition(config["leagues"], s) if m.has_result]

    ref = min(m.kickoff_utc for m in playable)
    model = build_model(config, config["neutral_venue"], config["team_type"])
    model.fit(train, ref, elo=load_elo(config, config["team_type"], ref.date()))
    probabilities = simulate(
        model,
        playable,
        simulations=int(cfg.get("simulations", 10000)),
        autumn_matchday=int(cfg.get("autumn_matchday", 17)),
    )

    # Torschützenkönig: Verein je Schütze aus den Spieldaten ableiten und die
    # Restsaison je Spieler fortschreiben. Ohne gefallene Tore gibt es nichts
    # zu rechnen - dann bleibt die Frage vorerst ohne Zahl.
    tore = fetch_goalscorers(config["leagues"][0], season, force_refresh=True)
    einsaetze: dict[str, int] = {}
    offen: dict[str, int] = {}
    for m in playable:
        for team in (m.home_name, m.away_name):
            if m.has_result:
                einsaetze[team] = einsaetze.get(team, 0) + 1
            else:
                offen[team] = offen.get(team, 0) + 1
    top_scorer = simulate_top_scorer_club(
        tore, einsaetze, offen, simulations=int(cfg.get("simulations", 10000))
    )

    stempel = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    changed = []
    for path, data in faellig:
        data["live"] = {
            "after_matches": gespielt,
            "updated_utc": stempel,
            "simulations": probabilities.simulations,
            "probabilities": answer_probabilities(probabilities, data["answers"], top_scorer),
            "top_scorer": _top_scorer_standing(tore),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        changed.append(path)
    return changed


def score_answers(answers: dict[str, list[str]], table: list[str] | None,
                  autumn_table: list[str] | None, points_per_answer: int = 4) -> dict:
    """Rechnet die abrechenbaren Bonusfragen ab.

    Torschützenkönig und Trainerwechsel bleiben `null`: OpenLigaDB liefert
    weder Spieler- noch Trainerdaten, das muss ein Mensch nachtragen.
    """
    scored: dict[str, dict] = {}
    for kind, tipped in sorted(answers.items()):
        actual = None
        if kind == "champion" and table:
            actual = table[:1]
        elif kind == "autumn_champion" and autumn_table:
            actual = autumn_table[:1]
        elif kind == "relegation" and table:
            actual = table[-3:]

        if actual is None:
            scored[kind] = {"tip": tipped, "actual": None, "correct": None, "points": None}
            continue

        # Reihenfolge zählt nicht (Kicktipp: "Punkte pro richtiger Antwort")
        hits = len({normalize(t) for t in tipped} & {normalize(a) for a in actual})
        scored[kind] = {
            "tip": tipped,
            "actual": actual,
            "correct": hits,
            "points": hits * points_per_answer,
        }

    settled = [q for q in scored.values() if q["points"] is not None]
    return {
        "questions": scored,
        "points_total": sum(q["points"] for q in settled),
        "answers_scored": sum(len(q["tip"]) for q in settled),
        "answers_open": sum(len(q["tip"]) for q in scored.values() if q["points"] is None),
    }


def main(config: dict) -> None:
    """Berechnet die Bonusantworten einmal pro Saison und versiegelt sie."""
    from .predict import build_model, load_elo
    from .season import simulate
    from .sources.openligadb import fetch_competition

    cfg = config.get("bonus") or {}
    if not cfg.get("enabled"):
        print("Bonusfragen sind deaktiviert (config.yaml: bonus.enabled).")
        return

    competition, season = config["competition"], config["season"]
    if exists(competition, season):
        print("Bonusantworten für diese Saison stehen bereits, nichts zu tun.")
        return

    now = datetime.now(timezone.utc)
    if not answer_window_open(cfg, now):
        # Vor dem Spielplan-Abruf prüfen: spart den API-Aufruf im Leerlauf.
        print(f"Bonusfragen werden erst ab {cfg['answer_after']} beantwortet.")
        return

    matches = fetch_competition(config["leagues"], season, force_refresh=True)
    playable = [m for m in matches if not m.has_placeholder]
    if not playable:
        print("Kein Spielplan verfügbar, Bonusfragen können nicht beantwortet werden.")
        return

    first_kickoff = min(m.kickoff_utc for m in playable)
    if now >= first_kickoff:
        # Fairness-Guard wie beim Versiegeln der Tipps: nach der Frist wäre die
        # Antwort wertlos - und der Beweis "stand vorher fest" gelogen.
        print("Tippfrist der Bonusfragen ist abgelaufen, es wird nicht mehr versiegelt.")
        return

    train = [m for m in matches if m.has_result]
    lookback = config.get("backtest", {}).get("club", {}).get("lookback_seasons", 2)
    previous: set[str] = set()
    if config["team_type"] == "club":
        for s in range(season - lookback, season):
            past = fetch_competition(config["leagues"], s)
            train += [m for m in past if m.has_result]
            if s == season - 1:
                previous = {m.home_name for m in past} | {m.away_name for m in past}

    load_dotenv()
    elo = load_elo(config, config["team_type"], first_kickoff.date())
    model = build_model(config, config["neutral_venue"], config["team_type"])
    model.fit(train, first_kickoff, elo=elo)

    probabilities = simulate(
        model,
        playable,
        simulations=int(cfg.get("simulations", 10000)),
        autumn_matchday=int(cfg.get("autumn_matchday", 17)),
    )
    promoted = {t for t in probabilities.teams if t not in previous} if previous else set()

    import os

    llm_cfg = config.get("llm", {})
    answers, sources = answer_questions(
        probabilities,
        promoted,
        os.environ.get("GROQ_API_KEY") if llm_cfg.get("enabled") else None,
        llm_cfg.get("model", "openai/gpt-oss-120b"),
    )

    bonus = {
        "id": bonus_id(competition, season),
        "competition": competition,
        "season": season,
        "type": "bonus",
        "created_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reveal_after_utc": first_kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "answers": answers,
        "sources": sources,
        "forecast": build_forecast(probabilities),
        "simulations": probabilities.simulations,
    }
    path = seal_bonus(bonus, require_secret())
    # Keine Antworten ins Log: GitHub-Actions-Logs sind bei einem öffentlichen
    # Repo für jeden einsehbar, die Antworten sind bis zur Frist versiegelt.
    quellen = ", ".join(f"{k}: {v}" for k, v in sorted(sources.items()))
    print(
        f"{sum(len(v) for v in answers.values())} Bonusantworten versiegelt: {path.name} "
        f"(Enthüllung nach dem ersten Anstoß). Quellen - {quellen}"
    )


def main_unseal() -> None:
    if not list(BONUS_DIR.glob("*.enc")):
        return
    for path in unseal_due(require_secret()):
        print(f"Bonusantworten enthüllt: {path.name}")
