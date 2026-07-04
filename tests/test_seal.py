import hashlib
import json
from datetime import datetime, timezone

import pytest

from engine.seal import canonical_payload, payload_hash, seal_file, unseal_all

SECRET = "test-geheimnis"
# Fest vor beiden Fixture-Anstößen (17:00/21:00Z) - der Fairness-Guard in
# seal_file würde sonst greifen, sobald die Echtzeit die Kickoffs überholt.
SEAL_NOW = datetime(2026, 7, 4, 6, 0, tzinfo=timezone.utc)

PREDICTION = {
    "competition": "wm2026",
    "season": 2026,
    "matchday": 5,
    "stage": "Achtelfinale",
    "created_utc": "2026-07-04T06:00:00Z",
    "model_version": "dixon-coles-elo-1",
    "kicktipp_scheme": {"exact": 4, "goal_diff": 3, "tendency": 2},
    "matches": [
        {
            "home": "Kanada",
            "away": "Marokko",
            "kickoff_utc": "2026-07-04T17:00:00Z",
            "matchday": 5,
            "stage": "Achtelfinale",
            "tip": [1, 1],
            "expected_points": 1.2,
            "factors": {"expected_goals": [1.1, 1.2]},
            "advance_tip": {"pick": "Kanada", "probability": 0.52},
            "begruendung": "Ausgeglichenes Spiel.",
        },
        {
            "home": "Paraguay",
            "away": "Frankreich",
            "kickoff_utc": "2026-07-04T21:00:00Z",
            "matchday": 5,
            "stage": "Achtelfinale",
            "tip": [0, 2],
            "expected_points": 1.6,
            "factors": {"expected_goals": [0.6, 1.9]},
            "advance_tip": None,
            "begruendung": "Frankreich klar vorn.",
        },
    ],
}


@pytest.fixture
def dirs(tmp_path):
    pred_file = tmp_path / "predictions" / "wm2026_2026_md05.json"
    pred_file.parent.mkdir()
    pred_file.write_text(json.dumps(PREDICTION, ensure_ascii=False), encoding="utf-8")
    matchdays = tmp_path / "matchdays"
    sealed = tmp_path / "sealed"
    matchdays.mkdir()
    sealed.mkdir()
    return pred_file, matchdays, sealed


class TestSeal:
    def test_public_file_contains_hashes_but_no_tips(self, dirs):
        pred_file, matchdays, sealed = dirs
        public_path = seal_file(pred_file, SECRET, matchdays, sealed, now=SEAL_NOW)
        text = public_path.read_text(encoding="utf-8")
        public = json.loads(text)

        assert all(m["status"] == "sealed" for m in public["matches"])
        assert all(len(m["hash"]) == 64 for m in public["matches"])
        assert '"tip"' not in text
        assert '"begruendung"' not in text
        assert "Frankreich klar vorn" not in text

    def test_encrypted_blob_is_not_plaintext(self, dirs):
        pred_file, matchdays, sealed = dirs
        seal_file(pred_file, SECRET, matchdays, sealed, now=SEAL_NOW)
        blob = (sealed / "wm2026_2026_md05.enc").read_bytes()
        assert b"Marokko" not in blob
        assert b"tip" not in blob

    def test_seal_consumes_prediction_and_is_idempotent(self, dirs):
        pred_file, matchdays, sealed = dirs
        assert seal_file(pred_file, SECRET, matchdays, sealed, now=SEAL_NOW) is not None
        # Klartext-Datei wird nach dem Versiegeln entfernt
        assert not pred_file.exists()
        # Gleiche Paarungen erneut versiegeln: nichts zu tun
        pred_file.write_text(json.dumps(PREDICTION, ensure_ascii=False), encoding="utf-8")
        assert seal_file(pred_file, SECRET, matchdays, sealed, now=SEAL_NOW) is None

    def test_never_seals_matches_after_kickoff(self, dirs):
        """Fairness-Guard: Versiegelung nach Anstoß würde den Beweis
        'Tipp stand vorher fest' wertlos machen."""
        pred_file, matchdays, sealed = dirs
        # Zwischen den beiden Anstößen (17:00 vorbei, 21:00 noch nicht):
        # nur das 21:00-Spiel darf versiegelt werden
        between = datetime(2026, 7, 4, 18, 0, tzinfo=timezone.utc)
        public_path = seal_file(pred_file, SECRET, matchdays, sealed, now=between)
        public = json.loads(public_path.read_text(encoding="utf-8"))
        assert [m["home"] for m in public["matches"]] == ["Paraguay"]

    def test_returns_none_when_all_kickoffs_passed(self, dirs):
        pred_file, matchdays, sealed = dirs
        after_all = datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc)
        assert seal_file(pred_file, SECRET, matchdays, sealed, now=after_all) is None
        assert not list(matchdays.glob("*.json"))
        assert not list(sealed.glob("*.enc"))

    def test_late_batch_is_appended_to_existing_matchday(self, dirs):
        """K.o.-Plan: Nachzügler-Paarungen kommen als zweiter Batch dazu."""
        pred_file, matchdays, sealed = dirs
        seal_file(pred_file, SECRET, matchdays, sealed, now=SEAL_NOW)

        batch = {**PREDICTION, "matches": [
            {
                "home": "Brasilien", "away": "Norwegen",
                "kickoff_utc": "2026-07-05T20:00:00Z", "matchday": 5,
                "stage": "Achtelfinale", "tip": [2, 0], "expected_points": 1.9,
                "factors": {"expected_goals": [2.1, 0.7]},
                "advance_tip": None,
                "begruendung": "Brasilien klar vorn.",
            }
        ]}
        batch_file = pred_file.parent / "wm2026_2026_md05_b2.json"
        batch_file.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
        seal_file(batch_file, SECRET, matchdays, sealed, now=SEAL_NOW)

        public = json.loads((matchdays / "wm2026_2026_md05.json").read_text(encoding="utf-8"))
        assert len(public["matches"]) == 3
        assert (sealed / "wm2026_2026_md05.enc").exists()
        assert (sealed / "wm2026_2026_md05_b2.enc").exists()

        # Alles entsiegeln: beide Batches landen in derselben Datei, .enc weg
        changed = unseal_all(SECRET, matchdays, sealed, now=datetime(2026, 7, 6, tzinfo=timezone.utc))
        assert len(changed) == 1
        public = json.loads(changed[0].read_text(encoding="utf-8"))
        assert all(m["status"] == "revealed" for m in public["matches"])
        assert not list(sealed.glob("*.enc"))


class TestUnseal:
    def test_reveals_only_past_kickoffs(self, dirs):
        pred_file, matchdays, sealed = dirs
        seal_file(pred_file, SECRET, matchdays, sealed, now=SEAL_NOW)

        # 17:00-Spiel ist (inkl. 5 min Verzögerung) durch, 21:00-Spiel nicht
        now = datetime(2026, 7, 4, 17, 10, tzinfo=timezone.utc)
        changed = unseal_all(SECRET, matchdays, sealed, now=now)
        assert len(changed) == 1

        public = json.loads(changed[0].read_text(encoding="utf-8"))
        first, second = public["matches"]
        assert first["status"] == "revealed"
        assert first["tip"] == [1, 1]
        assert first["advance_tip"] == {"pick": "Kanada", "probability": 0.52}
        assert first["begruendung"] == "Ausgeglichenes Spiel."
        assert second["status"] == "sealed"
        assert "tip" not in second
        # Verschlüsselte Datei bleibt, solange noch etwas versiegelt ist
        assert (sealed / "wm2026_2026_md05.enc").exists()

    def test_full_reveal_removes_encrypted_blob(self, dirs):
        pred_file, matchdays, sealed = dirs
        seal_file(pred_file, SECRET, matchdays, sealed, now=SEAL_NOW)
        now = datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc)
        unseal_all(SECRET, matchdays, sealed, now=now)
        assert not (sealed / "wm2026_2026_md05.enc").exists()

    def test_revealed_hash_is_verifiable(self, dirs):
        """Der Kern des Fairness-Beweises: Hash aus enthüllten Daten nachrechnen."""
        pred_file, matchdays, sealed = dirs
        public_path = seal_file(pred_file, SECRET, matchdays, sealed, now=SEAL_NOW)
        published_hashes = {
            (m["home"], m["away"]): m["hash"]
            for m in json.loads(public_path.read_text(encoding="utf-8"))["matches"]
        }

        unseal_all(SECRET, matchdays, sealed, now=datetime(2026, 7, 5, tzinfo=timezone.utc))
        public = json.loads(public_path.read_text(encoding="utf-8"))
        for m in public["matches"]:
            recomputed = hashlib.sha256(
                json.dumps(
                    {
                        "home": m["home"],
                        "away": m["away"],
                        "kickoff_utc": m["kickoff_utc"],
                        "tip": m["tip"],
                        "advance_tip": m["advance_tip"],
                        "begruendung": m["begruendung"],
                        "salt": m["salt"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            assert recomputed == published_hashes[(m["home"], m["away"])]
            assert recomputed == payload_hash(m, m["salt"])

    def test_wrong_secret_cannot_decrypt(self, dirs):
        pred_file, matchdays, sealed = dirs
        seal_file(pred_file, SECRET, matchdays, sealed, now=SEAL_NOW)
        with pytest.raises(Exception):
            unseal_all("falsches-geheimnis", matchdays, sealed)


class TestCanonicalPayload:
    def test_is_stable_and_sorted(self):
        match = PREDICTION["matches"][0]
        payload = canonical_payload(match, "abc")
        assert payload.index('"away"') < payload.index('"home"') < payload.index('"salt"')
        assert canonical_payload(match, "abc") == payload

    def test_includes_paper_bet_when_present(self):
        match = {**PREDICTION["matches"][0], "paper_bet": {"stake_eur": 10.0}}
        payload = canonical_payload(match, "abc")
        assert '"paper_bet"' in payload
        assert payload_hash(match, "abc") == hashlib.sha256(payload.encode("utf-8")).hexdigest()
