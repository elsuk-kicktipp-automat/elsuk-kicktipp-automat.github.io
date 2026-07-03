"""Fairness-Mechanismus: Tipps versiegeln und nach Anstoß entsiegeln (concept.md §5).

Versiegeln: Pro Spiel wird ein zufälliger Salt erzeugt und nur der SHA-256-Hash
der kanonischen Payload (Teams, Anstoß, Tipp, Begründung, Salt) veröffentlicht
(data/matchdays/). Der Klartext liegt bis zum Anstoß ausschließlich
Fernet-verschlüsselt im Repo (data/sealed/*.enc); der Schlüssel SEAL_SECRET
existiert nur als GitHub Actions Secret bzw. in der lokalen .env.

Entsiegeln: Ab 5 Minuten nach Anstoß wird der Klartext inkl. Salt in die
öffentliche Spieltags-Datei geschrieben. Jeder kann den Hash nachrechnen:

    sha256(json.dumps({felder..., "salt": salt}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":")))

Der Commit-Zeitstempel der Versiegelung beweist zusätzlich, dass der Tipp vor
Anstoß feststand.
"""

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from .config import MATCHDAYS_DIR, PREDICTIONS_DIR, PROJECT_ROOT, SEALED_DIR, load_dotenv

REVEAL_DELAY = timedelta(minutes=5)

# Diese Felder (plus Salt) gehen in den Hash. advance_tip (Elfmeterschießen-
# Zusatzfrage bei K.o.-Remis-Tipps) muss wie der Tipp selbst vor Anstoß feststehen.
HASHED_FIELDS = ("home", "away", "kickoff_utc", "tip", "advance_tip", "begruendung")

# Beim Entsiegeln werden zusätzlich diese Felder veröffentlicht.
REVEALED_FIELDS = ("tip", "advance_tip", "expected_points", "factors", "begruendung", "shadow_tips")


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def canonical_payload(match: dict, salt: str) -> str:
    """Kanonische JSON-Payload, deren SHA-256 veröffentlicht wird."""
    core = {field: match[field] for field in HASHED_FIELDS}
    core["salt"] = salt
    return json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(match: dict, salt: str) -> str:
    return hashlib.sha256(canonical_payload(match, salt).encode("utf-8")).hexdigest()


def public_file_name(data: dict) -> str:
    return f"{data['competition']}_{data['season']}_md{data['matchday']:02d}.json"


def seal_file(
    prediction_path: Path,
    secret: str,
    matchdays_dir: Path = MATCHDAYS_DIR,
    sealed_dir: Path = SEALED_DIR,
) -> Path | None:
    """Versiegelt eine Prognose-Datei; None, wenn alles darin schon versiegelt ist.

    Existiert die öffentliche Spieltags-Datei bereits, werden neue Paarungen
    angehängt (K.o.-Plan: Nachzügler-Batches, wenn Platzhalter feststehen).
    """
    pred = json.loads(prediction_path.read_text(encoding="utf-8"))
    public_path = matchdays_dir / public_file_name(pred)

    if public_path.exists():
        public = json.loads(public_path.read_text(encoding="utf-8"))
    else:
        public = {
            key: pred[key]
            for key in ("competition", "season", "matchday", "stage", "model_version", "kicktipp_scheme")
        }
        public["sealed_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        public["matches"] = []

    covered = {(m["home"], m["away"]) for m in public["matches"]}
    new_matches = [m for m in pred["matches"] if (m["home"], m["away"]) not in covered]
    if not new_matches:
        return None

    private_matches = []
    for m in new_matches:
        salt = secrets.token_hex(16)
        h = payload_hash(m, salt)
        public["matches"].append(
            {
                "home": m["home"],
                "away": m["away"],
                "kickoff_utc": m["kickoff_utc"],
                "status": "sealed",
                "hash": h,
            }
        )
        private_matches.append({**m, "salt": salt, "hash": h})
    public["matches"].sort(key=lambda m: (m["kickoff_utc"], m["home"]))

    matchdays_dir.mkdir(parents=True, exist_ok=True)
    public_path.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")

    sealed_dir.mkdir(parents=True, exist_ok=True)
    private = {**pred, "matches": private_matches}
    encrypted = _fernet(secret).encrypt(json.dumps(private, ensure_ascii=False).encode("utf-8"))
    (sealed_dir / f"{prediction_path.stem}.enc").write_bytes(encrypted)
    prediction_path.unlink()  # Klartext lokal nicht liegen lassen
    return public_path


def unseal_all(
    secret: str,
    matchdays_dir: Path = MATCHDAYS_DIR,
    sealed_dir: Path = SEALED_DIR,
    now: datetime | None = None,
) -> list[Path]:
    """Entsiegelt alle Spiele, deren Anstoß (+5 min) vorbei ist; gibt geänderte Dateien zurück."""
    now = now or datetime.now(timezone.utc)
    fernet = _fernet(secret)
    changed_files = []

    for enc_path in sorted(sealed_dir.glob("*.enc")):
        private = json.loads(fernet.decrypt(enc_path.read_bytes()).decode("utf-8"))
        by_pairing = {(m["home"], m["away"]): m for m in private["matches"]}

        # Öffentliche Datei aus dem Inhalt ableiten - eine Runde kann aus
        # mehreren versiegelten Batches bestehen (Nachzügler-Paarungen).
        public_path = matchdays_dir / public_file_name(private)
        public = json.loads(public_path.read_text(encoding="utf-8"))

        changed = False
        for pm in public["matches"]:
            kickoff = datetime.strptime(pm["kickoff_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            priv = by_pairing.get((pm["home"], pm["away"]))
            if priv is not None and pm["status"] == "sealed" and now >= kickoff + REVEAL_DELAY:
                pm.update({field: priv[field] for field in REVEALED_FIELDS if field in priv})
                pm["salt"] = priv["salt"]
                pm["status"] = "revealed"
                changed = True

        if changed:
            public_path.write_text(
                json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if public_path not in changed_files:
                changed_files.append(public_path)
        if all(
            pm["status"] == "revealed"
            for pm in public["matches"]
            if (pm["home"], pm["away"]) in by_pairing
        ):
            enc_path.unlink()

    return changed_files


def require_secret() -> str:
    load_dotenv()
    secret = os.environ.get("SEAL_SECRET")
    if not secret:
        raise SystemExit("SEAL_SECRET fehlt (GitHub Actions Secret bzw. lokale .env).")
    return secret


def main_seal() -> None:
    # Secret erst verlangen, wenn es wirklich etwas zu versiegeln gibt -
    # so bleibt der tägliche Lauf im Leerlauf grün.
    pending = sorted(PREDICTIONS_DIR.glob("*.json"))
    if not pending:
        print("Nichts zu versiegeln.")
        return
    secret = require_secret()
    for f in pending:
        result = seal_file(f, secret)
        if result is not None:
            print(f"Versiegelt: {result.relative_to(PROJECT_ROOT)}")


def main_unseal() -> None:
    if not list(SEALED_DIR.glob("*.enc")):
        print("Nichts zu entsiegeln.")
        return
    changed = unseal_all(require_secret())
    for path in changed:
        print(f"Entsiegelt: {path.relative_to(PROJECT_ROOT)}")
    if not changed:
        print("Nichts zu entsiegeln.")
