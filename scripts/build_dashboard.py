#!/usr/bin/env python3
"""Baut das statische Dashboard (docs/index.html) aus der Kurshistorie neu."""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from boersenspiel.dashboard import DEFAULT_OUTPUT, build_dashboard
from boersenspiel.history_store import read_fetch_log, read_price_history
from boersenspiel.scenarios import SCENARIOS, SCENARIOS_BY_NAME
from boersenspiel.strategies import STRATEGIES, STRATEGIES_BY_NAME

ALL_STRATEGIES = STRATEGIES + SCENARIOS
ALL_STRATEGIES_BY_NAME = {**STRATEGIES_BY_NAME, **SCENARIOS_BY_NAME}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy", help="Nur diese Strategie/dieses Szenario rendern (Name aus strategies.py/scenarios.py)"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Zielpfad der HTML-Datei")
    args = parser.parse_args()

    price_history = read_price_history()
    if not price_history:
        raise SystemExit("Kurshistorie ist leer - noch kein Dashboard erzeugbar. Erst Kurse abrufen.")

    strategies = ALL_STRATEGIES
    if args.strategy:
        if args.strategy not in ALL_STRATEGIES_BY_NAME:
            raise SystemExit(f"Unbekannte Strategie: {args.strategy!r}. Verfuegbar: {list(ALL_STRATEGIES_BY_NAME)}")
        strategies = [ALL_STRATEGIES_BY_NAME[args.strategy]]

    output_path = build_dashboard(price_history, strategies, args.output, fetch_log=read_fetch_log())
    print(f"Dashboard erzeugt: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
