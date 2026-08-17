#!/usr/bin/env python3
"""Manueller Andockpunkt für Kurse aus einer anderen Quelle (z. B. Cowork/Websuche).

Nimmt Kurse entgegen (als JSON-Objekt Ticker->Preis) und schreibt sie über
dieselbe ``history_store.record_week``-Funktion wie der automatisierte
GitHub-Actions-Kursabruf (``run_fetch.py``). Damit ist das Ergebnis
(Zeilenformat, Wochen-Idempotenz, carry-forward-Vermerk) identisch, egal
woher die Kurse kommen - kein Symbol-Mapping pro Ticker nötig, wenn diese
Quelle genutzt wird (z. B. weil ein Agent die Kurse per Websuche ermittelt
hat statt über yfinance/Stooq).

Beispiel:
    python scripts/record_prices.py --date 2026-08-17 \\
        --prices '{"EUNL": 82.10, "EUNA": 4.95, "4GLD": 61.30, \\
                   "LYMS": 21.40, "SEMI": 47.80, "EIMI": 29.10, "BTC-EUR": 58000}'

Für Ticker, die im JSON fehlen, wird automatisch der letzte bekannte Kurs
übernommen (carry forward) und in fetch_log.csv vermerkt.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime

import _bootstrap  # noqa: F401

from boersenspiel.history_store import record_week
from boersenspiel.sources import PriceQuote


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date.today(),
        help="Datum, dem die Kurse zugeordnet werden (Default: heute)",
    )
    parser.add_argument(
        "--prices",
        required=True,
        help='JSON-Objekt Ticker->Preis, z. B. \'{"EUNL": 82.1, "BTC-EUR": 58000}\'',
    )
    args = parser.parse_args()

    raw_prices: dict[str, float] = json.loads(args.prices)
    quotes = {
        ticker: PriceQuote(ticker=ticker, price=float(price), status="ok", source="manual")
        for ticker, price in raw_prices.items()
    }

    row = record_week(args.date, quotes)
    print(f"Kurshistorie aktualisiert fuer {row.date.isoformat()}: {row.prices}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
