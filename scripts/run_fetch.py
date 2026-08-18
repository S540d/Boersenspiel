#!/usr/bin/env python3
"""Wöchentlicher automatisierter Kursabruf (Standardweg, für GitHub Actions).

Nutzt die austauschbare ``AlphaVantageSource`` (offizielle, API-Key-basierte
REST-API - zuverlässiger als das zuvor genutzte yfinance/Stooq-Scraping, das
an Yahoos Crumb/Cookie-Authentifizierung scheiterte) und schreibt das Ergebnis
über ``history_store.record_week`` in ``data/price_history.csv``. Benötigt die
Umgebungsvariable ``ALPHAVANTAGE_API_KEY`` (als GitHub-Actions-Secret
hinterlegt). Soll der Kursabruf stattdessen manuell/über Cowork laufen, wird
dieses Script einfach nicht aufgerufen - siehe ``record_prices.py`` für den
alternativen Weg mit identischem Ergebnisformat.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

import _bootstrap  # noqa: F401

from boersenspiel.history_store import record_week, row_date_from_quotes
from boersenspiel.instruments import TICKERS
from boersenspiel.sources.alphavantage import AlphaVantageSource


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date.today(),
        help=(
            "Abrufdatum (Default: heute). Dient nur als Rueckfallwert - "
            "einsortiert werden die Kurse ueber den von der Quelle gemeldeten "
            "Handelstag, siehe --ignore-handelstag"
        ),
    )
    parser.add_argument(
        "--ignore-handelstag",
        action="store_true",
        help="Kurse stur unter --date ablegen, statt unter ihrem Handelstag",
    )
    args = parser.parse_args()

    source = AlphaVantageSource()
    quotes = source.fetch(TICKERS, args.date)

    missing = [t for t, q in quotes.items() if q.status != "ok"]
    if missing:
        print(f"WARNUNG: Kein aktueller Kurs gefunden fuer: {', '.join(missing)}", file=sys.stderr)

    # Ein von Alpha Vantage erreichtes Tageslimit liefert HTTP 200 mit
    # "Note"/"Information" statt Kursdaten und sieht damit fuer jeden Ticker
    # wie eine ganz normale Kurslücke aus (siehe AlphaVantageSource, Status
    # "rate_limited"). Schlaegt der Abruf fuer ALLE Ticker fehl, ist das kein
    # plausibler gleichzeitiger Ausfall aller Instrumente, sondern fast immer
    # genau dieser Fall - abbrechen, BEVOR record_week() eine Carry-Forward-
    # Zeile schreibt, die wie ein echtes Update aussaehe.
    if missing and len(missing) == len(quotes):
        print(
            "FEHLER: Kursabruf fuer ALLE Ticker fehlgeschlagen (vermutlich Rate-Limit "
            "der Quelle) - breche ab, ohne die Kurshistorie zu veraendern.",
            file=sys.stderr,
        )
        return 1

    # Ein Montagslauf vor Boersenbeginn liefert den Freitagsschluss der
    # Vorwoche - unter dem Montag abgelegt landete der Kurs in der falschen
    # ISO-Woche und damit eine Woche versetzt gegenueber dem Backfill.
    as_of = args.date if args.ignore_handelstag else row_date_from_quotes(quotes, args.date)
    if as_of != args.date:
        print(f"Kurse beziehen sich auf den Handelstag {as_of.isoformat()} (Abruf: {args.date.isoformat()})")

    row = record_week(as_of, quotes)
    print(f"Kurshistorie aktualisiert fuer {row.date.isoformat()}: {row.prices}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
