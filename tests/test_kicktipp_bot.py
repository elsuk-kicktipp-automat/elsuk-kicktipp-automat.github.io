import json
from datetime import datetime, timezone

import pytest

import engine.kicktipp_bot as bot
from engine.kicktipp_bot import (
    LOGIN_URL,
    _is_logged_in,
    load_pending_tips,
    login,
    verification_mismatches,
)


class FakeCountLocator:
    """Simuliert page.locator(sel).count(): 0, sobald eingeloggt (kein #kennung-Feld mehr)."""

    def __init__(self, page):
        self.page = page

    def count(self):
        return 0 if self.page.logged_in else 1


class FakeClickLocator:
    def __init__(self, page, succeeds: bool):
        self.page = page
        self.succeeds = succeeds

    def click(self, timeout=None):
        if self.succeeds:
            self.page.logged_in = True


class FakePage:
    """Deckt genau die Playwright-Methoden ab, die login()/_is_logged_in() aufrufen."""

    def __init__(self, login_succeeds: bool = True, logged_in: bool = False):
        self.logged_in = logged_in
        self.login_succeeds = login_succeeds
        self.filled = {}
        self.url = ""

    def goto(self, url):
        self.url = url

    def fill(self, selector, value):
        self.filled[selector] = value

    def locator(self, selector):
        return FakeCountLocator(self)

    def get_by_role(self, role, name=None):
        return FakeClickLocator(self, self.login_succeeds)

    def wait_for_load_state(self, state):
        pass


class TestIsLoggedIn:
    def test_login_form_present_means_not_logged_in(self):
        assert not _is_logged_in(FakePage(logged_in=False))

    def test_login_form_absent_means_logged_in(self):
        assert _is_logged_in(FakePage(logged_in=True))


class TestLogin:
    def test_successful_login_fills_credentials_and_clicks_anmelden(self, monkeypatch):
        monkeypatch.setattr("engine.kicktipp_bot._accept_consent", lambda page: None)
        page = FakePage(login_succeeds=True, logged_in=False)

        login(page, "bot@example.com", "geheim")

        assert page.filled["#kennung"] == "bot@example.com"
        assert page.filled["#passwort"] == "geheim"
        assert page.logged_in is True

    def test_already_logged_in_skips_form_fill(self, monkeypatch):
        monkeypatch.setattr("engine.kicktipp_bot._accept_consent", lambda page: None)
        page = FakePage(logged_in=True)  # #kennung nicht vorhanden -> schon eingeloggt

        login(page, "bot@example.com", "geheim")

        assert page.filled == {}

    def test_login_form_still_present_after_submit_raises(self, monkeypatch):
        monkeypatch.setattr("engine.kicktipp_bot._accept_consent", lambda page: None)
        page = FakePage(login_succeeds=False, logged_in=False)  # falsches Passwort

        with pytest.raises(RuntimeError):
            login(page, "bot@example.com", "falsch")

    def test_uses_documented_login_url(self):
        assert LOGIN_URL == "https://www.kicktipp.de/info/profil/login/"


class TestLoadPendingTips:
    """Tipps kommen aus data/sealed/*.enc - nur Spiele mit künftigem Anstoß."""

    def _write_enc(self, sealed_dir, matches):
        from engine.seal import _fernet

        blob = json.dumps({"matches": matches}, ensure_ascii=False).encode("utf-8")
        (sealed_dir / "wm26_2026_md05.enc").write_bytes(_fernet("test-geheimnis").encrypt(blob))

    def test_filters_past_kickoffs(self, tmp_path):
        self._write_enc(tmp_path, [
            {"home": "Kanada", "away": "Marokko", "kickoff_utc": "2026-07-04T17:00:00Z", "tip": [1, 2]},
            {"home": "Paraguay", "away": "Frankreich", "kickoff_utc": "2026-07-04T21:00:00Z", "tip": [0, 2]},
        ])
        now = datetime(2026, 7, 4, 18, 0, tzinfo=timezone.utc)  # 17:00 vorbei, 21:00 nicht
        tips = load_pending_tips("test-geheimnis", now=now, sealed_dir=tmp_path)
        assert tips == {("paraguay", "frankreich"): (0, 2)}

    def test_empty_dir_returns_empty(self, tmp_path):
        assert load_pending_tips("test-geheimnis", sealed_dir=tmp_path) == {}


class TestVerificationMismatches:
    def test_all_saved_correctly(self):
        filled = {("a", "b"): (1, 2)}
        saved = {("a", "b"): ("1", "2")}
        assert verification_mismatches(filled, saved) == []

    def test_detects_wrong_value(self):
        filled = {("a", "b"): (1, 2)}
        saved = {("a", "b"): ("1", "0")}  # Kicktipp hat den Gast-Tipp verworfen
        assert verification_mismatches(filled, saved) == [("a", "b")]

    def test_detects_missing_pairing(self):
        filled = {("a", "b"): (1, 2)}
        assert verification_mismatches(filled, {}) == [("a", "b")]


class TestMainFailsLoudly:
    """Anomalien müssen den Lauf non-zero beenden -> GitHub-Fehlermail (concept.md §6)."""

    CONFIG = {"kicktipp_submission": {"enabled": True, "dry_run": False}}

    @pytest.fixture
    def env(self, monkeypatch):
        for var in ("KICKTIPP_EMAIL", "KICKTIPP_PASSWORD", "KICKTIPP_RUNDE", "SEAL_SECRET"):
            monkeypatch.setenv(var, "x")
        monkeypatch.setattr(bot, "load_dotenv", lambda: None)
        monkeypatch.setattr(bot, "load_pending_tips", lambda secret: {("a", "b"): (1, 2)})

    def _log(self, **overrides):
        log = {
            "filled": [("a", "b")], "skipped_already_tipped": [], "skipped_no_input": [],
            "unmatched": [], "screenshot": None, "submitted": True, "mismatches": [],
        }
        return {**log, **overrides}

    def test_clean_run_exits_normally(self, env, monkeypatch):
        monkeypatch.setattr(bot, "submit_tips", lambda *a, **kw: self._log())
        bot.main(self.CONFIG)  # kein SystemExit

    def test_unmatched_raises(self, env, monkeypatch):
        monkeypatch.setattr(bot, "submit_tips", lambda *a, **kw: self._log(unmatched=[("x", "y")]))
        with pytest.raises(SystemExit):
            bot.main(self.CONFIG)

    def test_verification_mismatch_raises(self, env, monkeypatch):
        monkeypatch.setattr(bot, "submit_tips", lambda *a, **kw: self._log(mismatches=[("a", "b")]))
        with pytest.raises(SystemExit):
            bot.main(self.CONFIG)

    def test_missing_seal_secret_raises(self, env, monkeypatch):
        monkeypatch.delenv("SEAL_SECRET")
        with pytest.raises(SystemExit):
            bot.main(self.CONFIG)
