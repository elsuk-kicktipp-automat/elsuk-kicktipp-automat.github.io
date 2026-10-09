import pytest


@pytest.fixture(autouse=True)
def no_real_claude_cli(monkeypatch):
    """Kein Test startet die echte Claude-CLI - das kostete Abo-Kontingent.
    Tests, die den Aufruf prüfen, setzen shutil.which selbst."""
    monkeypatch.setattr("engine.llm.shutil.which", lambda name: None)
