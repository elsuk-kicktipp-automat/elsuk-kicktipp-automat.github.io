"""Kicktipp-Tippabgabe per Playwright (concept.md Phase 4).

Login: https://www.kicktipp.de/info/profil/login/ (Felder #kennung/#passwort).
Tippabgabe: https://www.kicktipp.de/{runde}/tippabgabe - Spiele stehen in
#tippabgabeSpiele, echte Spielzeilen tragen die Klasse "datarow" (Klasse
"rowheader" ist nur eine Datums-Zwischenüberschrift). Die Tor-Eingabefelder
heißen nicht exakt "heimTipp"/"gastTipp" (der volle name-Wert trägt ein
zeilenspezifisches Präfix), deshalb per Substring gesucht
(input[name*="heimTipp"]). Bereits beendete/laufende Spiele haben keine
Eingabefelder mehr - solche Zeilen werden übersprungen.

Dry-Run (config.yaml: kicktipp_submission.dry_run): füllt alle Felder ganz
normal aus und macht einen Screenshot, klickt aber NICHT auf "Tipps
speichern" - Zuordnung und Formularstruktur lassen sich damit gefahrlos
prüfen, bevor scharf geschaltet wird. Der Screenshot zeigt die noch nicht
versiegelten Klartext-Tipps und bleibt deshalb lokal (gitignored) - nie als
GitHub-Actions-Artefakt hochladen, das Repo ist öffentlich.
"""

import json
import os
from datetime import datetime, timezone

from .config import PREDICTIONS_DIR, PROJECT_ROOT, load_dotenv
from .teams import normalize

LOGIN_URL = "https://www.kicktipp.de/info/profil/login/"
STATE_FILE = PROJECT_ROOT / "data" / "cache" / "kicktipp_state.json"
SCREENSHOT_DIR = PROJECT_ROOT / "data" / "kicktipp_dryrun"


def _accept_consent(page) -> None:
    """Bestbemüht: Kicktipp zeigt teils ein Cookie-Overlay, in zwei bekannten
    Varianten (iframe-CMP oder direkter Button) - beide versuchen, keine darf
    den Ablauf blockieren, falls sie (schon akzeptiert) gar nicht erscheint."""
    try:
        page.frame_locator('iframe[id*="sp_message_iframe"]').get_by_text(
            "Akzeptieren"
        ).click(timeout=4000)
        return
    except Exception:
        pass
    try:
        page.get_by_role("button", name="ZUSTIMMEN").click(timeout=4000)
    except Exception:
        pass


def _is_logged_in(page) -> bool:
    """Robuster als ein URL-Vergleich: Kicktipp zeigt das Login-Formular auch
    eingebettet in die Runden-Navigation (nicht nur unter LOGIN_URL selbst) -
    entscheidend ist, ob das E-Mail-Feld auf der aktuellen Seite existiert."""
    return page.locator("#kennung").count() == 0


def login(page, email: str, password: str) -> None:
    """Meldet an; wirft RuntimeError, wenn danach immer noch die Login-Seite steht."""
    page.goto(LOGIN_URL)
    _accept_consent(page)
    if _is_logged_in(page):
        return
    page.fill("#kennung", email)
    page.fill("#passwort", password)
    page.get_by_role("button", name="Anmelden").click()
    page.wait_for_load_state("networkidle")
    if not _is_logged_in(page):
        raise RuntimeError(
            "Kicktipp-Login fehlgeschlagen (falsche Zugangsdaten oder Login-Seite geändert?)"
        )


def submit_tips(
    email: str,
    password: str,
    runde: str,
    predictions: dict[tuple[str, str], tuple[int, int]],
    dry_run: bool = True,
    headless: bool = True,
    overwrite: bool = False,
) -> dict:
    """Trägt Tipps bei Kicktipp ein.

    predictions: {(normalisierter Heim-Key, normalisierter Auswärts-Key): (heim, gast)}.
    Gibt ein Log-Dict zurück (gefüllte/übersprungene Paarungen, Screenshot-Pfad).
    Enthält absichtlich keine Tipp-Werte in print()-Ausgaben - dieser Code
    läuft auch in GitHub Actions, dessen Logs bei einem öffentlichen Repo für
    jeden einsehbar sind.
    """
    from playwright.sync_api import sync_playwright  # lazy: nur nötig, wenn wirklich aufgerufen

    log = {
        "filled": [],
        "skipped_already_tipped": [],
        "skipped_no_input": [],
        "unmatched": [],
        "screenshot": None,
        "submitted": False,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = (
            browser.new_context(storage_state=str(STATE_FILE))
            if STATE_FILE.exists()
            else browser.new_context()
        )
        page = context.new_page()

        tippabgabe_url = f"https://www.kicktipp.de/{runde}/tippabgabe"
        page.goto(tippabgabe_url)
        if not _is_logged_in(page):
            login(page, email, password)
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(STATE_FILE))
            page.goto(tippabgabe_url)
        _accept_consent(page)

        remaining = dict(predictions)
        for row in page.locator("#tippabgabeSpiele tbody tr.datarow").all():
            home_el = row.locator(".col1").first
            away_el = row.locator(".col2").first
            if home_el.count() == 0 or away_el.count() == 0:
                continue
            pairing = (normalize(home_el.inner_text()), normalize(away_el.inner_text()))
            if pairing not in remaining:
                continue

            home_input = row.locator('input[name*="heimTipp"]')
            away_input = row.locator('input[name*="gastTipp"]')
            if home_input.count() == 0 or away_input.count() == 0:
                log["skipped_no_input"].append(pairing)
                del remaining[pairing]
                continue

            if not overwrite and (home_input.input_value() or away_input.input_value()):
                log["skipped_already_tipped"].append(pairing)
                del remaining[pairing]
                continue

            tip_h, tip_a = remaining.pop(pairing)
            home_input.fill(str(tip_h))
            away_input.fill(str(tip_a))
            log["filled"].append(pairing)

        log["unmatched"] = list(remaining.keys())

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        screenshot_path = SCREENSHOT_DIR / f"{runde}_{stamp}.png"
        page.screenshot(path=str(screenshot_path), full_page=True)
        log["screenshot"] = str(screenshot_path)

        if not dry_run and log["filled"]:
            page.get_by_role("button", name="Tipps speichern").click()
            page.wait_for_load_state("networkidle")
            log["submitted"] = True

        context.close()
        browser.close()

    return log


def main(config: dict) -> None:
    load_dotenv()
    email = os.environ.get("KICKTIPP_EMAIL")
    password = os.environ.get("KICKTIPP_PASSWORD")
    runde = os.environ.get("KICKTIPP_RUNDE")
    cfg = config.get("kicktipp_submission", {})

    if not cfg.get("enabled"):
        print("Kicktipp-Abgabe deaktiviert (config.yaml: kicktipp_submission.enabled).")
        return
    if not (email and password and runde):
        raise SystemExit("KICKTIPP_EMAIL/PASSWORD/RUNDE fehlen (Secrets bzw. lokale .env).")

    predictions = {}
    for f in sorted(PREDICTIONS_DIR.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for m in data["matches"]:
            predictions[(normalize(m["home"]), normalize(m["away"]))] = tuple(m["tip"])

    if not predictions:
        print("Keine offenen Tipps zum Eintragen gefunden.")
        return

    dry_run = cfg.get("dry_run", True)
    log = submit_tips(email, password, runde, predictions, dry_run=dry_run)

    print(
        f"{len(log['filled'])} Tipps ausgefüllt, "
        f"{len(log['skipped_already_tipped'])} bereits vorhandene übersprungen, "
        f"{len(log['skipped_no_input'])} noch nicht tippbar/schon beendet."
    )
    if log["unmatched"]:
        print(f"{len(log['unmatched'])} Paarungen nicht auf der Kicktipp-Seite gefunden (Team-Namen prüfen).")
    if dry_run:
        print("Dry-Run: NICHT abgeschickt. Screenshot liegt lokal (nicht committen/hochladen).")
    elif log["submitted"]:
        print("Tipps live bei Kicktipp eingetragen.")
