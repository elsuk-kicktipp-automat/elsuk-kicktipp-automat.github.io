from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPIELTAG = ROOT / ".github" / "workflows" / "spieltag.yml"
UNSEAL = ROOT / ".github" / "workflows" / "unseal.yml"


def test_production_timing_is_external_only():
    for path in (SPIELTAG, UNSEAL):
        text = path.read_text(encoding="utf-8")
        assert "workflow_dispatch:" in text
        assert "\n  schedule:" not in text


def test_delivery_receipt_and_audit_run_after_kicktipp_submission():
    text = SPIELTAG.read_text(encoding="utf-8")
    submission = text.index("Kicktipp-Tippabgabe")
    receipt = text.index("Bestätigte Kicktipp-Abgabe committen")
    audit = text.index("Abgabe- und Deadline-Audit")
    assert submission < receipt < audit
    assert "python -m engine.cli audit" in text


def test_data_workflows_have_timeouts_and_shared_lock():
    for path in (SPIELTAG, UNSEAL):
        text = path.read_text(encoding="utf-8")
        assert "group: data-updates" in text
        assert "timeout-minutes:" in text
