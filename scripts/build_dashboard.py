#!/usr/bin/env python3
"""Baut das statische Dashboard (docs/index.html) aus der Kurshistorie neu."""

from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from boersenspiel.dashboard import DEFAULT_OUTPUT, build_dashboard
from boersenspiel.history_store import read_price_history
from boersenspiel.strategies import STRATEGIES, STRATEGIES_BY_NAME


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", help="Nur diese Strategie rendern (Name aus strategies.py)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Zielpfad der HTML-Datei")
    args = parser.parse_args()

    price_history = read_price_history()
    if not price_history:
        raise SystemExit("Kurshistorie ist leer - noch kein Dashboard erzeugbar. Erst Kurse abrufen.")

    strategies = STRATEGIES
    if args.strategy:
        if args.strategy not in STRATEGIES_BY_NAME:
            raise SystemExit(f"Unbekannte Strategie: {args.strategy!r}. Verfuegbar: {list(STRATEGIES_BY_NAME)}")
        strategies = [STRATEGIES_BY_NAME[args.strategy]]

    output_path = build_dashboard(price_history, strategies, args.output)
    print(f"Dashboard erzeugt: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
