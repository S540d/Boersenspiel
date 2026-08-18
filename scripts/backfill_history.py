#!/usr/bin/env python3
"""Einmaliger historischer Backfill von data/price_history.csv via Alpha Vantage.

Ersetzt die bestehende Kurshistorie komplett durch echte historische
Wochenschlusskurse (``TIME_SERIES_WEEKLY`` / ``DIGITAL_CURRENCY_WEEKLY`` -
liefern die komplette verfuegbare Historie in EINEM Request pro Ticker,
anders als das fuer den woechentlichen Live-Abruf genutzte ``GLOBAL_QUOTE``),
statt nur die seit Projektstart live gesammelten paar Wochen zu haben.
USD-notierte Einzelaktien (siehe ``sources.alphavantage.USD_TICKERS``) werden
mit dem jeweils zeitgleichen woechentlichen EUR/USD-Kurs (``FX_WEEKLY``) in
EUR umgerechnet, damit die Historie waehrungskonsistent zum Rest des
Portfolios ist - dieselbe Umrechnung, die auch der laufende Live-Abruf
(``run_fetch.py``) fuer diese Ticker vornimmt.

Verbraucht ca. 18 Requests (16 nicht-Krypto-Ticker + 1x FX_WEEKLY + 1x
Krypto) - passt in das taegliche Alpha-Vantage-Free-Tier-Limit von 25, sollte
aber NICHT mehrfach am selben Tag laufen (das Limit gilt pro Tag und API-Key,
nicht pro Skriptlauf).

Nutzung:
    python scripts/backfill_history.py --years 5
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import _bootstrap  # noqa: F401

from boersenspiel.history_store import DEFAULT_DATA_DIR, FETCH_LOG_HEADER, PRICE_HISTORY_HEADER, record_week
from boersenspiel.instruments import TICKERS
from boersenspiel.sources import PriceQuote
from boersenspiel.sources.alphavantage import USD_TICKERS, AlphaVantageSource

_REQUEST_INTERVAL_SECONDS = 1.1


def _iso_week(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return iso[0], iso[1]


def _reset_data_files(data_dir: Path) -> None:
    """Setzt price_history.csv/fetch_log.csv auf den leeren Header zurueck -
    der Backfill baut die komplette Historie neu aus Alpha-Vantage-Daten auf."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "price_history.csv").open("w", newline="") as f:
        csv.writer(f).writerow(PRICE_HISTORY_HEADER)
    with (data_dir / "fetch_log.csv").open("w", newline="") as f:
        csv.writer(f).writerow(FETCH_LOG_HEADER)


def _nearest_fx_rate(rates: dict[date, float], sorted_dates: list[date], target: date) -> float | None:
    """Naechstgelegener FX-Kurs an oder vor ``target`` (Forward-Fill der
    letzten bekannten Woche), sonst der frueheste verfuegbare Kurs."""
    if not sorted_dates:
        return None
    candidates = [d for d in sorted_dates if d <= target]
    return rates[candidates[-1]] if candidates else rates[sorted_dates[0]]


def collect_weekly_series(
    source: AlphaVantageSource, tickers: list[str], since: date
) -> dict[str, dict[date, float]]:
    """Holt die woechentliche Kurshistorie (in EUR) fuer alle ``tickers`` ab
    ``since``. Reine Datenbeschaffung + Waehrungsumrechnung, keine
    Dateizugriffe - macht die Logik unabhaengig testbar von echten
    Netzwerkaufrufen und von ``record_week``."""
    per_ticker: dict[str, dict[date, float]] = {}
    non_crypto = [t for t in tickers if t != "BTC-EUR"]
    usd_tickers_present = [t for t in non_crypto if t in USD_TICKERS]

    erste_anfrage = True

    def pace() -> None:
        nonlocal erste_anfrage
        if not erste_anfrage:
            time.sleep(_REQUEST_INTERVAL_SECONDS)
        erste_anfrage = False

    # Der FX-Abruf laeuft BEWUSST zuerst: er ist die einzige Anfrage, die alle
    # USD-Ticker gemeinsam braucht, und ein Fehlschlag bricht den ganzen Lauf
    # ab. Am Ende der Reihenfolge kostet dieser Abbruch die 16 bereits
    # verbrauchten Ticker-Requests mit - bei 25 Requests/Tag ist damit auch der
    # zweite Versuch fuer denselben Tag verloren (so geschehen beim ersten
    # Lauf, siehe Issue #6). Vorne kostet derselbe Fehlschlag genau 1 Request.
    fx_rates: dict[date, float] = {}
    if usd_tickers_present:
        pace()
        print("  EUR/USD-Historie (FX_WEEKLY) ...", file=sys.stderr)
        fx_rates = source.fetch_fx_weekly_eur_per_usd(since)

    for ticker in non_crypto:
        pace()
        print(f"  {ticker} ...", file=sys.stderr)
        per_ticker[ticker] = source.fetch_weekly_history(ticker, since)

    if usd_tickers_present:
        fx_dates_sorted = sorted(fx_rates)
        for ticker in usd_tickers_present:
            per_ticker[ticker] = {
                d: usd_price * rate
                for d, usd_price in per_ticker[ticker].items()
                if (rate := _nearest_fx_rate(fx_rates, fx_dates_sorted, d)) is not None
            }

    if "BTC-EUR" in tickers:
        pace()
        print("  BTC-EUR ...", file=sys.stderr)
        per_ticker["BTC-EUR"] = source.fetch_crypto_weekly_history(since)

    return per_ticker


def write_backfilled_history(per_ticker: dict[str, dict[date, float]], data_dir: Path) -> int:
    """Schreibt die gesammelte Kurshistorie ueber ``history_store.record_week``
    (einziger Schreibzugriff auf die CSVs) und liefert die Anzahl geschriebener
    Wochen. Nutzt fuer fehlende Ticker/Wochen exakt denselben Carry-Forward-
    Mechanismus wie der Live-Abruf."""
    by_week: dict[str, dict[tuple[int, int], float]] = {
        ticker: {_iso_week(d): price for d, price in series.items()} for ticker, series in per_ticker.items()
    }

    weeks: dict[tuple[int, int], date] = {}
    for series in per_ticker.values():
        for d in series:
            key = _iso_week(d)
            if key not in weeks or d > weeks[key]:
                weeks[key] = d

    _reset_data_files(data_dir)

    for week_key, week_date in sorted(weeks.items()):
        quotes: dict[str, PriceQuote] = {}
        for ticker in TICKERS:
            price = by_week.get(ticker, {}).get(week_key)
            if price is None:
                quotes[ticker] = PriceQuote(ticker, None, "missing", "alphavantage-backfill")
            else:
                quotes[ticker] = PriceQuote(ticker, price, "ok", "alphavantage-backfill")
        record_week(week_date, quotes, data_dir=data_dir)

    return len(weeks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--years", type=int, default=5, help="Wie viele Jahre historischer Daten (Default: 5)")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Zielverzeichnis (Default: data/)")
    args = parser.parse_args()

    since = date.today() - timedelta(days=365 * args.years)
    source = AlphaVantageSource()

    print(f"Backfill ab {since.isoformat()} fuer {len(TICKERS)} Ticker ...", file=sys.stderr)
    per_ticker = collect_weekly_series(source, TICKERS, since)
    week_count = write_backfilled_history(per_ticker, args.data_dir)

    print(f"Fertig: {week_count} Wochen zurueckgeschrieben nach {args.data_dir / 'price_history.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
