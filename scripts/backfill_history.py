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
nicht pro Skriptlauf). BTC-EUR laeuft dabei ueber denselben einen FX_WEEKLY-
Request wie die USD-Einzelaktien (kein zusaetzlicher Request) - siehe
``fetch_crypto_weekly_history`` in ``sources/alphavantage.py`` (Issue #56:
``DIGITAL_CURRENCY_WEEKLY`` liefert fuer ``market=EUR`` im Free-Tier nur ca.
50 statt der vollen Historie, fuer ``market=USD`` dagegen die komplette
verfuegbare Historie zurueck).

``--years`` ist nur eine untere Schranke, die an die Source durchgereicht wird
(``_parse_weekly_close_series``/``fetch_crypto_weekly_history`` filtern die von
Alpha Vantage gelieferte Zeitreihe auf Kurse ab diesem Datum) - ein Wert, der
weiter zurueckliegt als die tatsaechlich verfuegbare Historie eines Tickers,
liefert einfach dessen gesamte verfuegbare Historie statt eines Fehlers. Der
Default zielt deshalb bewusst auf "so weit wie moeglich" statt auf einen
Zeitraum, der zur juengsten Position (Rivian, IPO November 2021) passt -
aeltere Instrumente (ETFs, Einzelaktien wie Coca-Cola/Roche) haben oft
15-20+ Jahre Historie bei Alpha Vantage. Fuer Ticker ohne Kurs in einer frueh
liegenden Woche traegt ``history_store.record_week`` ohnehin "missing" statt
eines erfundenen Werts ein.

Handgepflegte Ergaenzungen (#63): Der Lauf setzt ``price_history.csv``
komplett zurueck - manuell nachgetragene Kurse waeren damit weg. Deshalb
gibt es zwei Dateien, die NUR gelesen und von keinem Skript geschrieben
werden, und deren Inhalt bei jedem Lauf neu eingemischt wird:

  data/manual_fx_usd_eur.csv   Date,EUR_pro_USD
  data/manual_prices.csv       Date,Ticker,Preis_EUR

Handgepflegte Werte gewinnen gegen die API, verglichen auf ISO-Wochen-Ebene;
der Lauf gibt je Datei aus, wie viele Wochen ergaenzt und wie viele ersetzt
wurden. Der wirksamste Hebel ist die FX-Datei: ein gepflegter EUR/USD-Kurs
macht die Fruehphase aller neun USD-Ticker UND von BTC-EUR umrechenbar,
waehrend ``manual_prices.csv`` nur fuer Kurse gedacht ist, die Alpha Vantage
ueberhaupt nicht liefert.

Nutzung:
    python scripts/backfill_history.py --years 20
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

# Handgepflegte Ergaenzungsdateien (#63). Werden von KEINEM Skript geschrieben -
# nur gelesen. Damit ueberlebt manuell recherchiertes Material jeden weiteren
# Backfill-Lauf, der price_history.csv ansonsten komplett neu aufbaut.
MANUAL_PRICES_FILE = "manual_prices.csv"
MANUAL_FX_FILE = "manual_fx_usd_eur.csv"


def _iso_week(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return iso[0], iso[1]


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Liest eine Ergaenzungsdatei und ueberspringt Kommentarzeilen (``#``),
    damit die Dateien sich selbst dokumentieren koennen. Fehlt die Datei,
    gibt es schlicht nichts zu ergaenzen."""
    if not path.exists():
        return []
    with path.open(newline="") as f:
        zeilen = [z for z in f if not z.lstrip().startswith("#")]
    return list(csv.DictReader(zeilen))


def read_manual_fx(data_dir: Path) -> dict[date, float]:
    """Handgepflegte EUR/USD-Wochenkurse aus ``data/manual_fx_usd_eur.csv``.

    Spalten: ``Date,EUR_pro_USD``. Deckt den Zeitraum ab, den Alpha Vantages
    ``FX_WEEKLY`` nicht liefert (vor November 2014, siehe #61). Ein einziger
    gepflegter Wechselkurs macht dort die Umrechnung aller neun USD-Ticker
    UND von BTC-EUR moeglich - deshalb ist das der wirksamste Ort fuer
    Handarbeit, nicht die Kurse selbst.
    """
    rates: dict[date, float] = {}
    for row in _read_csv_rows(data_dir / MANUAL_FX_FILE):
        datum = (row.get("Date") or "").strip()
        kurs = (row.get("EUR_pro_USD") or "").strip()
        if not datum or not kurs:
            continue
        rates[date.fromisoformat(datum)] = float(kurs)
    return rates


def read_manual_prices(data_dir: Path) -> dict[str, dict[date, float]]:
    """Handgepflegte Wochenkurse aus ``data/manual_prices.csv``.

    Spalten: ``Date,Ticker,Preis_EUR`` (Langformat - deutlich leichter von
    Hand zu pflegen als eine 18-spaltige Matrix, und ohne Kopplung an die
    Spaltenreihenfolge von ``price_history.csv``).

    **Die Werte sind IMMER schon in EUR.** Sie werden erst nach der
    Waehrungsumrechnung eingemischt und deshalb nicht noch einmal
    umgerechnet - anders als die von Alpha Vantage gelieferten USD-Kurse.
    """
    per_ticker: dict[str, dict[date, float]] = {}
    for row in _read_csv_rows(data_dir / MANUAL_PRICES_FILE):
        datum = (row.get("Date") or "").strip()
        ticker = (row.get("Ticker") or "").strip()
        preis = (row.get("Preis_EUR") or "").strip()
        if not datum or not ticker or not preis:
            continue
        if ticker not in TICKERS:
            raise ValueError(
                f"{MANUAL_PRICES_FILE}: unbekannter Ticker {ticker!r} "
                f"(erlaubt sind: {', '.join(TICKERS)})"
            )
        per_ticker.setdefault(ticker, {})[date.fromisoformat(datum)] = float(preis)
    return per_ticker


def _ueberschreibe_iso_woche(
    basis: dict[date, float], ergaenzung: dict[date, float]
) -> tuple[dict[date, float], int, int]:
    """Mischt ``ergaenzung`` in ``basis`` - handgepflegte Werte gewinnen.

    Verglichen wird auf ISO-WOCHEN-Ebene, nicht auf Datumsebene: der Backfill
    gruppiert spaeter ohnehin nach Kalenderwoche, und ein manueller Eintrag
    vom Freitag wuerde sonst neben einem API-Wert vom Donnerstag derselben
    Woche stehen bleiben - welcher davon in der Zeile landet, waere dann
    Zufall der Iterationsreihenfolge.

    Gibt zusaetzlich zurueck, wie viele Wochen ersetzt und wie viele neu
    gefuellt wurden, damit der Lauf das sichtbar machen kann.
    """
    if not ergaenzung:
        return dict(basis), 0, 0
    manuelle_wochen = {_iso_week(d) for d in ergaenzung}
    ersetzt = sum(1 for d in basis if _iso_week(d) in manuelle_wochen)
    zusammen = {d: p for d, p in basis.items() if _iso_week(d) not in manuelle_wochen}
    zusammen.update(ergaenzung)
    return zusammen, ersetzt, len(ergaenzung) - ersetzt


def _reset_data_files(data_dir: Path) -> None:
    """Setzt price_history.csv/fetch_log.csv auf den leeren Header zurueck -
    der Backfill baut die komplette Historie neu aus Alpha-Vantage-Daten auf."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "price_history.csv").open("w", newline="") as f:
        csv.writer(f).writerow(PRICE_HISTORY_HEADER)
    with (data_dir / "fetch_log.csv").open("w", newline="") as f:
        csv.writer(f).writerow(FETCH_LOG_HEADER)


def _nearest_fx_rate(rates: dict[date, float], sorted_dates: list[date], target: date) -> float | None:
    """Naechstgelegener FX-Kurs an oder VOR ``target`` (Forward-Fill der letzten
    bekannten Woche), sonst ``None``.

    Bewusst KEINE Rueckwaerts-Extrapolation (#61). Vorher fiel diese Funktion
    fuer Wochen vor Beginn der FX-Reihe auf ``rates[sorted_dates[0]]`` zurueck,
    also auf den aeltesten VERFUEGBAREN Kurs - der aber juenger ist als das
    umzurechnende Datum. Alpha Vantages ``FX_WEEKLY`` liefert USD/EUR erst ab
    November 2014; dadurch wurden im 20-Jahres-Backfill 227 Wochen (Juli 2010
    bis November 2014) aller USD-Ticker UND die komplette fruehe BTC-Historie
    mit dem konstanten Kurs von 2014 (0,7982 EUR/USD) umgerechnet. Die
    Wechselkursbewegung dieser Jahre fehlte damit vollstaendig, und die
    Umrechnung war je nach Woche um bis zu ~25% falsch.

    ``None`` heisst "kein Kurs fuer diese Woche": ``record_week()`` traegt dann
    "missing" ein statt eines falsch umgerechneten Werts - dieselbe Regel, der
    ``AlphaVantageSource.fetch()`` beim Live-Abruf schon folgt.
    """
    if not sorted_dates:
        return None
    candidates = [d for d in sorted_dates if d <= target]
    return rates[candidates[-1]] if candidates else None


def collect_weekly_series(
    source: AlphaVantageSource,
    tickers: list[str],
    since: date,
    manual_fx: dict[date, float] | None = None,
    manual_prices: dict[str, dict[date, float]] | None = None,
) -> dict[str, dict[date, float]]:
    """Holt die woechentliche Kurshistorie (in EUR) fuer alle ``tickers`` ab
    ``since``. Reine Datenbeschaffung + Waehrungsumrechnung, keine
    Dateizugriffe - macht die Logik unabhaengig testbar von echten
    Netzwerkaufrufen und von ``record_week``. Die beiden Ergaenzungs-Dicts
    kommen deshalb als Parameter herein (gelesen wird in ``main()`` ueber
    ``read_manual_fx`` / ``read_manual_prices``).

    Reihenfolge der Ergaenzungen ist bedeutsam (#63):

    1. ``manual_fx`` wird VOR der Umrechnung eingemischt - damit werden die
       von Alpha Vantage gelieferten USD-Kurse der Fruehphase ueberhaupt erst
       umrechenbar.
    2. ``manual_prices`` wird ganz zum Schluss eingemischt und ist deshalb
       bereits in EUR anzugeben - diese Werte werden NICHT mehr umgerechnet.
    """
    per_ticker: dict[str, dict[date, float]] = {}
    non_crypto = [t for t in tickers if t != "BTC-EUR"]
    usd_tickers_present = [t for t in non_crypto if t in USD_TICKERS]
    crypto_present = "BTC-EUR" in tickers
    # BTC-EUR wird ueber DIGITAL_CURRENCY_WEEKLY(market=USD) + FX_WEEKLY
    # umgerechnet statt direkt ueber market=EUR abgerufen - Alpha Vantage
    # liefert fuer den EUR-Markt im Free-Tier nur ca. 50 statt der vollen
    # Historie (siehe Issue #56 / AlphaVantageSource.fetch_crypto_weekly_history).
    # Es braucht deshalb denselben FX-Kurs wie die "echten" USD-Ticker.
    fx_needed = bool(usd_tickers_present) or crypto_present

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
    if fx_needed:
        pace()
        print("  EUR/USD-Historie (FX_WEEKLY) ...", file=sys.stderr)
        fx_rates = source.fetch_fx_weekly_eur_per_usd(since)
    if manual_fx:
        fx_rates, ersetzt, gefuellt = _ueberschreibe_iso_woche(fx_rates, manual_fx)
        print(
            f"  {MANUAL_FX_FILE}: {gefuellt} Wochen ergaenzt, {ersetzt} ersetzt",
            file=sys.stderr,
        )
    fx_dates_sorted = sorted(fx_rates)
    if fx_dates_sorted and fx_dates_sorted[0] > since:
        # Sichtbar machen, dass die FX-Reihe kuerzer ist als der angefragte
        # Zeitraum - alles davor bleibt fuer USD-Ticker und BTC leer (#61).
        print(
            f"  WARNUNG: FX_WEEKLY (USD/EUR) beginnt erst am {fx_dates_sorted[0]}, "
            f"angefragt ab {since}. Fuer alle Wochen davor bleiben die USD-Ticker "
            f"{sorted(usd_tickers_present)}"
            f"{' und BTC-EUR' if crypto_present else ''} ohne Kurs, statt mit einem "
            "spaeteren Wechselkurs falsch umgerechnet zu werden.",
            file=sys.stderr,
        )

    for ticker in non_crypto:
        pace()
        print(f"  {ticker} ...", file=sys.stderr)
        per_ticker[ticker] = source.fetch_weekly_history(ticker, since)

    if usd_tickers_present:
        for ticker in usd_tickers_present:
            per_ticker[ticker] = {
                d: usd_price * rate
                for d, usd_price in per_ticker[ticker].items()
                if (rate := _nearest_fx_rate(fx_rates, fx_dates_sorted, d)) is not None
            }

    if crypto_present:
        pace()
        print("  BTC-EUR (via BTC-USD + FX_WEEKLY) ...", file=sys.stderr)
        btc_usd = source.fetch_crypto_weekly_history(since)
        per_ticker["BTC-EUR"] = {
            d: usd_price * rate
            for d, usd_price in btc_usd.items()
            if (rate := _nearest_fx_rate(fx_rates, fx_dates_sorted, d)) is not None
        }

    # Handgepflegte Kurse ganz zum Schluss: sie sind bereits in EUR und duerfen
    # deshalb nicht mehr durch die Waehrungsumrechnung laufen (#63).
    for ticker, series in (manual_prices or {}).items():
        if ticker not in tickers:
            continue
        per_ticker[ticker], ersetzt, gefuellt = _ueberschreibe_iso_woche(
            per_ticker.get(ticker, {}), series
        )
        print(
            f"  {MANUAL_PRICES_FILE}: {ticker} - {gefuellt} Wochen ergaenzt, {ersetzt} ersetzt",
            file=sys.stderr,
        )

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
    parser.add_argument(
        "--years",
        type=int,
        default=20,
        help="Wie viele Jahre historischer Daten, nur untere Schranke (Default: 20 = so weit wie verfuegbar)",
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Zielverzeichnis (Default: data/)")
    args = parser.parse_args()

    since = date.today() - timedelta(days=365 * args.years)
    source = AlphaVantageSource()

    manual_fx = read_manual_fx(args.data_dir)
    manual_prices = read_manual_prices(args.data_dir)

    print(f"Backfill ab {since.isoformat()} fuer {len(TICKERS)} Ticker ...", file=sys.stderr)
    per_ticker = collect_weekly_series(source, TICKERS, since, manual_fx, manual_prices)
    week_count = write_backfilled_history(per_ticker, args.data_dir)

    print(f"Fertig: {week_count} Wochen zurueckgeschrieben nach {args.data_dir / 'price_history.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
