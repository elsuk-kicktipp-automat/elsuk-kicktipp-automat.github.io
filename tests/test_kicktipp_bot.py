import pytest

from engine.kicktipp_bot import LOGIN_URL, _is_logged_in, login


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
