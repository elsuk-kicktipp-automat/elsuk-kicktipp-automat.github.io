"""Die Versionshistorie (data/versions.json) muss lückenlos sein und zum Code passen."""

from datetime import datetime

from engine.predict import MODEL_VERSION
from engine.versions import current_version, load_versions


def test_versions_are_numbered_without_gaps():
    assert [v["version"] for v in load_versions()] == list(range(1, len(load_versions()) + 1))


def test_versions_are_in_chronological_order():
    since = [datetime.strptime(v["since_utc"], "%Y-%m-%dT%H:%M:%SZ") for v in load_versions()]
    assert since == sorted(since)
    assert len(set(since)) == len(since)


def test_every_version_says_what_changed():
    for v in load_versions():
        assert v["title"].strip()
        assert v["changes"] and all(c.strip() for c in v["changes"])


def test_latest_version_matches_the_model_in_code():
    # Wer MODEL_VERSION in engine/predict.py ändert, muss die Änderung auch in
    # data/versions.json eintragen - sonst läuft der Code der Historie davon
    assert load_versions()[-1]["model_version"] == MODEL_VERSION


def test_current_version_is_the_last_entry():
    assert current_version() == load_versions()[-1]["version"]
