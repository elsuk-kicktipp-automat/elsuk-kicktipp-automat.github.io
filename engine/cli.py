"""Kommandozeile des Automaten.

    python -m engine.cli predict    Tipps für die nächste anstehende Runde
    python -m engine.cli evaluate   Punkteabrechnung gegen die realen Ergebnisse
    python -m engine.cli backtest   Backtests (--mode club | national | all)
"""

import argparse

from . import backtest, evaluate, predict
from .config import load_config


def main():
    parser = argparse.ArgumentParser(prog="engine", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("predict", help="Tipps für die nächste anstehende Runde berechnen")
    sub.add_parser("evaluate", help="Tipps gegen reale Ergebnisse abrechnen")
    bt = sub.add_parser("backtest", help="Backtests ausführen")
    bt.add_argument("--mode", choices=["club", "national", "all"], default="all")

    args = parser.parse_args()
    config = load_config()

    if args.command == "predict":
        predict.main(config)
    elif args.command == "evaluate":
        evaluate.main(config)
    else:
        backtest.main(config, args.mode)


if __name__ == "__main__":
    main()
