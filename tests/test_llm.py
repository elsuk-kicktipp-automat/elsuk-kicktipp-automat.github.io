import subprocess

import requests

from engine.llm import (
    build_adjustment_prompt,
    build_prompt,
    call_claude,
    call_groq,
    check_begruendung,
    generate_begruendung,
    parse_adjustment_response,
    propose_adjustment,
    summarize_usage,
)

MATCH_CONTEXT = {
    "home": "Schweiz",
    "away": "Algerien",
    "stage": "Sechzehntelfinale",
    "probabilities": {"home": 0.49, "draw": 0.31, "away": 0.20},
    "expected_goals": (1.73, 1.09),
    "tip": (2, 1),
    "market_probabilities": None,
}


class TestBuildPrompt:
    def test_contains_key_facts(self):
        prompt = build_prompt(MATCH_CONTEXT)
        assert "Schweiz" in prompt
        assert "Algerien" in prompt
        assert "49%" in prompt
        assert "2:1" in prompt

    def test_includes_market_when_present(self):
        context = {**MATCH_CONTEXT, "market_probabilities": {"home": 0.45, "draw": 0.30, "away": 0.25}}
        prompt = build_prompt(context)
        assert "Buchmacherquoten" in prompt

    def test_omits_market_section_when_absent(self):
        # Der Anweisungssatz nennt "Buchmacherquoten" generisch als möglichen
        # Faktor - hier geht es um die konkrete Datenzeile mit Prozentwerten.
        prompt = build_prompt(MATCH_CONTEXT)
        assert "- Buchmacherquoten" not in prompt

    def test_mentions_elo_when_present(self):
        context = {**MATCH_CONTEXT, "elo": {"home": 1683.0, "away": 1608.0}}
        prompt = build_prompt(context)
        assert "1683" in prompt and "1608" in prompt

    def test_omits_elo_when_absent(self):
        prompt = build_prompt(MATCH_CONTEXT)
        assert "- ELO-Bewertung" not in prompt

    def test_includes_llm_adjustment_when_present(self):
        context = {
            **MATCH_CONTEXT,
            "llm_adjustment": {"tip": [1, 1], "grund": "Stammtorwart fehlt", "news_count": 3},
        }
        prompt = build_prompt(context)
        assert "Stammtorwart fehlt" in prompt
        assert "News-Check" in prompt

    def test_includes_news_checked_without_adjustment(self):
        context = {**MATCH_CONTEXT, "news_checked": 2}
        prompt = build_prompt(context)
        assert "2 aktuelle Schlagzeile" in prompt

    def test_asks_for_short_plain_language_text(self):
        prompt = build_prompt(MATCH_CONTEXT)
        assert "3-4 Sätze" in prompt
        assert "Vermeide Fachwörter" in prompt
        assert "Erfinde nichts" in prompt

    def test_allows_timeless_team_knowledge_but_no_third_team(self):
        prompt = build_prompt(MATCH_CONTEXT)
        assert "Spitznamen, Stadion" in prompt
        assert "keine andere" in prompt

    def test_neutral_venue_replaces_home_away_roles(self):
        prompt = build_prompt({**MATCH_CONTEXT, "neutral_venue": True})
        assert "neutraler Platz" in prompt
        assert "(Heim)" not in prompt

    def test_default_keeps_home_away_roles(self):
        prompt = build_prompt(MATCH_CONTEXT)
        assert "(Heim)" in prompt
        assert "neutraler Platz" not in prompt


class TestCallGroq:
    def test_returns_text_on_success(self, monkeypatch):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "  Klarer Heimsieg erwartet.  "}}]}

        monkeypatch.setattr("engine.llm.requests.post", lambda *a, **kw: FakeResponse())
        result = call_groq("prompt", "fake-key")
        assert result == "Klarer Heimsieg erwartet."

    def test_records_token_usage(self, monkeypatch):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 540, "completion_tokens": 120},
                }

        usage = []
        monkeypatch.setattr("engine.llm.requests.post", lambda *a, **kw: FakeResponse())
        call_groq("prompt", "fake-key", usage=usage)
        assert usage == [{"model": "openai/gpt-oss-120b", "input_tokens": 540, "output_tokens": 120}]

    def test_returns_none_on_network_error(self, monkeypatch):
        def raise_error(*args, **kwargs):
            raise requests.ConnectionError("down")

        monkeypatch.setattr("engine.llm.requests.post", raise_error)
        assert call_groq("prompt", "fake-key") is None

    def test_returns_none_on_malformed_response(self, monkeypatch):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"unexpected": "shape"}

        monkeypatch.setattr("engine.llm.requests.post", lambda *a, **kw: FakeResponse())
        assert call_groq("prompt", "fake-key") is None

    def test_returns_none_on_empty_content(self, monkeypatch):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "   "}}]}

        monkeypatch.setattr("engine.llm.requests.post", lambda *a, **kw: FakeResponse())
        assert call_groq("prompt", "fake-key") is None


class FakeProc:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.returncode = returncode


class TestCallClaude:
    def test_returns_text_on_success(self, monkeypatch):
        monkeypatch.setattr("engine.llm.shutil.which", lambda name: "/usr/bin/claude")
        monkeypatch.setattr(
            "engine.llm.subprocess.run",
            lambda *a, **kw: FakeProc('{"is_error": false, "result": "  Klarer Heimsieg erwartet.  "}'),
        )
        assert call_claude("prompt") == "Klarer Heimsieg erwartet."

    def test_records_token_usage_including_cached_input(self, monkeypatch):
        stdout = (
            '{"is_error": false, "result": "Text", "usage": {"input_tokens": 2, '
            '"cache_creation_input_tokens": 1423, "cache_read_input_tokens": 5, "output_tokens": 230}}'
        )
        usage = []
        monkeypatch.setattr("engine.llm.shutil.which", lambda name: "/usr/bin/claude")
        monkeypatch.setattr("engine.llm.subprocess.run", lambda *a, **kw: FakeProc(stdout))
        call_claude("prompt", usage=usage)
        assert usage == [{"model": "claude-opus-5-5", "input_tokens": 1430, "output_tokens": 230}]

    def test_failed_call_records_no_usage(self, monkeypatch):
        usage = []
        monkeypatch.setattr("engine.llm.shutil.which", lambda name: "/usr/bin/claude")
        monkeypatch.setattr(
            "engine.llm.subprocess.run",
            lambda *a, **kw: FakeProc('{"is_error": true, "result": "API Error: 400"}', returncode=1),
        )
        assert call_claude("prompt", usage=usage) is None
        assert usage == []

    def test_missing_cli_returns_none(self, monkeypatch):
        def must_not_run(*args, **kwargs):
            raise AssertionError("ohne CLI darf kein Prozess gestartet werden")

        monkeypatch.setattr("engine.llm.shutil.which", lambda name: None)
        monkeypatch.setattr("engine.llm.subprocess.run", must_not_run)
        assert call_claude("prompt") is None

    def test_error_message_is_never_returned_as_text(self, monkeypatch):
        # Limit erreicht, CLI zu alt, nicht angemeldet: die Meldung steht im
        # result-Feld und würde sonst als Begründung versiegelt
        monkeypatch.setattr("engine.llm.shutil.which", lambda name: "/usr/bin/claude")
        monkeypatch.setattr(
            "engine.llm.subprocess.run",
            lambda *a, **kw: FakeProc('{"is_error": true, "result": "API Error: 400"}', returncode=1),
        )
        assert call_claude("prompt") is None

    def test_returns_none_on_timeout(self, monkeypatch):
        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=180)

        monkeypatch.setattr("engine.llm.shutil.which", lambda name: "/usr/bin/claude")
        monkeypatch.setattr("engine.llm.subprocess.run", raise_timeout)
        assert call_claude("prompt") is None

    def test_returns_none_on_malformed_output(self, monkeypatch):
        monkeypatch.setattr("engine.llm.shutil.which", lambda name: "/usr/bin/claude")
        monkeypatch.setattr("engine.llm.subprocess.run", lambda *a, **kw: FakeProc("kein JSON"))
        assert call_claude("prompt") is None

    def test_api_credentials_are_not_passed_on(self, monkeypatch):
        # Abo statt API: ein gesetzter API-Schlüssel hätte Vorrang und würde berechnet
        seen = {}

        def fake_run(*args, **kwargs):
            seen.update(kwargs["env"])
            return FakeProc('{"is_error": false, "result": "Text"}')

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "abo-token")
        monkeypatch.setattr("engine.llm.shutil.which", lambda name: "/usr/bin/claude")
        monkeypatch.setattr("engine.llm.subprocess.run", fake_run)
        call_claude("prompt")
        assert "ANTHROPIC_API_KEY" not in seen
        assert "ANTHROPIC_AUTH_TOKEN" not in seen
        assert seen["CLAUDE_CODE_OAUTH_TOKEN"] == "abo-token"


BVB_CONTEXT = {"home": "Borussia Dortmund", "away": "SV Werder Bremen", "tip": (2, 1)}
LIGA = ["FC Bayern München", "Borussia Mönchengladbach", "Bayer 04 Leverkusen", "1. FC Köln"]
# Am 09.10.2026 so versiegelt (gpt-oss-120b): die dritte Mannschaft ist frei erfunden
BAYERN_TEXT = (
    "Tipp: 2 : 1. Dortmund geht mit klarer Übermacht in die Partie, die 71 % Siegchance "
    "sprechen für ein Torfestival zu Hause, während Bremen kaum mehr als ein Gegentreffer "
    "zu erwarten hat. Die Bayern setzen auf ein schnelles Pressing und ein schnelles "
    "Umschalten, das die Gäste in ein Abnutzungsspiel zwingt."
)


class TestCheckBegruendung:
    def test_rejects_invented_third_team(self):
        assert check_begruendung(BAYERN_TEXT, BVB_CONTEXT, LIGA) == "nennt dritte Mannschaft (FC Bayern München)"

    def test_accepts_own_knowledge_about_both_teams(self):
        text = (
            "2:1 für Dortmund. Der BVB ist zu Hause klarer Favorit, auf der Südtribüne "
            "grummelt es trotzdem, weil Werder ein Tor zuzutrauen ist. Die Schwarz-Gelben "
            "behalten die Punkte."
        )
        assert check_begruendung(text, BVB_CONTEXT, LIGA) is None

    def test_rejects_inflected_name(self):
        assert check_begruendung("Tipp 2:1 - die Kölner kommen als Außenseiter.", BVB_CONTEXT, LIGA) is not None

    def test_shared_name_part_is_not_a_third_team(self):
        # "Borussia" steckt auch in Mönchengladbach, gemeint ist hier Dortmund
        assert check_begruendung("Tipp 2:1 - die Borussia gewinnt.", BVB_CONTEXT, LIGA) is None

    def test_bayer_and_bayern_are_kept_apart(self):
        bayern = {"home": "FC Bayern München", "away": "1. FC Union Berlin", "tip": (4, 0)}
        assert check_begruendung("4:0 - die Bayern sind haushoher Favorit.", bayern, ["Bayer 04 Leverkusen"]) is None
        leverkusen = {"home": "Bayer 04 Leverkusen", "away": "RB Leipzig", "tip": (2, 1)}
        assert check_begruendung("2:1 - unterm Bayer-Kreuz bleibt der Dreier.", leverkusen, LIGA) is None
        assert check_begruendung("2:1 - die Bayerner haben das bessere Gespür.", leverkusen, LIGA) is not None

    def test_rejects_text_without_the_tip(self):
        assert check_begruendung("Dortmund gewinnt knapp.", BVB_CONTEXT, LIGA) == "Tipp fehlt im Text"
        assert check_begruendung("Dortmund gewinnt 12:1.", BVB_CONTEXT, LIGA) == "Tipp fehlt im Text"


class TestGenerateBegruendung:
    def test_claude_unavailable_falls_back_to_template(self, monkeypatch):
        monkeypatch.setattr("engine.llm.call_claude", lambda prompt, **kw: None)
        text, source = generate_begruendung(MATCH_CONTEXT)
        assert text is None
        assert source == "template"

    def test_successful_llm_call(self, monkeypatch):
        monkeypatch.setattr("engine.llm.call_claude", lambda prompt, **kw: "Tipp 2:1, die Schweiz liegt vorn.")
        text, source = generate_begruendung(MATCH_CONTEXT, other_teams=["Frankreich"])
        assert text == "Tipp 2:1, die Schweiz liegt vorn."
        assert source == "llm"

    def test_rejected_text_gets_one_more_attempt(self, monkeypatch):
        answers = ["Tipp 2:1, wie zuletzt Frankreich.", "Tipp 2:1, die Schweiz liegt vorn."]
        monkeypatch.setattr("engine.llm.call_claude", lambda prompt, **kw: answers.pop(0))
        text, source = generate_begruendung(MATCH_CONTEXT, other_teams=["Frankreich"])
        assert text == "Tipp 2:1, die Schweiz liegt vorn."
        assert source == "llm"

    def test_two_rejected_texts_fall_back_to_template(self, monkeypatch):
        calls = []

        def always_third_team(prompt, **kw):
            calls.append(1)
            return "Tipp 2:1, wie zuletzt Frankreich."

        monkeypatch.setattr("engine.llm.call_claude", always_third_team)
        text, source = generate_begruendung(MATCH_CONTEXT, other_teams=["Frankreich"])
        assert text is None
        assert source == "template"
        assert len(calls) == 2

    def test_never_falls_back_to_groq(self, monkeypatch):
        # Die Groq-Texte waren der Anlass der Umstellung: ohne Claude gilt das Template
        def groq_must_not_run(*args, **kwargs):
            raise AssertionError("für Begründungen gibt es keinen Groq-Ersatz")

        monkeypatch.setattr("engine.llm.call_groq", groq_must_not_run)
        assert generate_begruendung(MATCH_CONTEXT) == (None, "template")

    def test_rejected_attempt_still_counts_as_usage(self, monkeypatch):
        answers = ["Tipp 2:1, wie zuletzt Frankreich.", "Tipp 2:1, die Schweiz liegt vorn."]

        def fake_claude(prompt, usage=None, **kw):
            usage.append({"model": "claude-opus-5-5", "input_tokens": 1400, "output_tokens": 250})
            return answers.pop(0)

        usage = []
        monkeypatch.setattr("engine.llm.call_claude", fake_claude)
        generate_begruendung(MATCH_CONTEXT, other_teams=["Frankreich"], usage=usage)
        assert [c["zweck"] for c in usage] == ["begruendung", "begruendung"]
        assert summarize_usage(usage)["input_tokens"] == 2800


ADJUSTMENT_CONTEXT = {"home": "Deutschland", "away": "Portugal", "tip": (2, 1)}
NEWS = [{"source": "kicker", "title": "Kapitän verletzt", "description": "Fällt aus."}]


class TestBuildAdjustmentPrompt:
    def test_lists_news_items(self):
        prompt = build_adjustment_prompt(ADJUSTMENT_CONTEXT, NEWS)
        assert "Kapitän verletzt" in prompt
        assert "kicker" in prompt
        assert "2:1" in prompt


class TestParseAdjustmentResponse:
    def test_valid_adjustment(self):
        text = '{"adjust": true, "home_delta": -1, "away_delta": 0, "grund": "Stammtorwart fehlt"}'
        result = parse_adjustment_response(text)
        assert result == {"home_delta": -1, "away_delta": 0, "grund": "Stammtorwart fehlt"}

    def test_adjust_false_returns_none(self):
        text = '{"adjust": false, "home_delta": 0, "away_delta": 0, "grund": "nichts Relevantes"}'
        assert parse_adjustment_response(text) is None

    def test_no_op_delta_returns_none(self):
        # adjust=true aber beide Deltas 0 -> nichts zu tun
        text = '{"adjust": true, "home_delta": 0, "away_delta": 0, "grund": "x"}'
        assert parse_adjustment_response(text) is None

    def test_clamps_out_of_range_delta(self):
        text = '{"adjust": true, "home_delta": -3, "away_delta": 2, "grund": "x"}'
        result = parse_adjustment_response(text)
        assert result["home_delta"] == -1
        assert result["away_delta"] == 1

    def test_extracts_json_from_surrounding_prose(self):
        text = 'Hier ist meine Antwort: {"adjust": true, "home_delta": 1, "away_delta": 0, "grund": "x"} Danke.'
        result = parse_adjustment_response(text)
        assert result["home_delta"] == 1

    def test_malformed_json_returns_none(self):
        assert parse_adjustment_response("das ist kein JSON") is None
        assert parse_adjustment_response('{"adjust": true, "home_delta":}') is None

    def test_missing_delta_returns_none(self):
        text = '{"adjust": true, "grund": "x"}'
        assert parse_adjustment_response(text) is None


class TestSummarizeUsage:
    def test_no_calls_is_none(self):
        assert summarize_usage([]) is None

    def test_sums_all_calls_of_a_match(self):
        calls = [
            {"zweck": "news", "model": "claude-opus-5-5", "input_tokens": 900, "output_tokens": 60},
            {"zweck": "begruendung", "model": "claude-opus-5-5", "input_tokens": 1400, "output_tokens": 250},
        ]
        assert summarize_usage(calls) == {"input_tokens": 2300, "output_tokens": 310, "calls": calls}


class TestProposeAdjustment:
    def test_no_news_skips_llm_call_entirely(self, monkeypatch):
        called = []
        monkeypatch.setattr("engine.llm.call_groq", lambda *a, **kw: called.append(1))
        result = propose_adjustment(ADJUSTMENT_CONTEXT, [], api_key="fake-key")
        assert result is None
        assert called == []  # kein API-Call ohne News - nichts zu begründen

    def test_without_claude_and_api_key_returns_none(self):
        assert propose_adjustment(ADJUSTMENT_CONTEXT, NEWS, api_key=None) is None

    def test_claude_answers_first_and_groq_is_not_asked(self, monkeypatch):
        def groq_must_not_run(*args, **kwargs):
            raise AssertionError("Groq ist nur Ersatz, wenn Claude ausfällt")

        def fake_claude(prompt, usage=None, **kw):
            usage.append({"model": "claude-opus-5-5", "input_tokens": 900, "output_tokens": 60})
            return '{"adjust": true, "home_delta": -1, "away_delta": 0, "grund": "Verletzung"}'

        usage = []
        monkeypatch.setattr("engine.llm.call_claude", fake_claude)
        monkeypatch.setattr("engine.llm.call_groq", groq_must_not_run)
        result = propose_adjustment(ADJUSTMENT_CONTEXT, NEWS, api_key="fake-key", usage=usage)
        assert result == {"home_delta": -1, "away_delta": 0, "grund": "Verletzung"}
        assert usage == [
            {"zweck": "news", "model": "claude-opus-5-5", "input_tokens": 900, "output_tokens": 60}
        ]

    def test_claude_works_without_groq_key(self, monkeypatch):
        monkeypatch.setattr(
            "engine.llm.call_claude",
            lambda prompt, **kw: '{"adjust": true, "home_delta": 0, "away_delta": 1, "grund": "Sperre"}',
        )
        result = propose_adjustment(ADJUSTMENT_CONTEXT, NEWS, api_key=None)
        assert result == {"home_delta": 0, "away_delta": 1, "grund": "Sperre"}

    def test_successful_proposal(self, monkeypatch):
        monkeypatch.setattr(
            "engine.llm.call_groq",
            lambda *a, **kw: '{"adjust": true, "home_delta": -1, "away_delta": 0, "grund": "Verletzung"}',
        )
        result = propose_adjustment(ADJUSTMENT_CONTEXT, NEWS, api_key="fake-key")
        assert result == {"home_delta": -1, "away_delta": 0, "grund": "Verletzung"}

    def test_llm_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr("engine.llm.call_groq", lambda *a, **kw: None)
        assert propose_adjustment(ADJUSTMENT_CONTEXT, NEWS, api_key="fake-key") is None
