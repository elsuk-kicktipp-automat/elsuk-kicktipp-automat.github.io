"""Kommandozeile des Automaten.

    python -m engine.cli predict    Tipps für die nächste anstehende Runde
    python -m engine.cli kicktipp   Tipps bei Kicktipp eintragen (Dry-Run per config.yaml)
    python -m engine.cli seal       neue Tipps versiegeln (Hash öffentlich)
    python -m engine.cli unseal     Tipps nach Anstoß entsiegeln
    python -m engine.cli evaluate   Punkteabrechnung gegen die realen Ergebnisse
    python -m engine.cli backtest   Backtests (--mode club | national | all)
"""

import argparse

from . import backtest, evaluate, kicktipp_bot, learn, predict, seal
from .config import load_config


def main():
    parser = argparse.ArgumentParser(prog="engine", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("predict", help="Tipps für die nächste anstehende Runde berechnen")
    sub.add_parser("kicktipp", help="Tipps bei Kicktipp eintragen (Dry-Run per config.yaml)")
    sub.add_parser("seal", help="neue Tipps versiegeln (nur Hash wird öffentlich)")
    sub.add_parser("unseal", help="Tipps nach Anstoß entsiegeln")
    sub.add_parser("evaluate", help="enthüllte Tipps gegen reale Ergebnisse abrechnen")
    sub.add_parser("learn", help="Lernzustand aktualisieren (Vertrauensregler, Quotengewicht)")
    bt = sub.add_parser("backtest", help="Backtests ausführen")
    bt.add_argument("--mode", choices=["club", "national", "all"], default="all")

    args = parser.parse_args()
    config = load_config()

    if args.command == "predict":
        predict.main(config)
    elif args.command == "kicktipp":
        kicktipp_bot.main(config)
    elif args.command == "seal":
        seal.main_seal()
    elif args.command == "unseal":
        seal.main_unseal()
    elif args.command == "evaluate":
        evaluate.main(config)
    elif args.command == "learn":
        learn.main(config)
    else:
        backtest.main(config, args.mode)


if __name__ == "__main__":
    main()
