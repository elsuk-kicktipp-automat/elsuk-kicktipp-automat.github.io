import json
from datetime import datetime, timezone

import pytest

from engine import bonus
from engine.season import SeasonProbabilities

TEAMS = ["FC Bayern München", "Borussia Dortmund", "SV Werder Bremen", "FC Schalke 04"]


def _probabilities():
    return SeasonProbabilities(
        teams=TEAMS,
        champion={"FC Bayern München": 0.8, "Borussia Dortmund": 0.15,
                  "SV Werder Bremen": 0.04, "FC Schalke 04": 0.01},
        autumn_champion={"FC Bayern München": 0.7, "Borussia Dortmund": 0.2,
                         "SV Werder Bremen": 0.07, "FC Schalke 04": 0.03},
        bottom_three={"FC Schalke 04": 0.9, "SV Werder Bremen": 0.8,
                      "Borussia Dortmund": 0.7, "FC Bayern München": 0.6},
        expected_points={"FC Bayern München": 80.0, "Borussia Dortmund": 65.0,
                         "SV Werder Bremen": 40.0, "FC Schalke 04": 30.0},
        expected_goals_for={"FC Bayern München": 90.0, "Borussia Dortmund": 70.0,
                            "SV Werder Bremen": 45.0, "FC Schalke 04": 35.0},
        simulations=1000,
        simulated_matches=306,
    )


class TestQuestionKind:
    def test_maps_the_five_known_questions(self):
        assert bonus.question_kind("Wer wird Deutscher Meister?") == "champion"
        assert bonus.question_kind("Wer wird Herbstmeister?") == "autumn_champion"
        assert bonus.question_kind("Welche Mannschaften belegen die Plätze 16-18?") == "relegation"

    def test_ignores_case_and_punctuation(self):
        assert bonus.question_kind("wer wird DEUTSCHER meister") == "champion"

    def test_unknown_question_is_none(self):
        assert bonus.question_kind("Wer steigt in die Champions League auf?") is None

    def test_finds_question_inside_the_raw_row_text(self):
        """So kommt eine Bonuszeile wirklich von der Kicktipp-Seite: Datum davor,
        alle Dropdown-Optionen dahinter."""
        roh = (
            "28.08.26 20:30 Wer wird Deutscher Meister? -- Nicht getippt -- "
            "1. FC Köln 1. FC Union Berlin 1899 Hoffenheim FC Bayern München"
        )
        assert bonus.question_kind(roh) == "champion"

    def test_longer_question_wins_over_shorter_one(self):
        # "Wer wird Herbstmeister?" darf nicht als "Wer wird Deutscher Meister?"
        # durchgehen und umgekehrt
        assert bonus.question_kind("28.08.26 20:30 Wer wird Herbstmeister? --") == "autumn_champion"


class TestParseTeam:
    def test_finds_plain_answer(self):
        assert bonus.parse_team("Borussia Dortmund", TEAMS) == "Borussia Dortmund"

    def test_finds_answer_in_a_sentence(self):
        assert bonus.parse_team("Ich tippe auf FC Schalke 04.", TEAMS) == "FC Schalke 04"

    def test_prefers_the_longest_match(self):
        # "Bayer 04 Leverkusen" darf nicht an "Bayern München" verloren gehen
        teams = ["Bayern München", "Bayer 04 Leverkusen"]
        assert bonus.parse_team("Bayer 04 Leverkusen", teams) == "Bayer 04 Leverkusen"

    def test_unknown_or_empty_is_none(self):
        assert bonus.parse_team("Hansa Rostock", TEAMS) is None
        assert bonus.parse_team(None, TEAMS) is None


class TestAnswerQuestions:
    def test_simulation_questions_take_the_most_likely(self):
        answers, sources = bonus.answer_questions(
            _probabilities(), promoted=set(), api_key=None, llm_model="x",
            kinds=("champion", "autumn_champion", "relegation"),
        )
        assert answers["champion"] == ["FC Bayern München"]
        assert answers["autumn_champion"] == ["FC Bayern München"]
        assert answers["relegation"] == ["FC Schalke 04", "SV Werder Bremen", "Borussia Dortmund"]
        assert set(sources.values()) == {"simulation"}

    def test_falls_back_to_heuristic_without_api_key(self):
        answers, sources = bonus.answer_questions(
            _probabilities(), promoted=set(), api_key=None, llm_model="x",
            kinds=("top_scorer_club", "first_coach_change"),
        )
        assert sources == {"top_scorer_club": "heuristic", "first_coach_change": "heuristic"}
        # stärkste Offensive bzw. höchste Abstiegsgefahr
        assert answers["top_scorer_club"] == ["FC Bayern München"]
        assert answers["first_coach_change"] == ["FC Schalke 04"]

    def test_heuristic_skips_promoted_clubs_for_coach_change(self):
        answers, _ = bonus.answer_questions(
            _probabilities(), promoted={"FC Schalke 04"}, api_key=None, llm_model="x",
            kinds=("first_coach_change",),
        )
        assert answers["first_coach_change"] == ["SV Werder Bremen"]

    def test_uses_llm_answer_when_available(self, monkeypatch):
        monkeypatch.setattr(bonus, "call_groq", lambda *a, **k: "Borussia Dortmund")
        answers, sources = bonus.answer_questions(
            _probabilities(), promoted=set(), api_key="key", llm_model="x",
            kinds=("top_scorer_club",),
        )
        assert answers["top_scorer_club"] == ["Borussia Dortmund"]
        assert sources["top_scorer_club"] == "llm"

    def test_unusable_llm_answer_falls_back(self, monkeypatch):
        monkeypatch.setattr(bonus, "call_groq", lambda *a, **k: "Hansa Rostock")
        answers, sources = bonus.answer_questions(
            _probabilities(), promoted=set(), api_key="key", llm_model="x",
            kinds=("top_scorer_club",),
        )
        assert sources["top_scorer_club"] == "heuristic"
        assert answers["top_scorer_club"] == ["FC Bayern München"]


class TestScoreAnswers:
    ANSWERS = {
        "champion": ["FC Bayern München"],
        "autumn_champion": ["Borussia Dortmund"],
        "relegation": ["FC Schalke 04", "SV Werder Bremen", "Borussia Dortmund"],
        "top_scorer_club": ["FC Bayern München"],
    }
    TABLE = ["FC Bayern München", "Borussia Dortmund", "SV Werder Bremen", "FC Schalke 04"]

    def test_scores_correct_answers(self):
        report = bonus.score_answers(self.ANSWERS, self.TABLE, ["Borussia Dortmund"])
        assert report["questions"]["champion"]["points"] == 4
        assert report["questions"]["autumn_champion"]["points"] == 4

    def test_relegation_ignores_order(self):
        # Tabellenende ist Dortmund/Werder/Schalke - getippt in anderer Reihenfolge
        report = bonus.score_answers(self.ANSWERS, self.TABLE, None)
        assert report["questions"]["relegation"]["correct"] == 3
        assert report["questions"]["relegation"]["points"] == 12

    def test_unscorable_questions_stay_open(self):
        report = bonus.score_answers(self.ANSWERS, self.TABLE, self.TABLE)
        assert report["questions"]["top_scorer_club"]["points"] is None
        assert report["answers_open"] == 1

    def test_nothing_scored_without_a_table(self):
        report = bonus.score_answers(self.ANSWERS, None, None)
        assert report["points_total"] == 0
        assert report["answers_scored"] == 0


class TestSealRoundTrip:
    def _bonus(self):
        return {
            "id": "bl1_2026_bonus",
            "competition": "bl1",
            "season": 2026,
            "created_utc": "2026-08-20T10:00:00Z",
            "reveal_after_utc": "2026-08-28T18:30:00Z",
            "answers": {"champion": ["FC Bayern München"], "relegation": TEAMS[1:]},
            "sources": {"champion": "simulation", "relegation": "simulation"},
        }

    def test_sealed_file_hides_the_answers(self, tmp_path):
        bonus.seal_bonus(self._bonus(), "geheim", bonus_dir=tmp_path)
        public = json.loads((tmp_path / "bl1_2026_bonus.json").read_text(encoding="utf-8"))
        assert public["status"] == "sealed"
        assert "answers" not in public
        assert len(public["hash"]) == 64
        # Die Fragen selbst dürfen öffentlich sein, die Antworten nicht
        assert {q["kind"] for q in public["questions"]} == {"champion", "relegation"}
        assert b"Bayern" not in (tmp_path / "bl1_2026_bonus.enc").read_bytes()

    def test_reveals_only_after_the_deadline(self, tmp_path):
        bonus.seal_bonus(self._bonus(), "geheim", bonus_dir=tmp_path)
        early = datetime(2026, 8, 28, 18, 29, tzinfo=timezone.utc)
        assert bonus.unseal_due("geheim", bonus_dir=tmp_path, now=early) == []

        due = datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)
        assert len(bonus.unseal_due("geheim", bonus_dir=tmp_path, now=due)) == 1
        public = json.loads((tmp_path / "bl1_2026_bonus.json").read_text(encoding="utf-8"))
        assert public["status"] == "revealed"
        assert public["answers"]["champion"] == ["FC Bayern München"]
        assert not (tmp_path / "bl1_2026_bonus.enc").exists()

    def test_published_hash_matches_the_revealed_answers(self, tmp_path):
        bonus.seal_bonus(self._bonus(), "geheim", bonus_dir=tmp_path)
        due = datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)
        bonus.unseal_due("geheim", bonus_dir=tmp_path, now=due)
        public = json.loads((tmp_path / "bl1_2026_bonus.json").read_text(encoding="utf-8"))
        # Jeder kann den veröffentlichten Hash nachrechnen
        assert bonus.payload_hash(public, public["salt"]) == public["hash"]

    def test_pending_answers_come_from_the_encrypted_file(self, tmp_path):
        bonus.seal_bonus(self._bonus(), "geheim", bonus_dir=tmp_path)
        answers = bonus.load_pending_answers("geheim", bonus_dir=tmp_path)
        assert answers["champion"] == ["FC Bayern München"]

    def test_no_pending_answers_after_reveal(self, tmp_path):
        bonus.seal_bonus(self._bonus(), "geheim", bonus_dir=tmp_path)
        bonus.unseal_due("geheim", bonus_dir=tmp_path,
                         now=datetime(2026, 8, 29, tzinfo=timezone.utc))
        assert bonus.load_pending_answers("geheim", bonus_dir=tmp_path) == {}
