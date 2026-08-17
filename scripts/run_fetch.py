#!/usr/bin/env python3
"""Wöchentlicher automatisierter Kursabruf (Standardweg, für GitHub Actions).

Nutzt die austauschbare ``YfinanceStooqSource`` (yfinance primär, Stooq als
Fallback) und schreibt das Ergebnis über ``history_store.record_week`` in
``data/price_history.csv``. Soll der Kursabruf stattdessen manuell/über
Cowork laufen, wird dieses Script einfach nicht aufgerufen - siehe
``record_prices.py`` für den alternativen Weg mit identischem Ergebnisformat.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

import _bootstrap  # noqa: F401

from boersenspiel.history_store import record_week
from boersenspiel.instruments import TICKERS
from boersenspiel.sources.yfinance_stooq import YfinanceStooqSource


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date.today(),
        help="Datum, dem die abgerufenen Kurse zugeordnet werden (Default: heute)",
    )
    args = parser.parse_args()

    source = YfinanceStooqSource()
    quotes = source.fetch(TICKERS, args.date)

    missing = [t for t, q in quotes.items() if q.status != "ok"]
    if missing:
        print(f"WARNUNG: Kein aktueller Kurs gefunden fuer: {', '.join(missing)}", file=sys.stderr)

    row = record_week(args.date, quotes)
    print(f"Kurshistorie aktualisiert fuer {row.date.isoformat()}: {row.prices}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
